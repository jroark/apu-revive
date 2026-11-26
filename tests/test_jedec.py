import unittest

from apu import jedec
from apu.pcf import JedecRun, Region


class FakePcf:
    regions = [Region(kind=0x200, row=1, col=0, n=2, count=4, mask=0x3FF),
               Region(kind=0x10, row=0, col=0, n=1, count=8, mask=0xFF)]

    def fuses(self):
        n = 0
        for row, col, count, mask in ((1, 0, 2, 0x001), (1, 3, 2, 0x200), (1, 1, 1, 0x030)):
            for i in range(count):
                yield n, row + i, col, mask
                n += 1


class Jedec(unittest.TestCase):
    def test_text_round_trip(self):
        fuses = [(i * 7 + i // 3) & 1 for i in range(200)]
        text = jedec.write(fuses, "title", pins=84, device="EPM7160ELC84-15", security=0)
        back = jedec.parse(text)
        self.assertEqual(back["fuses"], fuses)
        self.assertEqual((back["device"], back["pins"], back["security"]), ("EPM7160ELC84-15", 84, 0))
        self.assertTrue(back["checksum_ok"] and back["file_checksum_ok"])
        self.assertFalse(jedec.parse(text.replace("L000000 0", "L000000 1", 1))["checksum_ok"])

    def test_default_fill_and_spacing(self):
        text = "\x02x*QF 12 *F1*L0 \n0101\n*L8 00*\x030000"
        self.assertEqual(jedec.parse(text)["fuses"], [0, 1, 0, 1, 1, 1, 1, 1, 0, 0, 1, 1])
        self.assertRaises(ValueError, jedec.parse, "no markers")

    def test_fuse_is_set_when_any_masked_bit_is(self):
        rows = {1: [0x3FE, 0x010, 0, 0x200], 2: [0x001, 0, 0, 0x1FF]}
        fuses = jedec.fuses_from_image(FakePcf(), rows)
        self.assertEqual(fuses, [0, 1, 1, 0, 1])

    def test_image_round_trip(self):
        pcf = FakePcf()
        fuses = [1, 0, 0, 1, 0]
        rows = jedec.image_from_fuses(pcf, fuses)
        self.assertEqual(sorted(rows), [1, 2])
        self.assertEqual(rows[1], [0x3FF, 0x3CF, 0x3FF, 0x1FF])
        self.assertEqual(jedec.fuses_from_image(pcf, rows), fuses)
        self.assertRaises(ValueError, jedec.image_from_fuses, pcf, [1, 0])


class Plan(unittest.TestCase):
    def test_plan_skips_blank_lines_and_masks_bits(self):
        from apu import programmer

        class P:
            regions = [Region(kind=0x200, row=1, col=0, n=2, count=2, mask=0x3FF),
                       Region(kind=0x200, row=3, col=0, n=1, count=2, mask=0x2FF),
                       Region(kind=0x10, row=0, col=0, n=1, count=8, mask=0xFF)]
        image = {1: [0x3FF, 0x3FE], 2: [0x3FF, 0x3FF], 3: [0x0FF, 0x2FF], 0: [0] * 80}
        plan = programmer.plan_program(P(), image)
        self.assertEqual(plan, {1: [0x3FF, 0x3FE], 3: [0x1FF, 0x3FF]})   # bit 8 is outside line 3's mask
        got = {1: [0x3FF, 0x3FE], 2: [0x3FF, 0x3FF], 3: [0x1FF, 0x3FF]}
        self.assertEqual(programmer.verify(P(), image, got), [])            # bit 8 of line 3 is ignored
        got[1][1] = 0x3FF
        self.assertEqual(programmer.verify(P(), image, got), [(1, 1, 0x3FE, 0x3FF)])
        self.assertRaises(ValueError, programmer.plan_program, P(), {1: [0x3FF], 2: [0, 0], 3: [0, 0]})
        self.assertEqual(programmer.vpp_from_signature(bytes(9) + b"\x80"), 12250)


if __name__ == "__main__":
    unittest.main()
