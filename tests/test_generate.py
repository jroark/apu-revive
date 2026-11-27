"""Generated EPM7032 fuses, run in the fuse-level simulator, behave like the source design."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
K = ROOT / "vendor/maxplus2-10.2/7k"


@unittest.skipUnless((K / "m7032044.daf").exists(), "vendor device files not extracted")
class Generate(unittest.TestCase):
    def run_design(self, text, steps=300):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        from fusesim_check import cosim, epm7032
        from apu import generate, parts, project, synth
        net = synth.synthesise(text)
        part = parts.lookup(parts.load(ROOT / "vendor/maxplus2-10.2/MAX+plus_II_BASELINE/partinfo.crf"), "EPM7032LC44")
        fitted = project.fit_part(net, part, K)
        fm = epm7032()
        fuses = generate.generate(net, fitted.result, fitted.placement, fitted.pins, fm)
        self.assertEqual(len(fuses), 14672)
        checks, mismatches = cosim("t", text, None, fm, fuses, fitted.pins, steps, verbose=False)
        self.assertGreater(checks, 0)
        self.assertEqual(mismatches, [])
        return fuses

    def test_counter_with_enable_load_and_clear(self):
        from tests.test_ahdl import COUNTER
        self.run_design(COUNTER)

    def test_state_machine_and_combinational(self):
        from tests.test_ahdl import BOOLE, LIGHTS, DECODE
        for text in (BOOLE, LIGHTS, DECODE):
            self.run_design(text)

    def test_pof_round_trip(self):
        from apu import pof
        from tests.test_ahdl import COUNTER
        fuses = self.run_design(COUNTER, steps=20)
        p = pof.Pof.from_bytes(pof.fuses_to_logical_pof(fuses, "EPM7032LC44").to_bytes())
        self.assertTrue(p.crc_ok)
        self.assertEqual(pof.logical_fuses(p), fuses)

    def test_wide_sum_uses_parallel_expanders(self):
        # parity of five inputs: 16 product terms, so three macrocells lend theirs
        self.run_design("SUBDESIGN w ( a, b, c, d, e : INPUT; y : OUTPUT; ) BEGIN y = a $ b $ c $ d $ e; END;")

    def test_refuses_what_it_cannot_encode(self):
        from apu import generate
        tri = "SUBDESIGN t ( a, oe : INPUT; y : OUTPUT; ) BEGIN y = TRI(a, oe); END;"
        with self.assertRaises(generate.CannotGenerate):
            self.run_design(tri)


if __name__ == "__main__":
    unittest.main()
