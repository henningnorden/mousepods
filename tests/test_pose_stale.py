# SPDX-License-Identifier: Apache-2.0
"""Staleness check used by the DLLs (native/pose_stale.h): when MousePods stops writing the pose
file, the game gets a centred camera after a moment instead of a frozen one.

Compiles the header natively with cc and calls it through ctypes (skipped without a compiler).
Run:  python -m unittest tests.test_pose_stale -v
"""
import ctypes
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CC = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")


@unittest.skipUnless(CC, "no C compiler")
class PoseStaleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        src = os.path.join(cls.tmp, "t.c")
        with open(src, "w") as f:
            f.write('#include <stdint.h>\n#include "pose_stale.h"\n'
                    "int fresh(uint32_t seq, uint64_t now, PoseStale *st) { return pose_fresh(st, seq, now); }\n")
        lib = os.path.join(cls.tmp, "t.so")
        subprocess.run([CC, "-shared", "-fPIC", "-O2", "-I", os.path.join(ROOT, "native"), "-o", lib, src], check=True)
        cls.lib = ctypes.CDLL(lib)
        cls.lib.fresh.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_void_p]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def state(self):
        buf = ctypes.create_string_buffer(64)
        self.lib.pose_stale_init = None
        return buf

    def test_first_read_is_fresh(self):
        st = ctypes.create_string_buffer(64)
        self.assertEqual(self.lib.fresh(0, 1000, st), 1)

    def test_changing_seq_stays_fresh(self):
        st = ctypes.create_string_buffer(64)
        for i in range(10):
            self.assertEqual(self.lib.fresh(i, 1000 + i * 10_000, st), 1)

    def test_unchanged_seq_goes_stale_after_limit(self):
        st = ctypes.create_string_buffer(64)
        self.lib.fresh(7, 1000, st)
        self.assertEqual(self.lib.fresh(7, 1000 + 1499, st), 1)
        self.assertEqual(self.lib.fresh(7, 1000 + 1501, st), 0)
        self.assertEqual(self.lib.fresh(8, 1000 + 5000, st), 1)      # writer back: fresh again


if __name__ == "__main__":
    unittest.main()
