import pathlib
import unittest

from apu import ahdl, ahdl_eval

TUTORIAL = pathlib.Path(__file__).resolve().parent.parent / "vendor/maxplus2-10.2/MAX+plus_II_Tutorial"

BOOLE = """
SUBDESIGN boole
( a0, a1, b : INPUT;  out1, out2 : OUTPUT; )
BEGIN
    out1 = a1 & !a0;      % a comment %
    out2 = out1 # b;      -- another
END;
"""
PRIORITY = """
SUBDESIGN priority
( low, middle, high : INPUT;  level[1..0] : OUTPUT; )
BEGIN
    IF high THEN level[] = 3;
    ELSIF middle THEN level[] = 2;
    ELSIF low THEN level[] = 1;
    ELSE level[] = 0;
    END IF;
END;
"""
DECODE = """
CONSTANT MAGIC = H"5";
SUBDESIGN decode
( code[2..0] : INPUT;  hit, odd, zero, sel[1..0] : OUTPUT; )
VARIABLE t : NODE;
BEGIN
    DEFAULTS sel[] = B"11"; END DEFAULTS;
    hit = (code[] == MAGIC);
    t = code0;
    odd = t;
    CASE code[] IS
        WHEN 0 => zero = VCC; sel[] = 0;
        WHEN 1, 2 => sel[] = 1;
        WHEN OTHERS => zero = GND;
    END CASE;
END;
"""
TABLE = """
SUBDESIGN tab
( a[1..0], en : INPUT;  y[1..0] : OUTPUT; )
BEGIN
    TABLE
        a[], en => y[];
        0, 1 => 3;
        B"1X", 1 => 1;
        X, 0 => 2;
    END TABLE;
END;
"""


class Parse(unittest.TestCase):
    def test_tree(self):
        tree = ahdl.parse(BOOLE)
        self.assertEqual(tree[0], "design")
        sub = tree[5]
        self.assertEqual((sub[0], sub[1], len(sub[2]), len(sub[4])), ("subdesign", "boole", 2, 2))
        self.assertEqual(sub[4][0], ("eq", ("name", "out1", None),
                                     ("bin", "&", ("name", "a1", None), ("un", "!", ("name", "a0", None)))))

    def test_registers_machines_and_instances_parse(self):
        text = """
        FUNCTION 4count (clk, clrn) RETURNS (qa, qb);
        SUBDESIGN s ( clk, d[3..0] : INPUT; q[3..0] : OUTPUT; io : BIDIR; )
        VARIABLE ff[3..0] : DFF;  sm : MACHINE OF BITS (z1, z0) WITH STATES (s0 = 0, s1, s2);
                 c : 4count;  alias : MACHINE;
        BEGIN
            ff[].clk = clk;  ff[].d = d[];  q[] = ff[].q;
            sm.(clk, reset) = (clk, GND);
            (qa, ) = 4count(clk, , );
            io = TRI(DFF(io, clk, , ), d0);
        END;
        """
        sub = ahdl.parse(text)[5]
        self.assertEqual([v[1][0] for v in sub[3]], ["prim", "machine", "instance", "machine"])
        self.assertEqual(len(sub[4]), 6)

    def test_errors_carry_a_line_number(self):
        with self.assertRaises(ahdl.AhdlError) as ctx:
            ahdl.parse("SUBDESIGN x ( a : INPUT; )\nBEGIN\n  a = ;\nEND;")
        self.assertIn("line 3", str(ctx.exception))

    @unittest.skipUnless(TUTORIAL.exists(), "vendor files not extracted")
    def test_every_tutorial_design_parses(self):
        files = sorted(TUTORIAL.glob("*/*.tdf"))
        self.assertGreater(len(files), 30)
        for path in files:
            ahdl.parse(path.read_text(encoding="latin1"))


