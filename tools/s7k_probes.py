"""Quartus probe designs for decoding a MAX 7000S device's fuse map.

    python tools/s7k_probes.py DEVICE KIND OUTDIR [COUNT]

DEVICE: a key of DEVICES (e.g. EPM7128S). KIND:
  x  each output pin copies one input pin (pin -> routing place)
  z  a shift register through almost every macrocell (macrocell feedback -> routing place)
  o  random mixes of logic and register features (option fuses, co-simulation)
  e  tri-state outputs with every kind of enable (output-enable fuses and the global OE lines)
Compile them with tools/quartus_compile.py; tools/s7k_derive.py reads the results.
"""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

# name -> (Quartus part, device file, architecture file, JTAG pins, dedicated pins gclk gclr oe1 oe2)
DEVICES = {
    "EPM7064S": ("EPM7064SLC44-10", "s7064044", "m7064s", (7, 13, 32, 38), (43, 1, 44, 2)),
    "EPM7128S": ("EPM7128SLC84-15", "s7128084", "m7128s", (14, 23, 62, 71), (83, 1, 84, 2)),
}
K = pathlib.Path(__file__).resolve().parent.parent / "vendor/maxplus2-10.2/7k"


def device(name):
    from apu import daf, fusemap, maf
    part, d, m, jtag, ded = DEVICES[name]
    fm = fusemap.FuseMap(daf.parse((K / (d + ".daf")).read_bytes()), maf.parse((K / (m + ".maf")).read_bytes()))
    io = sorted(p for p in fm.pin_of.values() if p not in jtag)
    return {"name": name, "part": part, "fm": fm, "io": io, "jtag": jtag, "ded": ded,
            "tag": name[3:].lower()}                       # file prefix, e.g. 7128s


def _acf(name, part, pins):
    lines = ["CHIP %s" % name, "BEGIN", "\tDEVICE = %s;" % part]
    lines += ["\t|%s : %s_PIN = %d;" % (n, "OUTPUT" if n.startswith("o") or n in ("dout",) else "INPUT", p) for n, p in pins]
    return "\n".join(lines + ["END;"]) + "\n"


