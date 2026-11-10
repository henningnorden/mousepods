# SPDX-License-Identifier: Apache-2.0
"""L2CAP connect, with a ctypes path for Pythons built without Bluetooth support.

Run:  python -m unittest tests.test_l2cap -v
"""
import os
import socket
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import l2cap  # noqa: E402


class FakeLibc:
    """Stands in for libc: socket() hands out one end of a real socketpair."""

    def __init__(self):
        self.a, self.b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.calls = []

    def socket(self, family, typ, proto):
        self.calls.append(("socket", family, typ, proto))
        return self.a.detach()

    def setsockopt(self, fd, level, name, val, size):
        self.calls.append(("setsockopt", level, name))
        return 0

    def getsockopt(self, fd, level, name, val, size):
        self.calls.append(("getsockopt", level, name))
        return 0

    def connect(self, fd, addr, size):
        self.calls.append(("connect", bytes(addr)[:size]))
        return 0


class L2capTests(unittest.TestCase):
    def test_pack_sockaddr_l2_layout(self):
        raw = l2cap.pack_sockaddr_l2("AA:BB:CC:DD:EE:FF", 0x1001)
        self.assertEqual(len(raw), 14)
        family, psm = struct.unpack_from("<HH", raw)
        self.assertEqual((family, psm), (31, 0x1001))
        self.assertEqual(raw[4:10], bytes.fromhex("FFEEDDCCBBAA"))
        self.assertEqual(raw[10:14], b"\0\0\0\0")

    def test_native_path_used_when_available(self):
        fake = mock.MagicMock()
        fake.getsockopt.return_value = bytes(12)
        with mock.patch.object(l2cap.socket, "AF_BLUETOOTH", 31, create=True), \
                mock.patch.object(l2cap.socket, "BTPROTO_L2CAP", 0, create=True), \
                mock.patch.object(l2cap.socket, "socket", return_value=fake) as ctor:
            got = l2cap.open_seqpacket("AA:BB:CC:DD:EE:FF", 0x1001, 2582, 8)
        self.assertIs(got, fake)
        ctor.assert_called_once_with(31, socket.SOCK_SEQPACKET, 0)
        fake.connect.assert_called_once_with(("AA:BB:CC:DD:EE:FF", 0x1001))

    def test_ctypes_path_without_af_bluetooth(self):
        libc = FakeLibc()
        with mock.patch.object(l2cap, "_has_native", return_value=False), \
                mock.patch.object(l2cap, "_libc", return_value=libc):
            s = l2cap.open_seqpacket("AA:BB:CC:DD:EE:FF", 0x1001, 2582, 8)
        try:
            self.assertEqual(libc.calls[0], ("socket", 31, socket.SOCK_SEQPACKET, 0))
            connect = [c for c in libc.calls if c[0] == "connect"][0]
            self.assertEqual(connect[1], l2cap.pack_sockaddr_l2("AA:BB:CC:DD:EE:FF", 0x1001))
            s.send(b"ping")                        # usable as a normal Python socket afterwards
            self.assertEqual(libc.b.recv(16), b"ping")
        finally:
            s.close()
            libc.b.close()

    def test_windows_dispatch(self):
        import l2cap_win
        with mock.patch.object(l2cap.sys, "platform", "win32"), \
                mock.patch.object(l2cap_win, "open_seqpacket", return_value="chan") as opener:
            got = l2cap.open_seqpacket("AA:BB:CC:DD:EE:FF", 0x1001, 2582, 8)
        self.assertEqual(got, "chan")
        opener.assert_called_once_with("AA:BB:CC:DD:EE:FF", 0x1001, 2582, 8, security="encrypt")

    def test_bad_address_rejected(self):
        with self.assertRaises(ValueError):
            l2cap.pack_sockaddr_l2("not-an-address", 1)


if __name__ == "__main__":
    unittest.main()
