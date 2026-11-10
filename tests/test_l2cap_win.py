# SPDX-License-Identifier: Apache-2.0
"""Windows L2CAP driver interface: the Python mirror must match public.h.

Run:  python -m unittest tests.test_l2cap_win -v
"""
import ctypes
import os
import re
import sys
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


if __name__ == "__main__":
    unittest.main()
