# SPDX-License-Identifier: Apache-2.0
"""BlueZ over D-Bus (no bluetoothctl needed, e.g. inside a Flatpak).

Run:  python -m unittest tests.test_bluez_dbus -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airpods_mouse_controller as m  # noqa: E402
import bluez_dbus  # noqa: E402

# shape of org.freedesktop.DBus.ObjectManager.GetManagedObjects() as returned by Gio (unpacked)
OBJECTS = {
    "/org/bluez/hci0": {"org.bluez.Adapter1": {"Address": "00:11:22:33:44:55", "Powered": True}},
    "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF": {"org.bluez.Device1": {
        "Address": "AA:BB:CC:DD:EE:FF", "Name": "Henning's AirPods Pro", "Paired": True, "Connected": True,
        "Modalias": "bluetooth:v004Cp2024d0A63"}},
    "/org/bluez/hci0/dev_11_22_33_44_55_66": {"org.bluez.Device1": {
        "Address": "11:22:33:44:55:66", "Name": "AirPods 3", "Paired": True, "Connected": False,
        "Modalias": "bluetooth:v004Cp2013d0A63"}},
    "/org/bluez/hci0/dev_22_22_22_22_22_22": {"org.bluez.Device1": {
        "Address": "22:22:22:22:22:22", "Name": "Keyboard", "Paired": True, "Connected": True,
        "Modalias": "usb:v046DpB33Ed0000"}},
    "/org/bluez/hci0/dev_33_33_33_33_33_33": {"org.bluez.Device1": {
        "Address": "33:33:33:33:33:33", "Name": "Unpaired Apple", "Paired": False, "Connected": False,
        "Modalias": "bluetooth:v004Cp2014d0001"}},
}


class BluezDbusTests(unittest.TestCase):
    def test_parses_paired_apple_devices_only(self):
        devs = bluez_dbus.devices_from_managed_objects(OBJECTS)
        self.assertEqual(devs, [("11:22:33:44:55:66", "AirPods 3", "2013", False),
                                ("AA:BB:CC:DD:EE:FF", "Henning's AirPods Pro", "2024", True)])

    def test_pid_lowercase(self):
        objs = {"/d": {"org.bluez.Device1": {"Address": "AA:AA:AA:AA:AA:AA", "Name": "x", "Paired": True,
                                             "Connected": True, "Modalias": "bluetooth:v004Cp200Ed0001"}}}
        self.assertEqual(bluez_dbus.devices_from_managed_objects(objs)[0][2], "200e")

    def test_info_from_objects(self):
        self.assertEqual(bluez_dbus.info_from_managed_objects(OBJECTS, "AA:BB:CC:DD:EE:FF"),
                         {"connected": True, "pid": "2024"})
        self.assertIsNone(bluez_dbus.info_from_managed_objects(OBJECTS, "99:99:99:99:99:99"))

    def test_available_false_without_gi(self):
        with mock.patch.dict(sys.modules, {"gi": None}):
            bluez_dbus._reset()
            self.assertFalse(bluez_dbus.available())
        bluez_dbus._reset()

    def test_controller_prefers_dbus_when_available(self):
        with mock.patch.object(bluez_dbus, "available", return_value=True), \
                mock.patch.object(bluez_dbus, "managed_objects", return_value=OBJECTS), \
                mock.patch.object(m.subprocess, "run", side_effect=AssertionError("bluetoothctl used")):
            self.assertEqual([d[0] for d in m.apple_audio_devices()], ["11:22:33:44:55:66", "AA:BB:CC:DD:EE:FF"])
            self.assertEqual(m.product_id("AA:BB:CC:DD:EE:FF"), "2024")
            self.assertTrue(m.bt_connected("AA:BB:CC:DD:EE:FF"))

    def test_controller_falls_back_to_bluetoothctl(self):
        out = "Device AA:BB:CC:DD:EE:FF AirPods\n"
        info = "Connected: yes\nModalias: bluetooth:v004Cp2024d0A63\n"
        runs = [mock.Mock(stdout=out), mock.Mock(stdout=info)]
        with mock.patch.object(bluez_dbus, "available", return_value=False), \
                mock.patch.object(m.subprocess, "run", side_effect=runs):
            self.assertEqual(m.apple_audio_devices(), [("AA:BB:CC:DD:EE:FF", "AirPods", "2024", True)])


if __name__ == "__main__":
    unittest.main()
