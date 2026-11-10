# SPDX-License-Identifier: Apache-2.0
"""Windows L2CAP driver interface: the Python mirror must match public.h.

Run:  python -m unittest tests.test_l2cap_win -v
"""
import ctypes
import os
import queue
import re
import select
import socket
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import l2cap_win  # noqa: E402

HEADER = os.path.join(ROOT, "windows", "driver", "public.h")

ACCESS = {"FILE_ANY_ACCESS": 0, "FILE_READ_ACCESS": 1, "FILE_WRITE_ACCESS": 2}


def _header():
    with open(HEADER, encoding="utf-8") as f:
        return f.read()


def _ctl_code(name):
    m = re.search(
        r"#define\s+%s\s*\\?\s*CTL_CODE\(\s*FILE_DEVICE_UNKNOWN\s*,\s*(0x[0-9A-Fa-f]+)\s*,"
        r"\s*METHOD_BUFFERED\s*,\s*([A-Z_| ]+?)\s*\)" % name,
        _header(),
    )
    assert m, name
    access = 0
    for part in m.group(2).split("|"):
        access |= ACCESS[part.strip()]
    return (0x22 << 16) | (access << 14) | (int(m.group(1), 16) << 2)


class InterfaceTests(unittest.TestCase):
    def test_ioctl_codes_match_header(self):
        for name, value in (
            ("IOCTL_MPL2_INFO", l2cap_win.IOCTL_INFO),
            ("IOCTL_MPL2_CONNECT", l2cap_win.IOCTL_CONNECT),
            ("IOCTL_MPL2_DISCONNECT", l2cap_win.IOCTL_DISCONNECT),
            ("IOCTL_MPL2_LOG", l2cap_win.IOCTL_LOG),
        ):
            self.assertEqual(_ctl_code(name), value, name)

    def test_struct_sizes(self):
        self.assertEqual(ctypes.sizeof(l2cap_win.ConnectIn), 24)
        self.assertEqual(ctypes.sizeof(l2cap_win.ConnectOut), 16)
        self.assertEqual(ctypes.sizeof(l2cap_win.Info), 16)

    def test_guid_matches_header(self):
        m = re.search(r"\{\s*([0-9A-Fa-f]{8}-[0-9A-Fa-f-]{27})\s*\}", _header())
        self.assertIsNotNone(m)
        self.assertEqual(
            m.group(1).lower(), l2cap_win.GUID_DEVINTERFACE_MOUSEPODS_L2CAP.lower()
        )

    def test_constants(self):
        h = _header()
        self.assertIn("MPL2_PSM_AAP 0x1001", h)
        self.assertEqual(l2cap_win.PSM_AAP, 0x1001)
        self.assertEqual(l2cap_win.VERSION, 1)
        self.assertEqual(l2cap_win.SECURITY, {"none": 0, "auth": 1, "encrypt": 2})
        self.assertEqual(sorted(l2cap_win.STAGE_NAMES), [1, 2, 3, 4, 5])

    def test_addr_to_u64(self):
        self.assertEqual(l2cap_win.addr_to_u64("AA:BB:CC:DD:EE:FF"), 0xAABBCCDDEEFF)
        with self.assertRaises(ValueError):
            l2cap_win.addr_to_u64("AA:BB")

    def test_imports_on_linux(self):
        self.assertTrue(hasattr(l2cap_win, "ConnectIn"))


ADDR = "AA:BB:CC:DD:EE:FF"
HANDLE = 0x1234


class FakeDriver:
    """Stands in for kernel32: failing calls return None and set the last error,
    like the real API. `incoming` holds bytes to deliver or ("err", code)."""

    def __init__(self, connect_out=None, connect_error=None):
        self.ioctls = []
        self.writes = []
        self.closed = []
        self.incoming = queue.Queue()
        self.cancelled = threading.Event()
        self.connect_out = connect_out or l2cap_win.ConnectOut(0, 0, 0, 1000, 0)
        self.connect_error = connect_error
        self.paths = ["\\\\?\\fake"]
        self.err = 0
        self.connect_in = None

    def list_device_paths(self, guid):
        return self.paths

    def GetLastError(self):
        return self.err

    def CreateFileW(self, path):
        return HANDLE

    def DeviceIoControl(self, handle, code, data, out_size):
        self.ioctls.append(code)
        if code == l2cap_win.IOCTL_CONNECT:
            self.connect_in = l2cap_win.ConnectIn.from_buffer_copy(data)
            if self.connect_error:
                self.err = self.connect_error
                return None
            return bytes(self.connect_out)
        if code == l2cap_win.IOCTL_INFO:
            return bytes(l2cap_win.Info(1, 2, 0xAABBCCDDEEFF))
        if code == l2cap_win.IOCTL_LOG:
            return "hello \u00e4".encode()
        return b""

    def ReadFile(self, handle, size):
        while True:
            if self.cancelled.is_set():
                self.err = l2cap_win.ERROR_OPERATION_ABORTED
                return None
            try:
                item = self.incoming.get(timeout=0.01)
            except queue.Empty:
                continue
            if isinstance(item, tuple):
                self.err = item[1]
                return None
            return item

    def WriteFile(self, handle, data):
        self.writes.append(data)
        return len(data)

    def CancelIoEx(self, handle):
        self.cancelled.set()
        return True

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


def _open(fake, **kw):
    return l2cap_win.open_seqpacket(ADDR, 0x1001, 2582, 1, api=fake, **kw)


