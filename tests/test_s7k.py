import pathlib
import sys
import tempfile
import unittest

from apu import s7k

ROOT = pathlib.Path(__file__).resolve().parent.parent
HAVE_FILES = (ROOT / "vendor/maxplus2-10.2/7k/s7128084.daf").exists()


class Epm7128sTables(unittest.TestCase):
    def setUp(self):
        self.t = s7k.Tables.load("7128s")

    def test_period(self):
        self.assertEqual(len(s7k.PERIOD), 56)
        for f in range(4):
            self.assertEqual(s7k.PERIOD.count(f), 13)          # 13 taps per input mux
            self.assertEqual(s7k.PERIOD.count(4 + f), 1)       # one selector fuse

    def test_partners_are_bijections(self):
        self.assertEqual(sorted(self.t.PARTNER_RIGHT), list(range(36)))
        self.assertEqual(sorted(self.t.PARTNER_LEFT), list(range(36)))

    def test_every_source_has_a_tap(self):
        for mc in range(1, 129):
            self.assertIn("mc%d" % mc, self.t.TAPS)
        for pin in self.t.PIN_OE:
            self.assertIn("pin%d" % pin, self.t.TAPS)
        taps = [o for v in self.t.TAPS.values() for o in v]
        self.assertEqual(len(taps), len(set(taps)))            # a tap carries one source
        self.assertTrue(all(0 <= o < 504 and s7k.PERIOD[o % 56] < 4 for o in taps))

    def test_route_own_and_partner(self):
        m_of, program = self.t.route({1: [("a", "pin4"), ("b", "mc17")], 8: [("a", "pin4")]})
        self.assertEqual(set(m_of[1]), {"a", "b"})
        self.assertNotEqual(m_of[1]["a"], m_of[1]["b"])
        self.assertTrue(all(self.t.ROUTING_BASE <= f < self.t.ROUTING_BASE + 8 * 504 for f in program))


@unittest.skipUnless(HAVE_FILES, "MAX+PLUS II device files not present")
class Epm7128sGenerated(unittest.TestCase):
    """Designs compiled by our generator for the EPM7128S behave like their sources in apu.fusesim."""

    def check(self, text, name, steps=300):
        sys.path.insert(0, str(ROOT / "tools"))
        from fusesim_check import cosim
        from gen_check7064s import build
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / (name + ".tdf")
            path.write_text(text)
            text, resolver, base, fuses, fitted = build(path, device="EPM7128S")
        checks, mismatches = cosim(name, text, resolver, s7k.FuseMap(*base), fuses, fitted.pins, steps, verbose=False)
        self.assertGreater(checks, 0)
        self.assertEqual(mismatches, [])
        return fitted

    def test_registers_and_logic(self):
        self.check("SUBDESIGN t\n( clk, a, b, c : INPUT;\n  q, y, z : OUTPUT; )\nVARIABLE\n\tr : DFF;\n\tn : TFF;\nBEGIN\n"
                   "\tr.clk = clk; r.d = a & !b # c;\n\tn.clk = clk; n.t = a $ c;\n\tq = r;\n\ty = n;\n\tz = !(a & b) # r;\nEND;\n", "t")

    def test_wide_sum_and_tristate(self):
        self.check("SUBDESIGN w\n( a[7..0], g : INPUT;\n  y, t : OUTPUT; )\nBEGIN\n"
                   "\ty = (a0 & a1) # (a2 & !a3) # (a4 & a5) # (a6 & !a7) # (a0 & a7) # (a1 & !a6) # (a2 & a5);\n"
                   "\tt = TRI(a0 $ a1, g);\nEND;\n", "w")


if __name__ == "__main__":
    unittest.main()
