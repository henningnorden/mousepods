# SPDX-License-Identifier: Apache-2.0
"""Headsets that follow Android's standard head-tracker HID protocol, on Linux through hidraw.

Sony WH-1000XM5 / XM6, WF-1000XM5 / XM6, ULT Wear, Google Pixel Buds Pro / Pro 2 and other headsets made
for Android's spatial audio expose their head tracker as a HID "custom sensor" (usage page 0x20,
usage 0xE1) whose description string starts with "#AndroidHeadTracker#". EXPERIMENTAL: written from
the protocol and from SonyTrackerLinux (MIT, https://github.com/kdani3/SonyTrackerLinux), not yet
tested on hardware by us.

Protocol (Android head tracker HID, as implemented by Sony and Google):
  feature report 1 (write)  01 03           enable: reporting all events, full power
  feature report 2 (read)   "#AndroidHeadTracker#1.0" + UUID
  input report 1            01 rx ry rz gx gy gz n
      rx..rz: rotation vector (axis * angle), int16 LE, ±32767 = ±π rad
      gx..gz: angular velocity, int16 LE, ±32767 = ±32 rad/s
      n:      discontinuity counter (the orientation reference changed)

Permissions: hidraw nodes are root-only by default. Install
packaging/udev/70-mousepods-headtrackers.rules (gives the desktop user access to Bluetooth HID raw
devices), then reconnect the headset.
"""
from __future__ import annotations

import fcntl
import glob
import logging
import math
import os
import select
import struct
import threading
import time

log = logging.getLogger(__name__)

MARKER = b"#AndroidHeadTracker#"
ENABLE = bytes([0x01, 0x03])
ROT_SCALE = math.pi / 32767.0          # rad per count
GYRO_SCALE = 32.0 / 32767.0            # rad/s per count
# Axis map from SonyTrackerLinux (tested on WH-1000XM5): output = sign * input[source],
# giving head axes X = right, Y = forward, Z = up.
AXIS_SOURCE = (1, 0, 2)
AXIS_SIGN = (-1.0, 1.0, -1.0)


# ---------------------------------------------------------------- ioctl numbers (linux/hidraw.h)
def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


HIDIOCGRDESCSIZE = _ioc(2, 0x01, 4)
HIDIOCGRDESC = _ioc(2, 0x02, 4 + 4096)


def HIDIOCGRAWNAME(n):
    return _ioc(2, 0x04, n)


def HIDIOCSFEATURE(n):
    return _ioc(3, 0x06, n)


def HIDIOCGFEATURE(n):
    return _ioc(3, 0x07, n)


# ---------------------------------------------------------------- pure helpers (unit tested)
def is_sensor_custom(desc: bytes) -> bool:
    """Usage page Sensor (0x20), usage Custom (0xE1) at the top of the report descriptor."""
    return desc[:4] == b"\x05\x20\x09\xe1" or (b"\x05\x20" in desc[:16] and b"\x09\xe1" in desc[:16])


def parse_input(report: bytes):
    """Input report 1 -> (rotation vector rad, angular velocity rad/s, discontinuity counter), or None."""
    if len(report) < 14 or report[0] != 0x01:
        return None
    v = struct.unpack_from("<6h", report, 1)
    rot = tuple(x * ROT_SCALE for x in v[:3])
    gyro = tuple(x * GYRO_SCALE for x in v[3:])
    return rot, gyro, report[13]


def remap(v):
    return tuple(AXIS_SIGN[i] * v[AXIS_SOURCE[i]] for i in range(3))


def rotvec_to_quat(v):
    angle = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if angle < 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    s = math.sin(angle / 2) / angle
    return (math.cos(angle / 2), v[0] * s, v[1] * s, v[2] * s)


def qmul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw)


def qconj(q):
    return (q[0], -q[1], -q[2], -q[3])


def rotate(q, v):
    w, x, y, z = q
    p = qmul(qmul(q, (0.0, *v)), (w, -x, -y, -z))
    return p[1:]


def head_angles(q):
    """Relative head quaternion (X right, Y forward, Z up) -> (yaw, pitch, roll) in degrees,
    MousePods convention: +yaw = look right, +pitch = look up, +roll = right ear down."""
    f = rotate(q, (0.0, 1.0, 0.0))
    r = rotate(q, (1.0, 0.0, 0.0))
    yaw = math.degrees(math.atan2(f[0], f[1]))
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, f[2]))))
    roll = math.degrees(math.asin(max(-1.0, min(1.0, -r[2]))))
    return yaw, pitch, roll


