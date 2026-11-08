# SPDX-License-Identifier: Apache-2.0
"""BlueZ over the system D-Bus (org.bluez), via Gio from PyGObject.

Used instead of the bluetoothctl command line where that is missing (Flatpak sandbox). Only
reads devices and asks BlueZ to connect; the AirPods data channel itself is still the L2CAP
socket in airpods_mouse_controller.
"""
import re

BUS_NAME = "org.bluez"
DEVICE_IFACE = "org.bluez.Device1"
CALL_TIMEOUT_MS = 2000
_APPLE = re.compile(r"^bluetooth:v004Cp([0-9A-Fa-f]{4})")
_state = {"available": None}


def _reset():
    _state["available"] = None


def _gio():
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib
    return Gio, GLib


def managed_objects():
    """org.freedesktop.DBus.ObjectManager.GetManagedObjects on org.bluez, unpacked to dicts."""
    Gio, GLib = _gio()
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    res = bus.call_sync(BUS_NAME, "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
                        GLib.VariantType.new("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, CALL_TIMEOUT_MS, None)
    return res.unpack()[0]


def available():
    """True when PyGObject is importable and BlueZ answers on the system bus (cached)."""
    if _state["available"] is None:
        try:
            managed_objects()
            _state["available"] = True
        except Exception:  # noqa: BLE001  (no gi, no system bus, no bluetoothd: all mean "use bluetoothctl")
            _state["available"] = False
    return _state["available"]


def devices_from_managed_objects(objs):
    """Paired Apple devices: [(address, name, pid_hex_lower, connected)], sorted by address."""
    out = []
    for ifaces in objs.values():
        dev = ifaces.get(DEVICE_IFACE)
        if not dev or not dev.get("Paired"):
            continue
        m = _APPLE.match(dev.get("Modalias", ""))
        if m:
            out.append((dev.get("Address", ""), dev.get("Name") or dev.get("Alias") or "",
                        m.group(1).lower(), bool(dev.get("Connected"))))
    return sorted(out)


def _device(objs, addr):
    for path, ifaces in objs.items():
        dev = ifaces.get(DEVICE_IFACE)
        if dev and dev.get("Address", "").upper() == addr.upper():
            return path, dev
    return None, None


def info_from_managed_objects(objs, addr):
    _, dev = _device(objs, addr)
    if dev is None:
        return None
    m = _APPLE.match(dev.get("Modalias", ""))
    return {"connected": bool(dev.get("Connected")), "pid": m.group(1).lower() if m else "0000"}


def list_apple_devices():
    return devices_from_managed_objects(managed_objects())


def info(addr):
    return info_from_managed_objects(managed_objects(), addr)


def connect(addr, timeout_s=12):
    """Ask BlueZ to connect the device; True if it reports Connected afterwards."""
    Gio, _ = _gio()
    path, _dev = _device(managed_objects(), addr)
    if path is None:
        return False
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    try:
        bus.call_sync(BUS_NAME, path, DEVICE_IFACE, "Connect", None, None, Gio.DBusCallFlags.NONE,
                      int(timeout_s * 1000), None)
    except Exception:  # noqa: BLE001  (GLib.Error: already connected, timeout, refused)
        pass
    got = info(addr)
    return bool(got and got["connected"])
