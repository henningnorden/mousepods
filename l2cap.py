# SPDX-License-Identifier: Apache-2.0
"""Open the AirPods' L2CAP channel (SOCK_SEQPACKET), with or without Python Bluetooth support.

Some Python builds (Homebrew, possibly Flatpak runtimes) lack socket.AF_BLUETOOTH. The kernel
does not care: we create and connect the socket through libc with ctypes, then wrap the file
descriptor in a normal socket object (send/recv/settimeout work for any family).
"""
import ctypes
import ctypes.util
import os
import re
import socket
import struct
import sys

AF_BLUETOOTH = 31
BTPROTO_L2CAP = 0
SOL_BLUETOOTH, BT_SECURITY, BT_SECURITY_MEDIUM = 274, 4, 2
SOL_L2CAP, L2CAP_OPTIONS = 6, 1
_ADDR = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def pack_sockaddr_l2(addr, psm):
    """struct sockaddr_l2 { sa_family_t; __le16 psm; bdaddr_t (reversed); __le16 cid; u8 type; } (14 B)"""
    if not _ADDR.match(addr or ""):
        raise ValueError(f"invalid Bluetooth address {addr!r}")
    bdaddr = bytes(int(b, 16) for b in reversed(addr.split(":")))
    return struct.pack("<HH6sHBx", AF_BLUETOOTH, psm, bdaddr, 0, 0)


def _has_native():
    return hasattr(socket, "AF_BLUETOOTH")


def _libc():
    lib = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    return lib


def _security_and_mtu(get, set_, mtu):
    """BT_SECURITY medium (encrypted link, needed for the motion data) and receive MTU."""
    set_(SOL_BLUETOOTH, BT_SECURITY, struct.pack("BB", BT_SECURITY_MEDIUM, 0))
    opts = bytearray(get(SOL_L2CAP, L2CAP_OPTIONS, 12))
    struct.pack_into("<H", opts, 2, mtu)
    set_(SOL_L2CAP, L2CAP_OPTIONS, bytes(opts))


def open_seqpacket(addr, psm, mtu, timeout):
    if sys.platform == "win32":
        import l2cap_win
        return l2cap_win.open_seqpacket(addr, psm, mtu, timeout, security="encrypt")
    if _has_native():
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
        try:
            _security_and_mtu(s.getsockopt, s.setsockopt, mtu)
        except OSError:
            pass                                   # older kernels: connect anyway
        s.settimeout(timeout)
        try:
            s.connect((addr, psm))
        except OSError:
            s.close()
            raise
        return s
    libc = _libc()
    fd = libc.socket(AF_BLUETOOTH, socket.SOCK_SEQPACKET, BTPROTO_L2CAP)
    if fd < 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    try:
        def set_(level, name, value):
            buf = ctypes.create_string_buffer(value, len(value))
            if libc.setsockopt(fd, level, name, buf, len(value)) != 0:
                e = ctypes.get_errno()
                raise OSError(e, os.strerror(e))

        def get(level, name, size):
            buf = ctypes.create_string_buffer(size)
            n = ctypes.c_uint32(size)
            if libc.getsockopt(fd, level, name, buf, ctypes.byref(n)) != 0:
                e = ctypes.get_errno()
                raise OSError(e, os.strerror(e))
            return buf.raw[:n.value]

        try:
            _security_and_mtu(get, set_, mtu)
        except OSError:
            pass
        sa = pack_sockaddr_l2(addr, psm)
        if libc.connect(fd, ctypes.create_string_buffer(sa, len(sa)), len(sa)) != 0:
            e = ctypes.get_errno()
            raise OSError(e, os.strerror(e))
    except BaseException:
        os.close(fd)
        raise
    s = socket.socket(fileno=fd)
    s.settimeout(timeout)
    return s