class Pose:
    """Rotation vectors in, head angles relative to a reference out; recenter on demand."""

    def __init__(self):
        self.reference = None
        self.counter = None

    def recenter(self):
        self.reference = None

    def update(self, rot, counter=None):
        q = rotvec_to_quat(remap(rot))
        if counter is not None and self.counter is not None and counter != self.counter:
            self.reference = None                      # the headset reset its orientation: start again
        self.counter = counter
        if self.reference is None:
            self.reference = q
        return head_angles(qmul(qconj(self.reference), q))


# ---------------------------------------------------------------- hidraw access
def _descriptor(fd):
    size = struct.unpack("I", fcntl.ioctl(fd, HIDIOCGRDESCSIZE, b"\0" * 4))[0]
    buf = bytearray(struct.pack("I", size) + b"\0" * 4096)
    fcntl.ioctl(fd, HIDIOCGRDESC, buf)
    return bytes(buf[4:4 + size])


def _name(fd):
    buf = bytearray(256)
    try:
        fcntl.ioctl(fd, HIDIOCGRAWNAME(256), buf)
    except OSError:
        return ""
    return buf.split(b"\0", 1)[0].decode(errors="replace")


def _feature_get(fd, report_id, size=64):
    buf = bytearray(size)
    buf[0] = report_id
    fcntl.ioctl(fd, HIDIOCGFEATURE(size), buf)
    return bytes(buf)


def _feature_set(fd, data: bytes):
    buf = bytearray(data)
    fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)


def find_devices(pattern="/dev/hidraw*"):
    """[(path, name)] of head trackers we can open. Unreadable nodes are reported in the log once."""
    found = []
    for path in sorted(glob.glob(pattern)):
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError:
            log.debug("%s: no permission (see packaging/udev/70-mousepods-headtrackers.rules)", path)
            continue
        except OSError:
            continue
        try:
            if is_sensor_custom(_descriptor(fd)) and MARKER in _feature_get(fd, 0x02):
                found.append((path, _name(fd)))
        except OSError:
            pass
        finally:
            os.close(fd)
    return found


class Tracker(threading.Thread):
    """Reads one headset and calls on_pose(yaw, pitch, roll, dt, yaw_rate_deg_s, name)."""

    def __init__(self, path, name, on_pose):
        super().__init__(name=f"headtracker {name}", daemon=True)
        self.path, self.devname, self.on_pose = path, name, on_pose
        self.pose = Pose()
        self.running = True
        self.samples = 0
        self.last = None
        self.error = None

    def recenter(self):
        self.pose.recenter()

    def run(self):
        try:
            fd = os.open(self.path, os.O_RDWR)
        except OSError as e:
            self.error = str(e)
            return
        try:
            _feature_set(fd, ENABLE)
            log.info("Head tracker on: %s (%s)", self.devname, self.path)
            while self.running:
                r, _, _ = select.select([fd], [], [], 1.0)
                if not r:
                    continue
                data = os.read(fd, 64)
                p = parse_input(data)
                if not p:
                    continue
                rot, gyro, counter = p
                now = time.monotonic()
                dt = min(max(now - self.last, 0.001), 0.1) if self.last else 0.02
                self.last = now
                self.samples += 1
                yaw, pitch, roll = self.pose.update(rot, counter)
                yaw_rate = math.degrees(remap(gyro)[2])
                self.on_pose(yaw, pitch, roll, dt, yaw_rate, self.devname)
        except OSError as e:
            self.error = str(e)
            log.info("Head tracker %s stopped: %s", self.devname, e)
        finally:
            os.close(fd)


class Manager(threading.Thread):
    """Looks for head-tracker headsets every few seconds and runs one Tracker per headset."""

    def __init__(self, on_pose, enabled=lambda: True, every_s=5.0, finder=find_devices):
        super().__init__(name="headtracker scan", daemon=True)
        self.on_pose, self.enabled, self.every_s, self.finder = on_pose, enabled, every_s, finder
        self.trackers = {}
        self.running = True

    def status(self):
        return [{"name": t.devname, "path": t.path, "samples": t.samples,
                 "live": bool(t.last and time.monotonic() - t.last < 0.5), "error": t.error}
                for t in self.trackers.values()]

    def recenter(self):
        for t in self.trackers.values():
            t.recenter()

    def scan_once(self):
        for path in [p for p, t in self.trackers.items() if not t.is_alive()]:
            del self.trackers[path]
        if not self.enabled():
            return
        for path, name in self.finder():
            if path not in self.trackers:
                t = Tracker(path, name, self.on_pose)
                self.trackers[path] = t
                t.start()

    def run(self):
        while self.running:
            try:
                self.scan_once()
            except Exception as e:  # noqa: BLE001  (a scan must never take the controller down)
                log.debug("head tracker scan: %s", e)
            time.sleep(self.every_s)
