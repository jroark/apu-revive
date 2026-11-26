import itertools
import unittest

from apu import ahdl_eval, synth
from tests.test_ahdl import BOOLE, COUNTER, DECODE, LIGHTS, PRIORITY, TABLE


class Minimise(unittest.TestCase):
    def test_merges_cubes(self):
        # f = a  (minterms where bit 0 is set, 3 variables)
        self.assertEqual(synth.minimise({1, 3, 5, 7}, 3), [(1, 6)])
        self.assertEqual(synth.minimise(set(), 3), [])
        cover = synth.minimise({0, 1, 2, 5, 6, 7}, 3)
        covered = {m for m in range(8) for value, mask in cover if (m & ~mask) == value}
        self.assertEqual(covered, {0, 1, 2, 5, 6, 7})
        self.assertLessEqual(len(cover), 3)


class Synthesise(unittest.TestCase):
    def check_combinational(self, text):
        net = synth.synthesise(text)
        design = ahdl_eval.Design(text)
        for values in itertools.product((0, 1), repeat=len(design.inputs)):
            env = dict(zip(design.inputs, values))
            want = design.evaluate(**env)
            for pin, eq in net.outputs.items():
                self.assertEqual(eq.evaluate(env), want[pin], (pin, env))
        return net

    def test_equations_match_the_evaluator(self):
        for text in (BOOLE, PRIORITY, DECODE, TABLE):
            self.check_combinational(text)

    def test_equation_text_and_inversion(self):
        net = self.check_combinational(BOOLE)
        self.assertEqual(net.outputs["out1"].text(), "!a0 & a1")
        self.assertEqual(net.outputs["out2"].text(), "!a0 & a1 # b")
        text = "SUBDESIGN n ( a, b, c : INPUT; y : OUTPUT; ) BEGIN y = !(a & b & c); END;"
        eq = self.check_combinational(text).outputs["y"]
        self.assertTrue(eq.invert)                        # one inverted term beats three terms
        self.assertEqual(len(eq.terms), 1)

    def test_counter_registers(self):
        net = synth.synthesise(COUNTER)
        self.assertEqual(sorted(net.registers), ["count0", "count1", "count2", "count3"])
        r0 = net.registers["count0"]
        self.assertEqual(r0["clk"].text(), "clk")
        self.assertEqual(r0["clrn"].text(), "!clr")
        self.assertNotIn("prn", r0)
        # replay the next-state equations against the simulator for a few hundred steps
        sim = ahdl_eval.Simulator(COUNTER)
        import random
        rng = random.Random(1)
        for _ in range(300):
            ins = {"ena": rng.randint(0, 1), "load": rng.random() < 0.2, "d": rng.randint(0, 15), "clr": 0, "clk": 0}
            sim.set(**ins)
            env = dict(sim.inputs)
            env.update({b + ".q": v for b, v in sim.q.items()})
            expect = {bit: eqs["d"].evaluate(env) if "d" in eqs else eqs["t"].evaluate(env) ^ env[bit + ".q"]
                      for bit, eqs in net.registers.items()}
            sim.clock()
            self.assertEqual(sim.q, expect)

    def test_counter_bits_become_toggle_flip_flops(self):
        text = """SUBDESIGN c ( clk, en : INPUT; q[7..0] : OUTPUT; ) VARIABLE r[7..0] : DFF;
                  BEGIN r[].clk = clk; IF en THEN r[].d = r[].q + 1; ELSE r[].d = r[].q; END IF; q[] = r[]; END;"""
        net = synth.synthesise(text)
        top = net.registers["r7"]
        self.assertIn("t", top)                                    # as the original compiler's TFFE
        self.assertEqual(len(top["t"].terms), 1)                   # en & r0..r6: one product term
        self.assertEqual(len(top["t"].terms[0]), 8)

    def test_state_machine_reset_becomes_clear(self):
        net = synth.synthesise(LIGHTS)
        bits = sorted(net.registers)
        self.assertEqual(len(bits), 2)
        for bit in bits:
            self.assertEqual(net.registers[bit]["clrn"].text(), "!reset")
            self.assertEqual(net.registers[bit]["clk"].text(), "clk")
        self.assertIn("red = ", net.report())

    def test_limit_is_per_function(self):
        wide = "SUBDESIGN big ( a[15..0] : INPUT; y : OUTPUT; ) BEGIN y = (a[] == 5); END;"
        self.assertRaises(synth.TooLarge, synth.synthesise, wide)
        # 24 inputs, but each output depends on only three of them
        split = "SUBDESIGN wide ( a[7..0], b[7..0], c[7..0] : INPUT; y[7..0] : OUTPUT; ) BEGIN y[] = a[] & b[] # !c[]; END;"
        net = synth.synthesise(split)
        self.assertEqual(net.outputs["y3"].text(), "!c3 # a3 & b3")

    def test_tristate_pin_gets_data_and_enable_equations(self):
        text = """SUBDESIGN t ( a, b, oe : INPUT; io : BIDIR; back : OUTPUT; )
                  BEGIN io = TRI(a & b, oe); back = io; END;"""
        net = synth.synthesise(text)
        self.assertEqual(net.outputs["io"].text(), "a & b")
        self.assertEqual(net.enables["io"].text(), "oe")
        self.assertEqual(net.outputs["back"].text(), "a & b & oe # io@ext & !oe")
        self.assertIn("io.oe = oe;", net.report())

    def test_dependencies(self):
        d = ahdl_eval.Design(COUNTER)
        deps = d.dependencies()
        self.assertEqual(deps["top"], {"count%d.q" % i for i in range(4)})
        self.assertEqual(deps["count0.clk"], {"clk"})
        self.assertIn("load", deps["count2.d"])


if __name__ == "__main__":
    unittest.main()
