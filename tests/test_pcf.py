import pathlib
import unittest

from apu.pcf import KIND_ID, Pcf

V = pathlib.Path(__file__).resolve().parent.parent / "vendor/maxplus2-10.2/7k"


class SignatureRow(unittest.TestCase):
    # Row 0 of a real EPM7160ELC84-15 as a line read returns it: data in every fifth cell.
    DATA = [0xF0, 0xF7, 0xE8, 0xFE, 0xE8, 0xF2, 0xF4, 0xF9, 0xF0, 0xE7, 0xE8, 0xE8, 0xE1, 0xE3, 0xE8, 0xEE]

    def cells(self):
        row = [0x3FF] * 80
        for i, v in enumerate(self.DATA):
            row[5 * i] = 0x300 | v
        return row

    def test_id_and_parameter_bytes(self):
        from apu import max7000
        cells = self.cells()
        self.assertEqual(max7000.silicon_id(cells), "ALTERA93")
        # the two bytes the vendor module's own ID read returned after the ID on this chip
        self.assertEqual([max7000.signature_byte(cells, k) for k in (8, 9)], [0xD7, 0x80])


@unittest.skipUnless((V / "m7064084.pcf").exists(), "vendor files not extracted")
class RealFiles(unittest.TestCase):
    def test_7064(self):
        p = Pcf.load(V / "m7064084.pcf")
        self.assertEqual(p.variants, 4)
        self.assertEqual(p.nominal_vcc(), 5000)
        self.assertEqual(p.siid_volts(), (5000, 0, 12000, 0))
        self.assertTrue(p.id_text.startswith("ALTERA93"))
        ids = [r for r in p.regions if r.kind == KIND_ID]
        self.assertEqual((ids[0].row, ids[0].col, ids[0].count, ids[0].mask), (0x7C, 0, 8, 0xFF))
        self.assertEqual(p.fuse_count, 29488)

    def test_variant_blocks(self):
        blocks = Pcf.load_all(V / "m7064084.pcf")
        self.assertEqual(len(blocks), 4)
        self.assertEqual([b.base for b in blocks], [8, 11788, 23526, 38086])
        self.assertEqual([b.nominal_vcc() for b in blocks], [5000, 5000, 3300, 2500])
        self.assertEqual(blocks[1].siid_volts(), (5000, 2000, 10000, 0))
        self.assertEqual(len(Pcf.load_all(V / "e7160084.pcf")), 2)

    def test_fuse_map_is_injective_and_in_range(self):
        for name in ("m7064084.pcf", "e7160084.pcf", "m7128084.pcf"):
            p = Pcf.load(V / name)
            cells = set()
            for r in p.regions:
                if r.kind != 0x100:
                    cells |= {(r.row + i, r.col + c) for i in range(r.n) for c in range(r.count)}
            seen = set()
            for _, row, col, mask in p.fuses():
                self.assertIn((row, col), cells, name)
                seen.add((row, col, mask))
            self.assertEqual(len(seen), p.fuse_count, name)
            self.assertEqual(p.fuse_count, p.declared_fuses, name)


if __name__ == "__main__":
    unittest.main()
