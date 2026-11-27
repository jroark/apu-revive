import unittest

from apu import fit, synth
from tests.test_ahdl import BOOLE, COUNTER, LIGHTS


class Fitting(unittest.TestCase):
    def test_small_combinational_design(self):
        result = fit.fit(synth.synthesise(BOOLE), "EPM7032", 32)
        self.assertEqual([(c.name, c.registered, c.sum_terms, c.block) for c in result.cells],
                         [("out1", False, 1, 0), ("out2", False, 2, 0)])
        self.assertIn("2 of 32 macrocells used", result.report())

    def test_counter_uses_global_clock_and_clear(self):
        # the global clear pin is active low, so it implements clrn = clr_n but not clrn = !clr
        # (an active-high clear input becomes a product-term clear, as MAX+PLUS II does)
        result = fit.fit(synth.synthesise(COUNTER), "EPM7032", 32)
        self.assertEqual((result.global_clock, result.global_clear), ("clk", None))
        regs = [c for c in result.cells if c.registered]
        self.assertEqual(len(regs), 4)
        controls = lambda c: {k: v for k, v in c.special.items() if k != "mode"}
        self.assertTrue(all(controls(c) == {"clk": "global:clk", "clrn": "term"} for c in regs))
        result = fit.fit(synth.synthesise(COUNTER.replace("count[].clrn = !clr;", "count[].clrn = clr;")), "EPM7032", 32)
        self.assertEqual((result.global_clock, result.global_clear), ("clk", "clr"))
        self.assertTrue(all(controls(c) == {"clk": "global:clk", "clrn": "global:clr"} for c in result.cells if c.registered))
        # q[] only repeats the registers, so the pins need no macrocells of their own; "top" does
        self.assertEqual(sorted(c.name for c in result.cells if not c.registered), ["top"])

    def test_wide_sum_borrows_macrocells(self):
        # parity of four inputs: eight product terms
        text = "SUBDESIGN p ( a, b, c, d : INPUT; y : OUTPUT; ) BEGIN y = a $ b $ c $ d; END;"
        result = fit.fit(synth.synthesise(text), "EPM7032", 32)
        cell = result.cells[0]
        self.assertEqual((cell.sum_terms, cell.lenders, cell.macrocells), (8, 1, 2))

    def test_pin_assignment(self):
        # a 44-pin package where macrocell n sits on pin n + 3 for the first 32 macrocells
        pin_mc = [0] * 45
        for mc in range(1, 33):
            pin_mc[mc + 3] = mc
        net = synth.synthesise(COUNTER.replace("count[].clrn = !clr;", "count[].clrn = clr;"))
        result = fit.fit(net, "EPM7032", 32)
        placement, pins = fit.assign_pins(result, net, pin_mc)
        self.assertEqual((pins["clk"], pins["clr"]), (43, 1))                 # dedicated clock and clear pins
        outs = {pins[n] for n in ("q3", "q2", "q1", "q0", "top")}
        self.assertEqual(len(outs), 5)
        self.assertEqual(pins["q0"], pin_mc.index(placement["count0"]))       # register drives its own pin
        ins = {pins[n] for n in ("ena", "load", "d3", "d2", "d1", "d0")}
        self.assertEqual(len(ins), 6)
        self.assertFalse(ins & outs)
        self.assertFalse((ins | outs) & {43, 1})

    def test_limits(self):
        net = synth.synthesise(LIGHTS)
        self.assertRaises(fit.DoesNotFit, fit.fit, net, "tiny", 2)
        # six-input parity: 32 terms, more than a macrocell can gather
        text = "SUBDESIGN p ( a[5..0] : INPUT; y : OUTPUT; ) BEGIN y = a5 $ a4 $ a3 $ a2 $ a1 $ a0; END;"
        self.assertRaises(fit.DoesNotFit, fit.fit, synth.synthesise(text), "EPM7032", 32)


if __name__ == "__main__":
    unittest.main()
