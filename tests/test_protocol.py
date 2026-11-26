import pathlib
import unittest

from apu import firmware, protocol as p

VENDOR = pathlib.Path(__file__).resolve().parent.parent / "vendor/maxplus2-10.2/MAX+plus_II_BASELINE"


class Encode(unittest.TestCase):
    def test_opcodes(self):
        wire, reads = p.encode([
            p.word(p.OP_READ, 0x05),
            p.word(p.OP_WRITE, 0x12, 0x34),
            p.word(p.OP_WRITE5, 0x01, 0xAA),
            p.word(p.OP_WRITE8, 0x02, 0x55),
            p.word(p.OP_NOP),
        ])
        self.assertEqual(wire, bytes.fromhex("85 1234 41aa c255 40ff"))
        self.assertEqual(reads, 1)

    def test_cmd1_rejected(self):
        with self.assertRaises(ValueError):
            p.encode([p.word(1)])

    def test_pad(self):
        self.assertEqual(len(p.pad(b"\x00" * 2)), 64)
        self.assertEqual(len(p.pad(b"")), 64)          # full packet of NOPs, as on Windows
        self.assertEqual(len(p.pad(b"\x00" * 64)), 128)
        self.assertTrue(p.pad(b"\x01\x02").endswith(p.NOP_PAIR))
        with self.assertRaises(ValueError):
            p.pad(b"\x00")

    def test_pad_with_reads(self):
        out = p.pad_with_reads(b"\x85", 1)
        self.assertEqual(out, bytes.fromhex("85 40ff 0100"))
        out = p.pad_with_reads(b"\x00" * 61, 0x1234)  # fill of 3 -> pad past the boundary first
        self.assertEqual(len(out), 65 + 4)
        self.assertEqual(out[-2:], bytes.fromhex("3412"))
        self.assertNotEqual(len(p.pad_with_reads(b"\x00" * 60, 1)) % 64, 0)  # last packet is short


class Rbf(unittest.TestCase):
    def test_permutation_is_involution(self):
        raw = bytes(range(117))                    # 117 + 11 = 128 bytes, two blocks
        padded = b"\xff" * p.RBF_PREFIX + raw
        once = p.rbf_stream(raw)
        self.assertEqual(once[1], padded[8])       # index 1 <-> index 8
        self.assertEqual(once[64 + 9], padded[64 + 9])  # fixed point
        twice = bytes(once[(n & ~0x3F) + p._PERM[n & 0x3F]] for n in range(len(once)))
        self.assertEqual(twice, padded)

    def test_bad_length(self):
        with self.assertRaises(ValueError):
            p.rbf_stream(b"\x00" * 10)

    @unittest.skipUnless((VENDOR / "pgdmpuz.rbf").exists(), "vendor files not extracted")
    def test_real_files(self):
        rbf = (VENDOR / "pgdmpuz.rbf").read_bytes()
        chunks = list(p.rbf_chunks(rbf))
        self.assertEqual(sum(map(len, chunks)), len(rbf) + 11)
        self.assertTrue(all(len(c) % 64 == 0 for c in chunks))
        recs = list(firmware.records((VENDOR / "pgdmpuz.drv").read_bytes()))
        self.assertEqual(len(recs), 39)
        self.assertEqual(recs[0][1][:3], bytes.fromhex("020200"))  # LJMP at reset vector


if __name__ == "__main__":
    unittest.main()