def gen_x(dev, out, count):
    io, ded = dev["io"], list(dev["ded"])
    n_in = max(9, len(io) // 3)
    for k in range(count):
        r = random.Random(1000 + k)
        pins = io[:]
        r.shuffle(pins)
        ins = pins[:n_in] + r.sample(ded, 2)
        outs = pins[n_in:]
        name = "x%s_%02d" % (dev["tag"], k)
        srcs = [ins[i % len(ins)] for i in range(len(outs))]
        r.shuffle(srcs)
        tdf = ["SUBDESIGN %s" % name, "( " + ", ".join("i%d" % p for p in ins) + " : INPUT;",
               "  " + ", ".join("o%d" % p for p in outs) + " : OUTPUT; )", "BEGIN"]
        tdf += ["\to%d = i%d;" % (o, s) for o, s in zip(outs, srcs)] + ["END;"]
        (out / (name + ".tdf")).write_text("\n".join(tdf) + "\n")
        (out / (name + ".acf")).write_text(_acf(name, dev["part"], [("i%d" % p, p) for p in ins] + [("o%d" % p, p) for p in outs]))


def gen_z(dev, out, count):
    n = dev["fm"].macrocells - 4
    for k in range(count):
        r = random.Random(9000 + k)
        order = list(range(n))
        r.shuffle(order)
        name = "z%s_%02d" % (dev["tag"], k)
        tdf = ["SUBDESIGN %s" % name, "( clk, din : INPUT;", "  dout : OUTPUT; )", "VARIABLE",
               "\t" + ", ".join("s%d" % j for j in range(n)) + " : DFF;", "BEGIN"]
        prev = "din"
        for j in order:
            tdf.append("\ts%d.clk = clk; s%d.d = %s;" % (j, j, prev))
            prev = "s%d" % j
        tdf += ["\tdout = %s;" % prev, "END;"]
        a, b = r.sample(dev["io"], 2)
        (out / (name + ".tdf")).write_text("\n".join(tdf) + "\n")
        (out / (name + ".acf")).write_text(_acf(name, dev["part"], [("clk", dev["ded"][0]), ("din", a), ("dout", b)]))


def gen_o(dev, out, count):
    io, (gclk, gclr, _, _) = dev["io"], dev["ded"]
    n_in = max(10, len(io) // 3)
    for k in range(count):
        r = random.Random(7000 + k)
        pins = io[:]
        r.shuffle(pins)
        ins, outs = pins[:n_in], pins[n_in:n_in + min(len(io) - n_in, 2 * n_in)]
        name = "o%s_%02d" % (dev["tag"], k)

        def lit():
            return ("!" if r.random() < 0.4 else "") + "i%d" % r.choice(ins)

        def term():
            return " & ".join(lit() for _ in range(r.randint(1, 3)))

        def sop(nmax=6):
            return " # ".join("(%s)" % term() for _ in range(r.randint(1, nmax)))
        var, body = [], []
        for o in outs:
            kind = r.choice(["comb", "ncomb", "xor", "wide", "dff", "dff", "tff", "dffe", "ptclk"])
            if kind == "comb":
                body.append("\to%d = %s;" % (o, sop()))
            elif kind == "ncomb":
                body.append("\to%d = !(%s);" % (o, sop()))
            elif kind == "xor":
                body.append("\to%d = (%s) $ (%s);" % (o, sop(2), sop(2)))
            elif kind == "wide":
                body.append("\to%d = %s;" % (o, " # ".join("(%s)" % term() for _ in range(r.randint(6, 12)))))
            else:
                rt = {"dff": "DFF", "tff": "TFF", "dffe": "DFFE", "ptclk": "DFF"}[kind]
                var.append("\tr%d : %s;" % (o, rt))
                body.append("\tr%d.clk = %s;" % (o, term() if kind == "ptclk" else "clk"))
                body.append("\tr%d.%s = %s;" % (o, "t" if rt == "TFF" else "d", sop(4)))
                if rt == "DFFE":
                    body.append("\tr%d.ena = %s;" % (o, term()))
                c = r.choice(["none", "none", "gclr", "pt"])
                if c == "gclr":
                    body.append("\tr%d.clrn = gclrn;" % o)
                elif c == "pt":
                    body.append("\tr%d.clrn = !(%s);" % (o, term()))
                if r.random() < 0.3:
                    body.append("\tr%d.prn = !(%s);" % (o, term()))
                body.append("\to%d = r%d;" % (o, o))        # no output inversion: Quartus would push it back
        tdf = ["SUBDESIGN %s" % name, "( clk, gclrn, " + ", ".join("i%d" % p for p in ins) + " : INPUT;",
               "  " + ", ".join("o%d" % p for p in outs) + " : OUTPUT; )"]
        if var:
            tdf += ["VARIABLE"] + var
        tdf += ["BEGIN"] + body + ["END;"]
        (out / (name + ".tdf")).write_text("\n".join(tdf) + "\n")
        (out / (name + ".acf")).write_text(_acf(name, dev["part"], [("clk", gclk), ("gclrn", gclr)] + [("i%d" % p, p) for p in ins]
                                                + [("o%d" % p, p) for p in outs]))


def gen_e(dev, out, count):
    io, (_, _, oe1, oe2) = dev["io"], dev["ded"]
    kinds_all = ["oe1", "oe2", "pt", "ptn", "pin", "always", "noe1", "noe2"]
    for k in range(count):
        r = random.Random(300 + k)
        pins = io[:]
        r.shuffle(pins)
        a, b, c, e = pins[:4]
        outs = pins[4:4 + min(16, len(io) - 4)]
        pool = ["always"] + r.sample([x for x in kinds_all if x != "always"], 5)   # the chip has six OE lines
        kinds = [r.choice(pool) for _ in outs]
        name = "e%s_%02d" % (dev["tag"], k)
        var = ["\tt%d : TRI;" % o for o, kd in zip(outs, kinds) if kd != "always"]
        body = []
        for o, kd in zip(outs, kinds):
            src = r.choice(["a", "b", "c", "!a", "a & b"])
            if kd == "always":
                body.append("\to%d = %s;" % (o, src))
                continue
            en = {"oe1": "g1", "oe2": "g2", "noe1": "!g1", "noe2": "!g2", "pt": "b & c", "ptn": "!(a & c)", "pin": "e"}[kd]
            body.append("\tt%d.in = %s; t%d.oe = %s; o%d = t%d.out;" % (o, src, o, en, o, o))
        tdf = ["SUBDESIGN %s" % name, "( a, b, c, e, g1, g2 : INPUT;", "  " + ", ".join("o%d" % o for o in outs) + " : OUTPUT; )",
               "VARIABLE"] + var + ["BEGIN"] + body + ["END;"]
        (out / (name + ".tdf")).write_text("\n".join(tdf) + "\n")
        (out / (name + ".acf")).write_text(_acf(name, dev["part"], [("a", a), ("b", b), ("c", c), ("e", e), ("g1", oe1), ("g2", oe2)]
                                                + [("o%d" % o, o) for o in outs]))


if __name__ == "__main__":
    dev = device(sys.argv[1])
    out = pathlib.Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    {"x": gen_x, "z": gen_z, "o": gen_o, "e": gen_e}[sys.argv[2]](dev, out, int(sys.argv[4]) if len(sys.argv) > 4 else 12)
    print(len(list(out.glob("%s%s_*.tdf" % (sys.argv[2], dev["tag"])))), "probes in", out)