class ChannelTest(unittest.TestCase):
    def open(self, fake):
        ch = _open(fake)
        self.addCleanup(ch.close)
        return ch

    def test_only_psm_0x1001(self):
        fake = FakeDriver()
        with self.assertRaises(PermissionError):
            l2cap_win.open_seqpacket(ADDR, 0x0001, 2582, 1, api=fake)
        self.assertNotIn(l2cap_win.IOCTL_CONNECT, fake.ioctls)

    def test_connect_request_fields(self):
        fake = FakeDriver()
        self.open(fake)
        c = fake.connect_in
        self.assertEqual((c.bth_addr, c.psm, c.mtu, c.security, c.timeout_ms),
                         (0xAABBCCDDEEFF, 0x1001, 2582, 2, 1000))

    def test_connect_failure_carries_stage(self):
        fake = FakeDriver(l2cap_win.ConnectOut(-0x3FFFFFFF, 5, 3, 0, 0))
        with self.assertRaises(l2cap_win.DriverError) as cm:
            _open(fake)
        self.assertEqual((cm.exception.stage, cm.exception.bt_status, cm.exception.ntstatus),
                         (3, 5, -0x3FFFFFFF))
        self.assertEqual(fake.closed, [HANDLE])

    def test_refused_connect_is_stage_0(self):
        for err, exc in ((l2cap_win.ERROR_ACCESS_DENIED, PermissionError),
                         (l2cap_win.ERROR_BUSY, l2cap_win.DriverError),
                         (l2cap_win.ERROR_INVALID_PARAMETER, l2cap_win.DriverError)):
            fake = FakeDriver(connect_error=err)
            with self.assertRaises(exc) as cm:
                _open(fake)
            if exc is l2cap_win.DriverError:
                self.assertEqual((cm.exception.stage, cm.exception.winerror), (0, err))
            self.assertEqual(fake.closed, [HANDLE])

    def test_driver_not_installed(self):
        fake = FakeDriver()
        fake.paths = []
        with self.assertRaises(FileNotFoundError):
            _open(fake)

    def test_connect_out_exposed(self):
        ch = self.open(FakeDriver())
        self.assertEqual(ch.connect_out["out_mtu"], 1000)
        self.assertEqual(ch.dropped, 0)

    def test_packets_keep_boundaries(self):
        fake = FakeDriver()
        ch = self.open(fake)
        fake.incoming.put(b"\x04\x00\x04\x00A")
        fake.incoming.put(b"BB")
        ch.settimeout(1)
        self.assertEqual(ch.recv(4096), b"\x04\x00\x04\x00A")
        self.assertEqual(ch.recv(4096), b"BB")

    def test_select_sees_packets(self):
        fake = FakeDriver()
        ch = self.open(fake)
        self.assertEqual(select.select([ch], [], [], 0.05)[0], [])
        fake.incoming.put(b"x")
        self.assertEqual(select.select([ch], [], [], 1)[0], [ch])
        ch.recv(10)
        self.assertEqual(select.select([ch], [], [], 0.05)[0], [])

    def test_recv_timeout(self):
        ch = self.open(FakeDriver())
        ch.settimeout(0.05)
        with self.assertRaises(socket.timeout):
            ch.recv(10)

    def test_disconnect_raises(self):
        fake = FakeDriver()
        ch = self.open(fake)
        fake.incoming.put(("err", l2cap_win.ERROR_DEVICE_NOT_CONNECTED))
        ch.settimeout(1)
        with self.assertRaises(ConnectionResetError):
            ch.recv(10)
        self.assertEqual(select.select([ch], [], [], 0.05)[0], [ch])
        with self.assertRaises(ConnectionResetError):
            ch.recv(10)

    def test_oversize_packet_is_error(self):
        fake = FakeDriver()
        ch = self.open(fake)
        fake.incoming.put(("err", l2cap_win.ERROR_INSUFFICIENT_BUFFER))
        fake.incoming.put(b"next")
        ch.settimeout(1)
        self.assertEqual(ch.recv(10), b"next")
        self.assertEqual(ch.dropped, 1)

    def test_send_writes_one_packet(self):
        fake = FakeDriver()
        ch = self.open(fake)
        self.assertEqual(ch.send(b"xyz"), 3)
        self.assertEqual(fake.writes, [b"xyz"])

    def test_close_disconnects_and_joins(self):
        fake = FakeDriver()
        ch = _open(fake)
        thread = ch._thread
        rd = ch._rd
        ch.close()
        self.assertIn(l2cap_win.IOCTL_DISCONNECT, fake.ioctls)
        self.assertFalse(thread.is_alive())
        self.assertEqual(fake.closed, [HANDLE])
        self.assertEqual(rd.fileno(), -1)
        ch.close()                                  # idempotent
        self.assertEqual(fake.closed, [HANDLE])


class DriverErrorTest(unittest.TestCase):
    def test_attributes_set_after_oserror_init(self):
        # On Windows OSError.__init__ resets winerror, so the attributes must be
        # assigned after super().__init__; Linux cannot show that, so check the order.
        import inspect
        src = inspect.getsource(l2cap_win.DriverError.__init__)
        self.assertLess(src.index("super().__init__"), src.index("self.winerror ="))
        self.assertLess(src.index("super().__init__"), src.index("self.stage ="))

    def test_attributes(self):
        e = l2cap_win.DriverError(-5, 6, 0, winerror=170)
        self.assertEqual((e.ntstatus, e.bt_status, e.stage, e.winerror), (-5, 6, 0, 170))


class InfoTest(unittest.TestCase):
    def test_driver_info(self):
        fake = FakeDriver()
        self.assertEqual(l2cap_win.driver_info(fake),
                         {"version": 1, "route": "bthenum", "radio": "AA:BB:CC:DD:EE:FF"})
        self.assertEqual(fake.closed, [HANDLE])

    def test_driver_log(self):
        self.assertEqual(l2cap_win.driver_log(FakeDriver()), "hello \u00e4")




if __name__ == "__main__":
    unittest.main()