class Evaluate(unittest.TestCase):
    def test_equations_feed_each_other(self):
        d = ahdl_eval.Design(BOOLE)
        self.assertEqual(d.evaluate(a0=0, a1=1, b=0), {"out1": 1, "out2": 1})
        self.assertEqual(d.evaluate(a0=1, a1=1, b=0), {"out1": 0, "out2": 0})
        self.assertRaises(ValueError, d.evaluate, a0=1)

    def test_if_chain(self):
        d = ahdl_eval.Design(PRIORITY)
        level = lambda **k: d.evaluate(**k)["level1"] * 2 + d.evaluate(**k)["level0"]
        self.assertEqual([level(high=h, middle=m, low=l) for h, m, l in ((0, 0, 0), (0, 0, 1), (0, 1, 1), (1, 1, 1))],
                         [0, 1, 2, 3])

    def test_case_defaults_constants_and_groups(self):
        d = ahdl_eval.Design(DECODE)
        out = d.evaluate(code=5)
        self.assertEqual((out["hit"], out["odd"], out["zero"], out["sel1"], out["sel0"]), (1, 1, 0, 1, 1))
        out = d.evaluate(code=0)
        self.assertEqual((out["hit"], out["odd"], out["zero"], out["sel1"], out["sel0"]), (0, 0, 1, 0, 0))
        out = d.evaluate(code=2)
        self.assertEqual((out["sel1"], out["sel0"]), (0, 1))

    def test_truth_table(self):
        d = ahdl_eval.Design(TABLE)
        y = lambda a, en: d.evaluate(a=a, en=en)["y1"] * 2 + d.evaluate(a=a, en=en)["y0"]
        self.assertEqual([y(0, 1), y(2, 1), y(3, 1), y(1, 1), y(3, 0)], [3, 1, 1, 0, 2])

    def test_registers_need_the_simulator(self):
        text = "SUBDESIGN r ( clk, d : INPUT; q : OUTPUT; ) VARIABLE f : DFF; BEGIN f.clk = clk; f.d = d; q = f.q; END;"
        self.assertRaises(ahdl_eval.Unsupported, ahdl_eval.Design(text).evaluate, clk=0, d=0)

    def test_names_are_case_insensitive_and_unknown_names_are_errors(self):
        text = "SUBDESIGN c ( Enable, D : INPUT; Q : OUTPUT; ) BEGIN q = ENABLE & d; END;"
        self.assertEqual(ahdl_eval.Design(text).evaluate(enable=1, d=1), {"q": 1})
        typo = "SUBDESIGN c ( enable, d : INPUT; q : OUTPUT; ) BEGIN q = enabel & d; END;"
        self.assertRaises(ahdl_eval.Unsupported, ahdl_eval.Design(typo).evaluate, enable=1, d=1)

    def test_instances_are_refused(self):
        text = "SUBDESIGN r ( a : INPUT; q : OUTPUT; ) VARIABLE u : other; BEGIN u.x = a; q = u.y; END;"
        self.assertRaises(ahdl_eval.Unsupported, ahdl_eval.Design, text)


COUNTER = """
SUBDESIGN counter
( clk, ena, clr, load, d[3..0] : INPUT;  q[3..0], top : OUTPUT; )
VARIABLE count[3..0] : DFFE;
BEGIN
    count[].clk = clk;
    count[].ena = ena;
    count[].clrn = !clr;
    IF load THEN count[].d = d[];
    ELSE count[].d = count[].q + 1;
    END IF;
    q[] = count[];
    top = (count[] == 15);
END;
"""
TOGGLE = """
SUBDESIGN toggle ( clk, t : INPUT; q : OUTPUT; )
VARIABLE f : TFF;
BEGIN f.clk = clk; f.t = t; q = f; END;
"""
LIGHTS = """
SUBDESIGN lights ( clk, reset, go : INPUT; red, green : OUTPUT; )
VARIABLE sm : MACHINE OF BITS (b1, b0) WITH STATES (stop, ready, run);
BEGIN
    sm.clk = clk;
    sm.reset = reset;
    CASE sm IS
        WHEN stop  => red = VCC; IF go THEN sm = ready; END IF;
        WHEN ready => red = VCC; green = VCC; sm = run;
        WHEN run   => green = VCC; IF !go THEN sm = stop; END IF;
    END CASE;
END;
"""


