# SPDX-License-Identifier: Apache-2.0
"""Game bridge output: the head pose, shaped by each enabled game's profile, written to one file
per game. The game's own NPClient/FreeTrack DLL (native/) reads that file through Wine's Z: drive.

File layout (64 bytes, little-endian): u32 seq | u32 version=1 | f64 x, y, z (cm) |
f64 yaw, pitch, roll (deg) | f64 t (monotonic s). `seq` is written last, after the data.
"""
import logging
import mmap
import os
import re
import stat
import struct
import threading

from game_profiles import ProfileFilter, normalize_profile

log = logging.getLogger(__name__)

SIZE = 64
VERSION = 1
MAX_DT = 0.1          # a gap (reconnect, suspend) must not throw the smoothing filter


_APPID = re.compile(r"^[0-9]+$")


def private_dir(shm_dir="/dev/shm"):
    """<shm_dir>/mousepods-<uid>, mode 0700 and owned by us; refuses a directory someone else
    planted (shared /dev/shm)."""
    d = os.path.join(shm_dir, f"mousepods-{os.getuid()}")
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass
    st = os.lstat(d)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise PermissionError(f"unsafe pose directory {d}")
    return d


def pose_path(appid, shm_dir="/dev/shm"):
    appid = str(appid)
    if not _APPID.match(appid):
        raise ValueError(f"invalid Steam appid {appid!r}")
    return os.path.join(shm_dir, f"mousepods-{os.getuid()}", f"pose-{appid}")


class PoseFile:
    def __init__(self, path):
        self.path = path
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid():
                raise PermissionError(f"unsafe pose file {path}")
            os.fchmod(fd, 0o600)
            os.ftruncate(fd, SIZE)
            self.mm = mmap.mmap(fd, SIZE)
        finally:
            os.close(fd)
        self.seq = struct.unpack_from("<I", self.mm, 0)[0]
        struct.pack_into("<I", self.mm, 4, VERSION)

    def write(self, pose6, t):
        struct.pack_into("<6dd", self.mm, 8, *pose6, t)
        self.seq = (self.seq + 1) & 0xFFFFFFFF
        struct.pack_into("<I", self.mm, 0, self.seq)

    def close(self, remove):
        self.mm.close()
        if remove:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass


class GameBridge:
    def __init__(self, shm_dir="/dev/shm"):
        self.shm_dir = shm_dir
        self.games = {}        # appid -> (PoseFile, ProfileFilter, normalized profile)
        self.last_now = None
        # configure() runs on the controller thread, tick() on the output thread
        self.lock = threading.Lock()

    @property
    def active(self):
        return bool(self.games)          # a dict truth test is atomic, no lock needed

    def configure(self, games_cfg):
        with self.lock:
            self._configure(games_cfg)

    def _configure(self, games_cfg):
        wanted = {str(appid): normalize_profile((g or {}).get("profile"))
                  for appid, g in (games_cfg or {}).items()
                  if (g or {}).get("enabled") and _APPID.match(str(appid))}
        for appid in list(self.games):
            if appid not in wanted:
                self.games.pop(appid)[0].close(remove=True)
        for appid, profile in wanted.items():
            old = self.games.get(appid)
            if old and old[2] == profile:
                continue
            try:
                if old:
                    pf = old[0]
                else:
                    private_dir(self.shm_dir)
                    pf = PoseFile(pose_path(appid, self.shm_dir))
            except OSError as e:
                log.error("game %s: cannot create pose file: %s", appid, e)
                continue
            self.games[appid] = (pf, ProfileFilter(profile), profile)

    def tick(self, now, pose6):
        with self.lock:
            dt = 0.0 if self.last_now is None else min(max(now - self.last_now, 0.0), MAX_DT)
            self.last_now = now
            for pf, flt, _ in self.games.values():
                pf.write(flt.apply(pose6, dt), now)

    def close(self):
        with self.lock:
            for pf, _, _ in self.games.values():
                pf.close(remove=True)
            self.games = {}
