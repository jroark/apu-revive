import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from apu import svf7064s  # noqa: E402

QOUT = ROOT / "vendor/quartus-13.0sp1/out"


def resolved(text):
    """SVF statements with MASK carried over as SVF defines it."""
    from jtag_svf_read import parse_svf
    out, masks = [], {}
    for c, n, f in parse_svf(text):
        f = dict(f)
        f.pop("text", None)
        if c == "SDR" and "TDO" in f:
            if "MASK" in f:
                masks[n] = f["MASK"]
            f["MASK"] = masks.get(n, (1 << n) - 1)
        out.append((c, n, tuple(sorted(f.items()))))
    return out


class Layout(unittest.TestCase):
    def test_every_fuse_once(self):
        seen = [f for _, w0, w1 in svf7064s.ROWS for f in w0 + w1 if not isinstance(f, tuple)]
        self.assertEqual(len(seen), 30608)
        self.assertEqual(sorted(seen), list(range(30608)))

    def test_jtag_enable_default(self):
        text = svf7064s.write([1] * 30609)
        self.assertIn("SIR 10 TDI (079);", text)
        off = svf7064s.write([1] * 30609, jtag_enabled=False)
        self.assertNotEqual(text, off)


@unittest.skipUnless((QOUT / "t02_cd_to_ab" / "verify.svf").exists(), "Quartus outputs not present (vendor/, not in git)")
class AgainstQuartus(unittest.TestCase):
    def check(self, name):
        from apu import pof
        d = QOUT / name
        fuses = pof.logical_fuses(pof.Pof.from_bytes((d / (name + ".pof")).read_bytes()))
        self.assertEqual(resolved(svf7064s.write(fuses, "program")), resolved((d / (name + ".svf")).read_text()), name)
        self.assertEqual(resolved(svf7064s.write(fuses, "verify")), resolved((d / "verify.svf").read_text()), name)

    def test_designs(self):
        for name in ("t02_cd_to_ab", "x00", "o05", "z00"):
            if (QOUT / name / "verify.svf").exists():
                self.check(name)


if __name__ == "__main__":
    unittest.main()