class Simulate(unittest.TestCase):
    def q(self, sim):
        return sum(sim.outputs()["q%d" % i] << i for i in range(4))

    def test_counter_counts_loads_holds_and_clears(self):
        sim = ahdl_eval.Simulator(COUNTER)
        sim.set(ena=1)
        sim.clock(cycles=5)
        self.assertEqual(self.q(sim), 5)
        sim.set(ena=0)
        sim.clock(cycles=3)
        self.assertEqual(self.q(sim), 5)                         # enable low: holds
        sim.set(ena=1, load=1, d=14)
        sim.clock()
        self.assertEqual(self.q(sim), 14)
        sim.set(load=0)
        sim.clock()
        self.assertEqual((self.q(sim), sim.outputs()["top"]), (15, 1))
        sim.clock()
        self.assertEqual(self.q(sim), 0)                         # wraps
        sim.clock(cycles=3)
        sim.set(clr=1)
        self.assertEqual(self.q(sim), 0)                         # asynchronous clear, no clock
        sim.set(clr=0)

    def test_registers_take_the_value_from_before_the_edge(self):
        sim = ahdl_eval.Simulator(COUNTER)
        sim.set(ena=1, load=1, d=9)
        sim.set(clk=1, d=3)                                      # d changes with the edge
        self.assertEqual(self.q(sim), 9)

    def test_toggle(self):
        sim = ahdl_eval.Simulator(TOGGLE)
        sim.set(t=1)
        self.assertEqual([sim.clock()["q"] for _ in range(4)], [1, 0, 1, 0])
        sim.set(t=0)
        self.assertEqual(sim.clock()["q"], 0)

    def test_inline_register_and_mixed_group(self):
        text = """
        SUBDESIGN inl ( clk, a, b : INPUT; q, n, w[1..0] : OUTPUT; )
        BEGIN
            q = DFF(a & b, clk, VCC, VCC);
            n = EXP(LCELL(a));
            w[] = (0, a) + (0, b);
        END;
        """
        sim = ahdl_eval.Simulator(text)
        sim.set(a=1, b=1)
        self.assertEqual(sim.outputs(), {"q": 0, "n": 0, "w1": 1, "w0": 0})
        sim.clock()
        self.assertEqual(sim.outputs()["q"], 1)
        sim.set(b=0)
        self.assertEqual((sim.outputs()["w1"], sim.outputs()["w0"]), (0, 1))
        sim.clock()
        self.assertEqual(sim.outputs()["q"], 0)

    def test_hierarchy_is_flattened(self):
        library = {
            "half": "SUBDESIGN half ( a, b : INPUT; s, c : OUTPUT; ) BEGIN s = a $ b; c = a & b; END;",
            "bit": """SUBDESIGN bit ( clk, d : INPUT; en : INPUT = VCC; q : OUTPUT; )
                      VARIABLE r : DFFE; BEGIN r.clk = clk; r.ena = en; r.d = d; q = r; END;""",
        }
        top = """
        FUNCTION half (a, b) RETURNS (s, c);
        SUBDESIGN top ( clk, u, v, w : INPUT; sum, carry, held : OUTPUT; )
        VARIABLE h1 : half;  keep : bit;
        BEGIN
            h1.a = u;  h1.b = v;
            (sum, carry) = half(h1.s, w);          % second adder as an inline call %
            keep.(clk, d) = (clk, h1.c);           % en left unconnected: defaults to VCC %
            held = keep.q;
        END;
        """
        sim = ahdl_eval.Simulator(top, library.get)
        sim.set(u=1, v=1, w=1)
        self.assertEqual(sim.outputs(), {"sum": 1, "carry": 0, "held": 0})
        sim.clock()
        self.assertEqual(sim.outputs()["held"], 1)
        sim.set(u=0)
        self.assertEqual((sim.outputs()["sum"], sim.outputs()["carry"]), (0, 1))
        self.assertRaises(ahdl_eval.Unsupported, ahdl_eval.Simulator, top, lambda name: None)

    def test_tristate_and_bidirectional_pins(self):
        text = """
        SUBDESIGN port ( clk, oe, wr : INPUT; x : INPUT; io : BIDIR; seen, y : OUTPUT; )
        VARIABLE r : DFFE;
        BEGIN
            r.clk = clk;  r.ena = wr;  r.d = io;       % latch what is on the pin %
            io = TRI(r.q, oe);                         % drive it back when enabled %
            seen = io;
            y = x;                                     % a signal really named x %
        END;
        """
        sim = ahdl_eval.Simulator(text)
        sim.set(io=1, wr=1, x=1)
        self.assertEqual(sim.outputs(), {"io": 1, "seen": 1, "y": 1})      # pin follows the outside
        sim.clock()
        sim.set(io=0, wr=0)
        self.assertEqual(sim.outputs()["io"], 0)                           # still not driving
        sim.set(oe=1)
        self.assertEqual((sim.outputs()["io"], sim.outputs()["seen"]), (1, 1))   # now the register drives it
        sim.set(oe=0)
        self.assertEqual(sim.outputs()["io"], 0)

    def test_include_prototypes_and_conditional_generate(self):
        library = {
            "pick.inc": "FUNCTION pick (sel, a, b) RETURNS (y);",
            "pick": """PARAMETERS ( DEVICE_FAMILY );
                       SUBDESIGN pick ( sel, a, b : INPUT; y : OUTPUT; )
                       VARIABLE
                           IF (FAMILY_FLEX() == 1) GENERATE  inner : flex_only;
                           ELSE GENERATE                     n : NODE;
                           END GENERATE;
                       BEGIN
                           IF (USED(sel)) GENERATE  n = sel & a # !sel & b;  END GENERATE;
                           y = n;
                       END;""",
        }
        top = """INCLUDE "pick";
                 SUBDESIGN t ( s, p, q : INPUT; o : OUTPUT; ) BEGIN o = pick(s, p, q); END;"""
        design = ahdl_eval.Design(top, library.get)
        self.assertEqual([design.evaluate(s=a, p=b, q=c)["o"] for a, b, c in ((1, 1, 0), (1, 0, 1), (0, 0, 1), (0, 1, 0))],
                         [1, 0, 1, 0])

    def test_state_machine(self):
        sim = ahdl_eval.Simulator(LIGHTS)
        self.assertEqual((sim.state("sm"), sim.outputs()), ("stop", {"red": 1, "green": 0}))
        sim.clock()
        self.assertEqual(sim.state("sm"), "stop")                # no assignment: stays
        sim.set(go=1)
        sim.clock()
        self.assertEqual((sim.state("sm"), sim.outputs()), ("ready", {"red": 1, "green": 1}))
        sim.clock()
        self.assertEqual((sim.state("sm"), sim.outputs()), ("run", {"red": 0, "green": 1}))
        sim.clock()
        self.assertEqual(sim.state("sm"), "run")
        sim.set(reset=1)
        self.assertEqual(sim.state("sm"), "stop")                # asynchronous reset to the first state


