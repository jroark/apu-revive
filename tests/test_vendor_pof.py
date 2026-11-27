"""Checks against POFs compiled by MAX+PLUS II 10.2 itself (probes/ designs, compiled in a VM;
the outputs are local only, in vendor/vm_link/inbox)."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
K = ROOT / "vendor/maxplus2-10.2/7k"
INBOX = ROOT / "vendor/vm_link/inbox"


@unittest.skipUnless((INBOX / "p08_wide_or.pof").exists() and K.exists(), "vendor-compiled POFs not present")
class VendorEpm7032(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from apu import daf, fusemap, maf
        cls.fm = fusemap.FuseMap(daf.parse((K / "m7032044.daf").read_bytes()), maf.parse((K / "m7032.maf").read_bytes()))

    def fuses(self, name):
        from apu import pof
        p = pof.Pof.from_bytes((INBOX / (name + ".pof")).read_bytes())
        self.assertTrue(p.crc_ok)
        fuses = pof.logical_fuses(p)
        self.assertEqual(len(fuses), 14672)
        rebuilt = pof.fuses_to_logical_pof(fuses, p.device)
        self.assertEqual([x for t, x in rebuilt.packets if t == 17], [x for t, x in p.packets if t == 17])
        return fuses

    def terms(self, fuses, mc):
        fm = self.fm
        names = fm.block_inputs(fuses, (mc - 1) // 16 + 1)
        out = []
        for t in fm.product_terms(fuses, mc):
            if t is None or t == "all":
                out.append(None if t is None else "GND")
            else:
                out.append({("exp%d" % fm.expander_of[c]) if c in fm.expander_of
                            else ("" if c % 2 else "!") + names[c // 2] for c in t})
        return out

    def test_combinational_literals_and_routing(self):
        f = self.fuses("p01_comb")                         # a, b, c on the pins of macrocells 1-3
        self.assertEqual(self.terms(f, 17)[1], {"pin1", "pin2"})               # y_and = a & b
        self.assertEqual(self.terms(f, 18)[1], {"pin1", "pin2", "pin3"})       # y_nand3, inverted
        self.assertEqual(self.terms(f, 19)[1], {"!pin1", "!pin2"})             # y_or = !(!a & !b)

    def test_wide_or_with_shared_expanders(self):
        f = self.fuses("p08_wide_or")                      # i0 on macrocell 1's pin, i11 on 2's, ...
        pins = {0: 1, 11: 2, 10: 3, 9: 4, 8: 5, 7: 6, 6: 7, 5: 8, 4: 9, 3: 10, 2: 11, 1: 12}
        name = {i: "pin%d" % mc for i, mc in pins.items()}
        y = self.terms(f, 17)
        for pair in ((8, 9), (6, 7), (4, 5), (2, 3)):
            self.assertIn({name[pair[0]], name[pair[1]]}, y)
        self.assertEqual(self.terms(f, 31)[0], {"!" + name[0], "!" + name[10]})   # EXP(!i0 & !i10)
        self.assertEqual(self.terms(f, 32)[0], {"!" + name[1], "!" + name[11]})   # EXP(!i1 & !i11)

    def test_every_routed_input_of_the_routing_probes(self):
        from apu import acf
        for name in ("r01_b1_to_b2", "r02_b2_to_b1"):
            f = self.fuses(name)
            pins = acf.pins(acf.parse((INBOX / (name + ".fit")).read_text(encoding="latin1")))
            pin_mc = {p: mc for mc, p in self.fm.pin_of.items()}
            for k in range(16):
                src, out = pin_mc[pins["din%d" % k]], pin_mc[pins["dout%d" % k]]
                self.assertEqual(self.terms(f, out)[1], {"pin%d" % src}, (name, k))

    def test_option_elements(self):
        opts = lambda f, mc: self.fm.option_bits(f, mc)
        f = self.fuses("p07_tri")
        self.assertEqual([opts(f, mc)[34] for mc in (1, 2, 3)], [0, 0, 0])    # input pins: driver off
        self.assertEqual(opts(f, 19)[34], 1)               # y_goe: enabled from the global OE pin
        self.assertEqual(opts(f, 20)[34], 2)               # y_ptoe: from the second OE pin
        f = self.fuses("p01_comb")
        self.assertEqual([opts(f, mc)[31] for mc in (17, 18, 19)], [3, 3, 3])  # combinational
        self.assertEqual([opts(f, mc)[6] for mc in (17, 18, 19)], [0, 1, 1])   # inverted outputs
        self.assertEqual(opts(self.fuses("p02_dff_gclk"), 17)[31], 2)          # registered
        self.assertEqual(opts(self.fuses("p04_tff"), 17)[32], 1)               # T flip-flop
        unused = [opts(f, mc) for mc in range(4, 17)]
        self.assertTrue(all(u == unused[0] for u in unused))                   # odd and even alike


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless((INBOX / "chiptrip.pof").exists() and K.exists(), "vendor-compiled POFs not present")
class FuseSimulatorAgainstVendor(unittest.TestCase):
    """The fuse-level simulator, run on each vendor POF, behaves like the source design."""

    def test_every_vendor_design(self):
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        from fusesim_check import check
        names = sorted(p.stem for p in INBOX.glob("*.pof") if (ROOT / "probes" / (p.stem + ".tdf")).exists() or p.stem == "chiptrip")
        self.assertGreaterEqual(len(names), 10)
        for name in names:
            checks, mismatches = check(name, steps=150, verbose=False)
            self.assertGreater(checks, 0, name)
            self.assertEqual(mismatches, [], name)
