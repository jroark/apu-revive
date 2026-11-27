"""Co-simulate vendor-compiled EPM7032 POFs against their source designs.

    python tools/fusesim_check.py [design ...]      (default: every probe in vendor/vm_link/inbox)

For each design: the source (probes/<name>.tdf, or the tutorial's chiptrip) runs in
apu.ahdl_eval.Simulator; the vendor's POF runs in apu.fusesim.Device, pins mapped by the
vendor's .fit. Random input changes; every output pin is compared whenever it is driven.
"""
import itertools
import pathlib
import random
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from apu import acf, ahdl_eval, daf, fusemap, fusesim, maf, pof  # noqa: E402

K = ROOT / "vendor/maxplus2-10.2/7k"
INBOX = ROOT / "vendor/vm_link/inbox"
VENDOR = ROOT / "vendor/maxplus2-10.2"


def source(name):
    if name == "chiptrip":
        d = VENDOR / "MAX+plus_II_Tutorial/chiptrip"
        return (d / "chiptrip.tdf").read_text(encoding="latin1"), ahdl_eval.directory_resolver(d, *ahdl_eval.vendor_library(VENDOR / "MAX+plus_II_BASELINE"))
    return (ROOT / "probes" / (name + ".tdf")).read_text(encoding="latin1"), None


def epm7032():
    return fusemap.FuseMap(daf.parse((K / "m7032044.daf").read_bytes()), maf.parse((K / "m7032.maf").read_bytes()))


def check(name, steps=400, seed=1, expander_source=None, verbose=True):
    fm = epm7032()
    fuses = pof.logical_fuses(pof.Pof.from_bytes((INBOX / (name + ".pof")).read_bytes()))
    pins = acf.pins(acf.parse((INBOX / (name + ".fit")).read_text(encoding="latin1")))
    text, resolver = source(name)
    return cosim(name, text, resolver, fm, fuses, pins, steps, seed, expander_source, verbose)


def cosim(name, text, resolver, fm, fuses, pins, steps=400, seed=1, expander_source=None, verbose=True,
          power_up=None):
    """Design simulator vs fuse simulator on `fuses`, ports on `pins`. power_up: {flop bit: value}
    to start the design simulator's registers in (a compiler that pushes an output inversion into
    a register makes the chip power up with the opposite output)."""
    dev = fusesim.Device(fm, fuses, expander_source)
    sim = ahdl_eval.Simulator(text, resolver)
    for bit, v in (power_up or {}).items():
        sim.q[bit] = v
    d = sim.design
    ins = [i for i in d.inputs if i in pins]
    outs = [o for o in d.outputs if o in pins]
    rng = random.Random(seed)
    mismatches, checks = [], 0

    # MAX+PLUS II inserts pin pairs "x~1" (output) / "x~2" (input) that the board must wire
    # together, e.g. to bring a generated output enable back in on a dedicated OE pin
    wires = [(pins[n], pins[n[:-1] + "2"]) for n in pins if n.endswith("~1") and n[:-1] + "2" in pins]

    def apply(values):
        sim.set(**values)
        dev.set(**{"p%d" % pins[k]: v for k, v in values.items()})
        for _ in range(3):
            dev.set(**{"p%d" % dst: dev.pin_value(src) for src, dst in wires})

    apply({i: 0 for i in ins})
    for step in range(steps):
        i = rng.choice(ins)
        apply({i: 1 - sim.inputs.get(i, 0)})
        for o in outs:
            mc = dev.mc_of_pin.get(pins[o])
            if mc and not dev.driving(mc):
                continue
            checks += 1
            want, got = sim.env.get(o, 0), dev.pin_value(pins[o])
            if want != got:
                mismatches.append((step, o, want, got))
    if verbose:
        print("%-14s %5d checks, %d mismatches %s" % (name, checks, len(mismatches), mismatches[:3]))
    return checks, mismatches


if __name__ == "__main__":
    names = sys.argv[1:] or sorted(p.stem for p in INBOX.glob("*.pof"))
    for n in names:
        try:
            check(n)
        except Exception as e:
            print("%-14s error: %s: %s" % (n, type(e).__name__, e))
