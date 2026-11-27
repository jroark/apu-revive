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

    def test_wide_functions_go_symbolic(self):
        wide = "SUBDESIGN big ( a[15..0] : INPUT; y, z : OUTPUT; ) BEGIN y = (a[] == 5); z = (a[] > 40000); END;"
        net = synth.synthesise(wide)
        self.assertEqual(len(net.outputs["y"].terms), 1)
        self.assertEqual(len(net.outputs["y"].terms[0]), 16)
        import random
        rng = random.Random(1)
        for value in [5, 4, 40000, 40001, 65535, 0] + [rng.randrange(65536) for _ in range(300)]:
            env = {"a%d" % i: (value >> i) & 1 for i in range(16)}
            self.assertEqual(net.outputs["y"].evaluate(env), int(value == 5), value)
            self.assertEqual(net.outputs["z"].evaluate(env), int(value > 40000), value)

    def test_symbolic_agrees_with_tabulation(self):
        # BDD evaluation with irredundant covers everywhere, against settling every assignment
        designs = (BOOLE, COUNTER, DECODE, LIGHTS, PRIORITY, TABLE)
        old_sym, old_max = synth.Symbolic, synth.MAX_SUPPORT

        def no_bdd(design):
            raise ahdl_eval.Unsupported("disabled for the test")
        try:
            synth.Symbolic = no_bdd
            exhaustive = [synth.synthesise(t) for t in designs]
            synth.Symbolic, synth.MAX_SUPPORT = old_sym, -1
            symbolic = [synth.synthesise(t) for t in designs]
        finally:
            synth.Symbolic, synth.MAX_SUPPORT = old_sym, old_max
        for text, a, b in zip(designs, exhaustive, symbolic):
            pairs = [(a.outputs[k], b.outputs[k], k) for k in a.outputs]
            pairs += [(a.enables[k], b.enables[k], k) for k in a.enables]
            for bit in a.registers:
                ra, rb = a.registers[bit], b.registers[bit]
                self.assertEqual(set(ra) - {"d", "t"}, set(rb) - {"d", "t"}, bit)
                pairs += [(ra[p], rb[p], bit + "." + p) for p in ra if p not in ("d", "t")]
                pairs.append((ra, rb, bit))                      # D or T: compare the next state
            for x, y, key in pairs:
                if isinstance(x, dict):
                    eqs = [e for r in (x, y) for p, e in r.items() if p in ("d", "t")]
                    nxt = lambda r, env, key=key: (r["d"].evaluate(env) if "d" in r
                                                   else r["t"].evaluate(env) ^ env[key + ".q"])
                else:
                    eqs, nxt = [x, y], None
                names = sorted({n for e in eqs for t in e.terms for n in t} | ({key + ".q"} if nxt else set()))
                for values in itertools.product((0, 1), repeat=len(names)):
                    env = dict(zip(names, values))
                    if nxt:
                        self.assertEqual(nxt(x, env), nxt(y, env), key)
                    else:
                        self.assertEqual(x.evaluate(env), y.evaluate(env), key)

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


import pathlib
VENDOR = pathlib.Path(__file__).resolve().parent.parent / "vendor/maxplus2-10.2"


@unittest.skipUnless(VENDOR.exists(), "vendor files not extracted")
class WholeTutorialDesign(unittest.TestCase):
    def test_chiptrip_netlist_matches_the_design(self):
        import random
        tutorial = VENDOR / "MAX+plus_II_Tutorial/chiptrip"
        text = (tutorial / "chiptrip.tdf").read_text(encoding="latin1")
        resolver = ahdl_eval.directory_resolver(tutorial, *ahdl_eval.vendor_library(VENDOR / "MAX+plus_II_BASELINE"))
        net = synth.synthesise(text, resolver)
        self.assertEqual(len(net.registers), 18)        # ticket counter's unobservable half swept away
        ref = ahdl_eval.Simulator(text, resolver)
        mine = synth.NetlistSimulator(net, ref.q)
        d, rng = ref.design, random.Random(7)
        for step in range(600):
            name = rng.choice(d.inputs)
            value = 0 if name == "reset" and rng.random() < 0.8 else 1 - ref.inputs[name]
            ref.set(**{name: value})
            mine.set(**{name: value})
            self.assertEqual({p: ref.env[p] for p in d.outputs}, {p: mine.env[p] for p in d.outputs}, step)
            self.assertEqual({b: ref.q[b] for b in net.registers}, mine.q, step)
