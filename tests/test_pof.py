import unittest

from apu import pof


class Crc(unittest.TestCase):
    def test_is_crc16_x25(self):
        # standard check value for CRC-16/X.25
        self.assertEqual(pof.finish_crc(pof.crc16(b"123456789")), 0x906E)


class RoundTrip(unittest.TestCase):
    def build(self):
        p = pof.Pof()
        p.add_text(pof.TAG_CREATOR, "apu-revive")
        p.add_text(pof.TAG_DEVICE, "EPM7160ELC84-15")
        p.add_flag(pof.TAG_SECURITY, 0)
        p.add(pof.Electrical(0, 1, 80, 2, 0x3FF, 0, bytes(range(200))))
        p.add(pof.Logical(0x12345, 8, 16, 0, bytes(16), wide=True))
        return p

    def test_round_trip(self):
        raw = self.build().to_bytes()
        q = pof.Pof.from_bytes(raw)
        self.assertTrue(q.crc_ok)
        self.assertEqual((q.creator, q.device, q.flag(pof.TAG_SECURITY)), ("apu-revive", "EPM7160ELC84-15", 0))
        e = q.electrical()[0]
        self.assertEqual((e.start_col, e.start_row, e.col_count, e.row_count, e.mask, len(e.data)), (0, 1, 80, 2, 0x3FF, 200))
        self.assertEqual(q.logical()[0].start_address, 0x12345)
        self.assertEqual(q.to_bytes(), raw)

    def test_damage_is_detected(self):
        raw = bytearray(self.build().to_bytes())
        raw[40] ^= 1
        self.assertFalse(pof.Pof.from_bytes(bytes(raw)).crc_ok)
        self.assertRaises(ValueError, pof.Pof.from_bytes, b"JED\0" + bytes(20))
        self.assertRaises(ValueError, pof.Pof.from_bytes, bytes(raw[:30]))


if __name__ == "__main__":
    unittest.main()


class Image(unittest.TestCase):
    def test_image_round_trip_through_pof(self):
        from apu.pcf import Region
        regions = [Region(kind=0x200, row=1, col=0, n=3, count=80, mask=0x3FF),
                   Region(kind=0x200, row=4, col=0, n=2, count=16, mask=0x3FF),
                   Region(kind=0x10, row=0, col=0, n=1, count=8, mask=0xFF)]       # ID row: left out
        rows = {r: [(r * 37 + i * 5) & 0x3FF for i in range(80 if r < 4 else 16)] for r in range(1, 6)}
        rows[0] = [0x3FF] * 80
        p = pof.Pof.from_bytes(pof.image_to_pof(regions, rows, "EPM7160ELC84-15").to_bytes())
        self.assertTrue(p.crc_ok)
        e = p.electrical()
        self.assertEqual([(x.start_col, x.start_row, x.col_count, x.row_count, x.flags) for x in e],
                         [(1, 0, 3, 80, 0x200), (4, 0, 2, 16, 0x200)])
        self.assertEqual(len(e[0].data), 3 * 80 * 2)
        back = pof.pof_to_image(p)
        self.assertEqual(back, {r: rows[r] for r in range(1, 6)})
