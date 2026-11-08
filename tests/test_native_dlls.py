# SPDX-License-Identifier: Apache-2.0
"""The Windows DLLs export the TrackIR / FreeTrack entry points games look up by name.

Build first:  native/build.sh   (skipped when native/out/ is missing)
Run:  python -m unittest tests.test_native_dlls -v
"""
import os
import struct
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "native", "out")

NP = {"NP_GetData", "NP_GetSignature", "NP_RegisterProgramProfileID", "NP_StartDataTransmission",
      "NP_QueryVersion", "NP_RegisterWindowHandle", "NP_RequestData"}
FT = {"FTGetData", "FTProvider", "FTGetDllVersion", "FTReportName"}


def pe_exports(path):
    """(machine, set of exported names) from a PE file - minimal reader, no dependencies."""
    with open(path, "rb") as f:
        data = f.read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe:pe + 4] == b"PE\0\0"
    machine, nsec = struct.unpack_from("<HH", data, pe + 4)
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dd = opt + (96 if magic == 0x10B else 112)
    exp_rva = struct.unpack_from("<I", data, dd)[0]
    sections = []
    for i in range(nsec):
        s = opt + opt_size + 40 * i
        vsize, va, rawsize, rawptr = struct.unpack_from("<IIII", data, s + 8)
        sections.append((va, max(vsize, rawsize), rawptr))

    def off(rva):
        for va, size, raw in sections:
            if va <= rva < va + size:
                return rva - va + raw
        raise ValueError(rva)

    if not exp_rva:
        return machine, set()
    e = off(exp_rva)
    n_names, names_rva = struct.unpack_from("<I", data, e + 24)[0], struct.unpack_from("<I", data, e + 32)[0]
    names = set()
    for i in range(n_names):
        p = off(struct.unpack_from("<I", data, off(names_rva) + 4 * i)[0])
        names.add(data[p:data.index(b"\0", p)].decode())
    return machine, names


@unittest.skipUnless(os.path.isdir(OUT), "native/out not built")
class NativeDllTests(unittest.TestCase):
    def check(self, name, machine, wanted):
        m, names = pe_exports(os.path.join(OUT, name))
        self.assertEqual(m, machine, name)
        self.assertTrue(wanted <= names, f"{name}: missing {sorted(wanted - names)}")

    def test_npclient64(self):
        self.check("NPClient64.dll", 0x8664, NP)

    def test_npclient32_undecorated(self):
        self.check("NPClient.dll", 0x14C, NP)

    def test_freetrack64(self):
        self.check("freetrackclient64.dll", 0x8664, FT)

    def test_freetrack32_undecorated(self):
        self.check("freetrackclient.dll", 0x14C, FT)


if __name__ == "__main__":
    unittest.main()
