# SPDX-License-Identifier: Apache-2.0
"""Python mirror of windows/driver/public.h (constants and ctypes structs).

Windows-only lookups (ctypes.windll) must stay inside functions, never at import.
"""
import ctypes

GUID_DEVINTERFACE_MOUSEPODS_L2CAP = "6d0e3c52-4f1b-4c9a-9a51-2f0b8e7d1a01"

PSM_AAP = 0x1001
VERSION = 1

_FILE_DEVICE_UNKNOWN = 0x22


def _ctl_code(function, access):
    # METHOD_BUFFERED is 0
    return (_FILE_DEVICE_UNKNOWN << 16) | (access << 14) | (function << 2)


_READ, _WRITE = 1, 2

IOCTL_INFO = _ctl_code(0x800, _READ)
IOCTL_CONNECT = _ctl_code(0x801, _READ | _WRITE)
IOCTL_DISCONNECT = _ctl_code(0x802, 0)
IOCTL_LOG = _ctl_code(0x803, _READ)

STAGE_NAMES = {
    1: "interface/radio lookup",
    2: "BRB submit",
    3: "remote response",
    4: "configuration",
    5: "open",
}

SECURITY = {"none": 0, "auth": 1, "encrypt": 2}


class Info(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("route", ctypes.c_uint32),  # 1 root, 2 bthenum
        ("radio_addr", ctypes.c_uint64),
    ]


class ConnectIn(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("bth_addr", ctypes.c_uint64),
        ("psm", ctypes.c_uint16),
        ("mtu", ctypes.c_uint16),
        ("security", ctypes.c_uint32),
        ("timeout_ms", ctypes.c_uint32),
    ]


class ConnectOut(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("ntstatus", ctypes.c_int32),
        ("bt_status", ctypes.c_uint32),
        ("stage", ctypes.c_uint32),
        ("out_mtu", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16),
    ]


def addr_to_u64(addr):
    """'AA:BB:CC:DD:EE:FF' -> 0xAABBCCDDEEFF."""
    parts = addr.split(":")
    if len(parts) != 6:
        raise ValueError("not a Bluetooth address: %r" % (addr,))
    try:
        octets = [int(p, 16) for p in parts]
    except ValueError:
        raise ValueError("not a Bluetooth address: %r" % (addr,)) from None
    if any(len(p) != 2 or not 0 <= o <= 255 for p, o in zip(parts, octets)):
        raise ValueError("not a Bluetooth address: %r" % (addr,))
    value = 0
    for o in octets:
        value = (value << 8) | o
    return value
