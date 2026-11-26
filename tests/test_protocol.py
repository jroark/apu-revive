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


class Board(unittest.TestCase):
    def test_startup_stream(self):
        from apu import board
        wire, reads = p.encode(board.startup_words())
        self.assertEqual(reads, 0)
        self.assertEqual(wire, bytes.fromhex("0800 0801 0803 0000 41ff 42ff 48ff 49ff 4aff 0000 0901 097f"))


    def test_dac_frames_match_the_module(self):
        from apu import board
        vals = lambda words: " ".join("%02x" % (w & 0xFF) for w in words)
        # frames copied from the real start-up trace (re/traces/init_real.txt)
        self.assertEqual(vals(board.dac_words("A", 0, 1, 45)),
                         "c0 82 80 82 80 83 81 82 80 82 80 83 81 82 80 83 81 83 81 82 80 83 81 88 00")
        self.assertEqual(vals(board.dac_words("B", 3, 1, 127)),
                         "c0 83 81 83 81 83 81 82 80 83 81 83 81 83 81 83 81 83 81 83 81 83 81 84 00")
        self.assertEqual(vals(board.dac_words("B", 0, 1, 0)),
                         "c0 82 80 82 80 83 81 82 80 82 80 82 80 82 80 82 80 82 80 82 80 82 80 84 00")
        with self.assertRaises(ValueError):
            board.dac_words("C", 0, 1, 0)


    def test_rail_codes(self):
        from apu import board
        self.assertEqual(board.rail_code("vcc", 5000), 127)      # the module's start code for 5 V
        self.assertEqual(board.rail_code("vmarg", 2000), 51)
        self.assertEqual(board.rail_code("vpd", 12000), 153)
        with self.assertRaises(ValueError):
            board.rail_code("vcc", 12000)

    def test_adc_pass_shape(self):
        from apu import board
        wire, reads = p.encode(board._adc_pass_words(5, read=True))
        self.assertEqual(reads, 8)
        # eight idle writes, then address 0101 and four zero bits, each as idle/clock + a read
        self.assertEqual(wire[:16], bytes.fromhex("0190") * 8)
        self.assertEqual(wire[16:26], bytes.fromhex("0190 0192 81 0191 0193 81"))


    def test_programming_rail_codes_match_the_module(self):
        from apu import board
        # final DAC codes the module produced in simulation for these requests
        vpd = {0: 0, 1000: 12, 5000: 63, 9000: 115, 10000: 128, 11000: 141, 12000: 154, 12500: 161,
               12750: 164, 13000: 167, 13500: 174}
        for mv, code in vpd.items():
            self.assertEqual(board.vpd_code(mv), code, mv)
        for mv, code in {5000: 63, 9000: 118, 12000: 160}.items():
            self.assertEqual(board.vpg_code(mv), code, mv)

    def test_supply_trim_rule(self):
        from apu import programmer

        class Fake:
            def __init__(self, reach): self.reach, self.code = reach, None
        calls = []
        real_dac, real_send, real_adc = programmer._dac, programmer._send, programmer.board.adc_read
        fake = Fake(128)
        programmer._dac = lambda apu, chip, ch, code: calls.append(code)
        programmer._send = lambda apu, ops: b""
        programmer.board.adc_read = lambda apu, ch: 255 if calls[-1] >= fake.reach else 254
        try:
            self.assertEqual(programmer.trim_vcc(None, 5000), 128)     # as on the real unit: 127, then 128
            self.assertEqual(calls, [127, 128])
            fake.reach = 200
            with self.assertRaises(RuntimeError):                       # never runs away
                programmer.trim_vcc(None, 5000)
        finally:
            programmer._dac, programmer._send, programmer.board.adc_read = real_dac, real_send, real_adc


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
