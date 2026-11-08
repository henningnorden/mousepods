# SPDX-License-Identifier: Apache-2.0
"""config.json written from several threads (web requests, controller) must not lose updates.

Run:  python -m unittest tests.test_config_write -v
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import airpods_mouse_controller as m  # noqa: E402


class ConfigWriteTests(unittest.TestCase):
    def test_concurrent_updates_are_all_kept(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "config.json")
        with open(path, "w") as f:
            f.write("{}")
        barrier = threading.Barrier(8)

        def writer(n):
            barrier.wait()
            for i in range(25):
                m.update_config_file(path, {"games": {str(n * 100 + i): {"enabled": True}}})

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with open(path) as f:
            games = json.load(f)["games"]
        leftovers = [x for x in os.listdir(d) if x != "config.json"]
        shutil.rmtree(d)
        self.assertEqual(len(games), 8 * 25)
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
