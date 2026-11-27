import random
import unittest

from apu import jtag, svf7064s


class DecodeReads(unittest.TestCase):
    def test_round_trip(self):
        rng = random.Random(7)
        fuses = [rng.randint(0, 1) for _ in range(svf7064s.FUSES)]
        fuses[30608] = 1                                     # not stored in the array
        # what a chip holding `fuses` returns: the verify file's expected words
        words = [f["TDO"] for c, n, f in jtag._statements(svf7064s.write(fuses, "verify")) if c == "SDR" and "TDO" in f]
        words[0] = 0x5445414C
        self.assertEqual(jtag.decode_reads(words), fuses)

    def test_read_sequence_is_read_only(self):
        text = svf7064s.write([1] * svf7064s.FUSES, "verify")
        irs = {f["TDI"] for c, n, f in jtag._statements(text) if c == "SIR"}
        self.assertLessEqual(irs, jtag.READ_ONLY)
        prog = {f["TDI"] for c, n, f in jtag._statements(svf7064s.write([1] * svf7064s.FUSES, "program")) if c == "SIR"}
        self.assertTrue({0x006, 0x00A, 0x00E} <= prog)


if __name__ == "__main__":
    unittest.main()


class TemplateDevice(unittest.TestCase):
    """EPM7128S: the SVF template writes read-only verify files and its reads decode back."""

    def test_round_trip_and_read_only(self):
        from apu import svf_template
        rng = random.Random(3)
        n = svf_template.template("7128s")["fuses"]
        fuses = [rng.randint(0, 1) for _ in range(n)]
        text = svf_template.write("7128s", fuses, "verify")
        self.assertLessEqual({f["TDI"] for c, _, f in jtag._statements(text) if c == "SIR"}, jtag.READ_ONLY)
        words = [f["TDO"] for c, _, f in jtag._statements(text) if c == "SDR" and "TDO" in f]
        got = jtag.decode_reads_template("7128s", words)
        carried = {s + st * k for stmt in svf_template.template("7128s")["verify"] if isinstance(stmt, dict)
                   and isinstance(stmt.get("TDO"), list) for s, st, c in stmt["TDO"] if s >= 0 for k in range(c)}
        self.assertGreaterEqual(len(carried), n - 1)
        self.assertTrue(all(got[i] == fuses[i] for i in carried))

    def test_jtag_bits_forced(self):
        from apu import svf_template
        n = svf_template.template("7128s")["fuses"]
        self.assertNotEqual(svf_template.write("7128s", [1] * n, "program"),
                            svf_template.write("7128s", [1] * n, "program", jtag_enabled=False))