if __name__ == "__main__":
    unittest.main()


class Hierarchy(unittest.TestCase):
    LIB = {
        "busreg": "SUBDESIGN busreg ( clk, oe : INPUT; io : BIDIR; ) BEGIN io = TRI(DFF(io, clk, , ), oe); END;",
        "counter3": """SUBDESIGN counter3 ( clk, reset, go : INPUT; s : MACHINE OUTPUT; )
                       VARIABLE m : MACHINE WITH STATES (a, b, c);
                       BEGIN s = m; m.(clk, reset) = (clk, reset);
                       CASE m IS WHEN a => IF go THEN m = b; END IF;
                                 WHEN b => IF go THEN m = c; END IF;
                                 WHEN c => IF go THEN m = a; END IF; END CASE; END;""",
        "is_c": "SUBDESIGN is_c ( s : MACHINE INPUT; y : OUTPUT; ) BEGIN y = (s == c); END;",
    }

    def resolve(self, name):
        return self.LIB.get(name.lower())

    def test_bidir_port_of_an_instance_is_the_pin(self):
        from apu import ahdl_eval
        text = """FUNCTION busreg (clk, oe) RETURNS (io);
                  SUBDESIGN top ( clk, oe : INPUT; p : BIDIR; ) BEGIN p = busreg(clk, oe); END;"""
        sim = ahdl_eval.Simulator(text, self.resolve)
        sim.set(oe=0, p=1)
        sim.set(clk=1)
        sim.set(clk=0, p=0)
        self.assertEqual(sim.env["p"], 0)                  # not driving: the outside value
        sim.set(oe=1)
        self.assertEqual(sim.env["p"], 1)                  # driving back what it captured

    def test_tri_variable_drives_pins(self):
        from apu import ahdl_eval, synth
        text = """SUBDESIGN t ( a[1..0], e[1..0] : INPUT; y[1..0] : OUTPUT; )
                  VARIABLE b[1..0] : TRI;
                  BEGIN b[].in = a[]; b[].oe = e[]; y[] = b[].out; END;"""
        d = ahdl_eval.Design(text)
        self.assertEqual(d.tri, {"y1", "y0"})
        net = synth.synthesise(text)
        self.assertEqual(net.outputs["y1"].text(), "a1")
        self.assertEqual(net.enables["y0"].text(), "e0")

    def test_machine_passed_between_designs(self):
        from apu import ahdl_eval
        text = """FUNCTION counter3 (clk, reset, go) RETURNS (MACHINE s);
                  FUNCTION is_c (MACHINE s) RETURNS (y);
                  SUBDESIGN top ( clk, reset, go : INPUT; y : OUTPUT; )
                  VARIABLE m : MACHINE;
                  BEGIN m = counter3(clk, reset, go); y = is_c(m); END;"""
        sim = ahdl_eval.Simulator(text, self.resolve)
        seen = []
        sim.set(go=1)
        for _ in range(4):
            sim.set(clk=1)
            sim.set(clk=0)
            seen.append(sim.env["y"])
        self.assertEqual(seen, [0, 1, 0, 0])               # a -> b -> c -> a -> b

    def test_machine_input_alone_is_explained(self):
        from apu import ahdl_eval
        with self.assertRaisesRegex(ahdl_eval.Unsupported, "connected to no state machine"):
            ahdl_eval.Design(self.LIB["is_c"])

    def test_machine_output_at_the_top_is_its_state_bits(self):
        from apu import ahdl_eval
        sim = ahdl_eval.Simulator(self.LIB["counter3"])
        sim.set(go=1)
        sim.set(clk=1)
        self.assertEqual([sim.env[p] for p in sim.design.groups["s"]], [0, 1])   # state b = 1
