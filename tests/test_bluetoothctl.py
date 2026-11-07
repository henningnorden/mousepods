"""Sans bluetoothctl (ex. distrobox sans bluez), l'erreur doit le dire au lieu de « aucun AirPods connecté »."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airpods_mouse_controller as m  # noqa: E402


class TestMissingBluetoothctl(unittest.TestCase):
    def test_auto_pick_reports_missing_bluetoothctl(self):
        with mock.patch.object(m.shutil, "which", return_value=None):
            with self.assertRaises(OSError) as cm:
                m.AAPClient("auto")._pick()
        self.assertEqual(cm.exception.errno, 112)
        self.assertIn("bluetoothctl", str(cm.exception))
        self.assertIn("bluez", str(cm.exception))

    def test_explicit_address_still_works_without_bluetoothctl(self):
        with mock.patch.object(m.shutil, "which", return_value=None):
            self.assertEqual(m.AAPClient("AA:BB:CC:DD:EE:FF")._pick(), "AA:BB:CC:DD:EE:FF")


if __name__ == "__main__":
    unittest.main()
