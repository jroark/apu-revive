"""Derive a MAX 7000S device's fuse tables from Quartus-compiled probes (tools/s7k_probes.py).

    python tools/s7k_derive.py DEVICE [--write]

Reads vendor/quartus-13.0sp1/out/<kind><tag>_NN and prints what it finds; --write stores
apu/data/<tag>.json for apu.s7k. Steps (the EPM7064S was decoded by hand the same way,
re/protocol.md 2026-11-26/27):
  routing   x probes: an output copying an input has one literal at block input m; the single
            programmed fuse of field m XOR 3 names the pin's (field, slot). z probes: the same
            for macrocell feedbacks, the stages located by the fit report.
  options   assumed to follow the EPM7064S layout (apu.epm7064s.OPTION_ROLES); checked here on
            unused macrocells and later by co-simulation.
  pins      fuses after the routing area that follow "pin is an output" (a pair) and "pin is
            unused"; the global-clock-unused fuse; the constant rest.
  OE lines  e probes: each tri-state pin's 3-bit selector, the fuse connecting each line to its
            source (an OE pin or the macrocell computing the enable) and each line's invert fuse.
"""
import collections
import itertools
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from apu import epm7064s, pof  # noqa: E402
from s7k_probes import device  # noqa: E402

OUT = ROOT / "vendor/quartus-13.0sp1/out"
ROW = re.compile(r";\s*[A-Z]\s*;\s*LC(\d+)\s*;\s*([^;]*?)\s*;\s*([^;]*?)\s*;")


def designs(dev, kind):
    out = []
    for d in sorted(OUT.glob("%s%s_*" % (kind, dev["tag"]))):
        p = d / (d.name + ".pof")
        if p.exists():
            out.append({"name": d.name, "fuses": pof.logical_fuses(pof.Pof.from_bytes(p.read_bytes())),
                        "tdf": (d / (d.name + ".tdf")).read_text(), "rpt": (d / (d.name + ".fit.rpt")).read_text(),
                        "pins": {n: int(p_) for p_, n in re.findall(r"PIN_(\d+) -to (\S+)", (d / (d.name + ".qsf")).read_text())}})
    return out


def geometry(dev):
    fm = dev["fm"]
    option_base = fm.macrocells * 440
    route_base = option_base + 13 * fm.macrocells
    blocks = fm.macrocells // 16
    return option_base, route_base, blocks


def field_width(dev, xs):
    """The spacing of routing fields, from the programmed routing fuses of the x probes."""
    option_base, route_base, blocks = geometry(dev)
    fm = dev["fm"]
    mc_of_pin = {p: m for m, p in fm.pin_of.items()}
    for w in range(4, 33):
        ok = total = 0
        for x in xs[:6]:
            for o, i in re.findall(r"\to(\d+) = i(\d+);", x["tdf"]):
                mc = mc_of_pin[int(o)]
                cols = [c for t in fm.product_terms(x["fuses"], mc) if isinstance(t, list) for c in t]
                if len(cols) != 1:
                    continue
                base = route_base + w * (36 * ((mc - 1) // 16) + ((cols[0] // 2) ^ 3))
                total += 1
                if base + w <= len(x["fuses"]):
                    ok += sum(1 for s in range(w) if x["fuses"][base + s] == 0) == 1
        if total and ok == total:
            return w
    raise SystemExit("no field width fits the x probes")


def routing(dev, xs, zs, w):
    option_base, route_base, blocks = geometry(dev)
    fm = dev["fm"]
    mc_of_pin = {p: m for m, p in fm.pin_of.items()}
    pin, mcell, odd = collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter), 0

    def place(fuses, mc):
        cols = [c for t in fm.product_terms(fuses, mc) if isinstance(t, list) for c in t]
        if len(cols) != 1:
            return None
        field = (cols[0] // 2) ^ 3
        base = route_base + w * (36 * ((mc - 1) // 16) + field)
        hits = [s for s in range(w) if fuses[base + s] == 0]
        return (field, hits[0]) if len(hits) == 1 else None
    for x in xs:
        for o, i in re.findall(r"\to(\d+) = i(\d+);", x["tdf"]):
            pl = place(x["fuses"], mc_of_pin[int(o)])
            if pl:
                pin[int(i)][pl] += 1
            else:
                odd += 1
    for z in zs:
        feeds = {b: a for a, b in re.findall(r"(s\d+)\.clk = clk; \1\.d = (\w+);", z["tdf"])}   # source -> stage
        home, fin_of = {}, {}
        for lc, fin, fout in ROW.findall(z["rpt"]):
            srcs = [s.strip() for s in fin.split(",") if s.strip() != "clk"]
            if len(srcs) == 1 and srcs[0] in feeds:
                home[feeds[srcs[0]]] = int(lc)
                fin_of[int(lc)] = srcs[0]
        for lc, src in fin_of.items():
            if src in home:
                pl = place(z["fuses"], lc)
                if pl:
                    mcell[home[src]][pl] += 1
    return ({p: sorted(c) for p, c in pin.items()}, {m: sorted(c) for m, c in mcell.items()}, odd)


def paired_routing(dev, all_designs, known):
    """The EPM7128S-style routing (apu.s7k.PERIOD): partner tables from the architecture file,
    the period layout checked against every design, and the source on each tap from literals
    whose signal is certain (`known`: designs of kinds x, u, v, z)."""
    from apu import maf, s7k
    from s7k_probes import DEVICES, K
    option_base, route_base, blocks = geometry(dev)
    fm = dev["fm"]
    entries = maf.parse((K / (DEVICES[dev["name"]][2] + ".maf")).read_bytes()).entries
    right, left = {}, {}
    for e in entries:
        if e.type == 4:
            t = entries[e.targets[1]].index
            (right if e.index // 36 < blocks // 2 else left)[t % 36] = e.index % 36
    sel_q = {s7k.PERIOD.index(4 + f): f for f in range(4)}
    mc_of_pin = {p: m for m, p in fm.pin_of.items()}

    def regions(fz):
        mux, sel = {}, {b: set() for b in range(blocks)}
        for b in range(blocks):
            base = route_base + 504 * b
            for z in (f - base for f in range(base, base + 504) if fz[f] == 0):
                if z % 56 in sel_q:
                    sel[b].add(4 * (z // 56) + sel_q[z % 56])
                else:
                    mux.setdefault((b, 4 * (z // 56) + s7k.PERIOD[z % 56]), []).append(z)
        return mux, sel
    explained = unexplained = 0
    for d in all_designs:
        mux, sel = regions(d["fuses"])
        used = {b: {(c // 2) ^ 3 for mc in range(16 * b + 1, 16 * b + 17) for t in fm.product_terms(d["fuses"], mc)
                    if isinstance(t, list) for c in t if c not in fm.expander_of} for b in range(blocks)}
        for b in range(blocks):
            o = blocks - 1 - b
            needs = {i for i in used[b] if i not in sel[b]} | {(right if b < blocks // 2 else left)[i] for i in used[o] if i in sel[o]}
            got = {f: z for (bb, f), z in mux.items() if bb == b}
            ok = set(got) == needs and all(len(z) == 1 for z in got.values())
            explained += ok
            unexplained += not ok
    taps, bad = collections.defaultdict(collections.Counter), 0
    for d in known:
        fz = d["fuses"]
        mux, sel = regions(fz)
        lits = {}
        if d["name"][0] in "xuv":
            for o, i in re.findall(r"\to(\d+) = !?i(\d+);", d["tdf"]):
                mc = mc_of_pin[int(o)]
                cols = [c for t in fm.product_terms(fz, mc) if isinstance(t, list) for c in t]
                if len(cols) == 1:
                    lits[((mc - 1) // 16, (cols[0] // 2) ^ 3)] = "pin%s" % i
        else:
            feeds = {b_: a for a, b_ in re.findall(r"(s\d+)\.clk = clk; \1\.d = (\w+);", d["tdf"])}
            home, fin_of = {}, {}
            for lc, fin, fout in ROW.findall(d["rpt"]):
                srcs = [x.strip() for x in fin.split(",") if x.strip() != "clk"]
                if len(srcs) == 1 and srcs[0] in feeds:
                    home[feeds[srcs[0]]] = int(lc)
                    fin_of[int(lc)] = srcs[0]
            for lc, src in fin_of.items():
                cols = [c for t in fm.product_terms(fz, lc) if isinstance(t, list) for c in t]
                if len(cols) == 1 and (src in home or src == "din"):
                    lits[((lc - 1) // 16, (cols[0] // 2) ^ 3)] = "mc%d" % home[src] if src in home else "pin%d" % d["pins"]["din"]
        for (b, i), src in lits.items():
            key = (blocks - 1 - b, (right if b >= blocks // 2 else left)[i]) if i in sel[b] else (b, i)
            z = mux.get(key, [])
            if len(z) == 1:
                taps[z[0]][src] += 1
            else:
                bad += 1
    conflicts = {o: dict(c) for o, c in taps.items() if len(c) > 1}
    by_source = collections.defaultdict(list)
    for o, c in taps.items():
        by_source[c.most_common(1)[0][0]].append(o)
    return {"partner_right": [right[i] for i in range(36)], "partner_left": [left[i] for i in range(36)],
            "taps": {k: sorted(v) for k, v in by_source.items()}}, explained, unexplained, bad, conflicts


def infer_pin_taps(dev, os_, tables):
    """More taps from the random-logic probes: an unknown tap read by an output's macrocell must
    carry one of the input pins in that output's equations (cells whose known sources are not all
    in their own equations host another cell's expander terms and are skipped). Returns
    {source: [offsets]} for taps with exactly one candidate over at least two uses."""
    from apu import s7k
    T = s7k.Tables(tables)
    fm = dev["fm"]
    mc_of_pin = {p: m for m, p in fm.pin_of.items()}

    def tap_of(fz, block, field):
        if not fz[T.region(block) + T.selector_offset[field]]:
            block, field = T.partner(block, field)
        base, p, f = T.region(block), field // 4, field % 4
        hits = [56 * p + q for q in range(56) if s7k.PERIOD[q] == f and not fz[base + 56 * p + q]]
        return hits[0] if len(hits) == 1 else None
    cand, uses = {}, collections.Counter()
    for d in os_:
        fz, tdf = d["fuses"], d["tdf"]
        for o in re.findall(r"\n\to(\d+) = ", tdf):
            mc = mc_of_pin[int(o)]
            lines = re.findall(r"\t(?:o%s|r%s\.\w+) = ([^;]*);" % (o, o), tdf)
            sigs = {"pin%s" % p for line in lines for p in re.findall(r"\bi(\d+)\b", line)}
            fields = {(c // 2) ^ 3 for t in fm.product_terms(fz, mc) if isinstance(t, list) for c in t if c not in fm.expander_of}
            known, unknown = set(), []
            for f in fields:
                t = tap_of(fz, (mc - 1) // 16 + 1, f)
                if t is not None:
                    (known.add(T.TAP_SOURCE[t]) if t in T.TAP_SOURCE else unknown.append(t))
            if known <= sigs:
                for t in unknown:
                    cand[t] = cand.get(t, sigs - known) & (sigs - known)
                    uses[t] += 1
    out = collections.defaultdict(list)
    for t, c in cand.items():
        if len(c) == 1 and uses[t] >= 2:
            out[next(iter(c))].append(t)
    return out


def option_fuse(dev, mc, role):
    option_base = geometry(dev)[0]
    return option_base + 104 * ((mc - 1) // 8) + epm7064s.OPTION_ROLES[(mc - 1) % 8][role]


def option_word(dev, fuses, mc):
    return {r: fuses[option_fuse(dev, mc, r)] for r in epm7064s.OPTION_ROLES[0]}


def check_options(dev, ds):
    unused = tuple(sorted({"t0": 1, "t1": 1, "t2": 1, "t3": 1, "t4": 0, "clk": 1, "gclr": 0, "nused": 1, "byp": 0,
                           "inv": 0, "T": 0, "T2": 0, "gck": 1}.items()))
    good = bad = 0
    for d in ds:
        used = {int(lc) for lc, _, _ in ROW.findall(d["rpt"])}
        for mc in range(1, dev["fm"].macrocells + 1):
            w = tuple(sorted(option_word(dev, d["fuses"], mc).items()))
            if mc in used:
                good += dict(w)["nused"] == 0
                bad += dict(w)["nused"] != 0
            else:
                good += w == unused
                bad += w != unused
    return good, bad


def pin_fuses(dev, ds, w):
    """Per-pin output-enable pair and unused fuse, the global-clock fuse and the constants."""
    option_base, route_base, blocks = geometry(dev)
    first = route_base + 36 * blocks * w
    n = dev["fm"].fuses
    sig = collections.defaultdict(list)
    for f in range(first, n):
        sig[tuple(d["fuses"][f] for d in ds)].append(f)

    def outputs(d):
        names = set(re.findall(r"\w+", re.search(r"\n\s*([^\n:]*): OUTPUT", d["tdf"]).group(1)))
        return {p for nme, p in d["pins"].items() if nme in names}
    outs = [outputs(d) for d in ds]
    oe, unused, problems = {}, {}, []
    for p in dev["io"]:
        a = sig.get(tuple(int(p in o) for o in outs), [])
        u = sig.get(tuple(int(p not in d["pins"].values()) for d in ds), [])
        if len(a) == 2 and a[1] == a[0] + 1 and len(u) == 1:
            oe[p], unused[p] = a[0], u[0]
        else:
            problems.append((p, a, u))
    gck = sig.get(tuple(int(not re.search(r"\.clk = clk;", d["tdf"])) for d in ds), [])
    known = set(oe.values()) | {f + 1 for f in oe.values()} | set(unused.values()) | {f + 1 for f in unused.values()} | set(gck)
    zeros = [f for f in range(first, n) if f not in known and all(d["fuses"][f] == 0 for d in ds)]
    varying = [f for f in range(first, n) if f not in known and len({d["fuses"][f] for d in ds}) > 1]
    return oe, unused, gck, zeros, varying, problems, first


def oe_lines(dev, es, oe, unused, tables_for_sim):
    """Selector code -> (invert fuse, {source fuse: source}) from the tri-state probes."""
    from apu import fusesim, s7k
    fm = dev["fm"]
    mc_of_pin = {p: m for m, p in fm.pin_of.items()}
    _, _, oe1, oe2 = dev["ded"]
    first = tables_for_sim["first_pin_fuse"]
    known = set(oe.values()) | {f + 1 for f in oe.values()} | set(unused.values()) | {f + 1 for f in unused.values()}
    uses = collections.defaultdict(set)          # (code, source) -> designs
    polarity = collections.defaultdict(dict)     # code -> design -> True (enables while source high)
    codes_always = collections.Counter()
    T = s7k.Tables(tables_for_sim)
    for e in es:
        fz = e["fuses"]
        ens = {int(p): x for p, x in re.findall(r"t(\d+)\.oe = ([^;]*);", e["tdf"])}
        owner = {}                                 # output pin -> macrocell computing its enable, if any
        for lc, fin, fout in ROW.findall(e["rpt"]):
            for o in map(int, re.findall(r"\bo(\d+)\b", fout)):
                if mc_of_pin.get(o) != int(lc) and o in ens:
                    owner[o] = int(lc)
        dev_sim = fusesim.Device(s7k.FuseMap(fm, T), fz)
        names = ("a", "b", "c", "e", "g1", "g2")
        for o, expr in ens.items():
            code = (fz[unused[o] + 1], fz[oe[o]], fz[oe[o] + 1])
            py = expr.replace("!", " not ").replace("&", " and ")
            if o in owner:
                src = owner[o]
                same = 0
                for vals in itertools.product((0, 1), repeat=6):
                    env = dict(zip(names, vals))
                    dev_sim.set(**{"p%d" % e["pins"][nme]: v for nme, v in env.items()})
                    same += dev_sim.value[src] == int(bool(eval(py, {}, env)))
                if same not in (0, 64):
                    continue
                polarity[code][e["name"]] = same == 64
            else:
                pin_name = "g1" if "g1" in expr else "g2"
                src = "pin%d" % (oe1 if pin_name == "g1" else oe2)
                polarity[code][e["name"]] = not expr.strip().startswith("!")
            uses[(code, src)].add(e["name"])
        for o in map(int, re.findall(r"\to(\d+) = [^;]*;", e["tdf"])):
            if o not in ens:
                codes_always[(fz[unused[o] + 1], fz[oe[o]], fz[oe[o] + 1])] += 1
    all_names = {e["name"] for e in es}
    by_name = {e["name"]: e["fuses"] for e in es}
    area = [f for f in range(first, dev["fm"].fuses) if f not in known]
    lines, unresolved = {}, []
    for (code, src), ds in sorted(uses.items(), key=lambda kv: str(kv[0])):
        exact = [f for f in area if {nme for nme in all_names if by_name[nme][f] == 0} == ds]
        if len(exact) == 1:
            lines.setdefault(code, [None, {}])[1][exact[0]] = src
        else:
            unresolved.append((code, src, sorted(ds), exact))
    source_fuses = {f for inv, srcs in lines.values() for f in srcs}
    for code, pol in polarity.items():
        cands = [f for f in area if f not in source_fuses
                 and all(by_name[nme][f] == (0 if high else 1) for nme, high in pol.items())
                 and all(by_name[nme][f] == 1 for nme in all_names - set(pol))]
        if code in lines and len(cands) == 1:
            lines[code][0] = cands[0]
        else:
            unresolved.append((code, "invert", cands[:6]))
    return lines, unresolved, codes_always


def main():
    dev = device(sys.argv[1])
    xs, zs, os_, es = (designs(dev, k) for k in "xzoe")
    print("designs: x %d, z %d, o %d, e %d" % (len(xs), len(zs), len(os_), len(es)))
    option_base, route_base, blocks = geometry(dev)
    us, vs = designs(dev, "u"), designs(dev, "v")
    paired = None
    try:
        w = field_width(dev, xs)
    except SystemExit:
        w = 14
        paired, explained, unexplained, bad, conflicts = paired_routing(dev, xs + zs + os_ + es + us + vs, xs + us + vs + zs)
    print("option base %d, routing base %d, field width %d%s" % (option_base, route_base, w, " (paired routing)" if paired else ""))
    if paired:
        pin, mcell = {}, {}
        srcs = paired["taps"]
        print("paired routing: %d (design, block) tap sets explained, %d not; %d taps known (%d pins, %d macrocells); "
              "%d literals unresolved, %d conflicting taps" % (
                  explained, unexplained, sum(len(v) for v in srcs.values()), sum(k.startswith("pin") for k in srcs),
                  sum(k.startswith("mc") for k in srcs), bad, len(conflicts)))
    else:
        pin, mcell, odd = routing(dev, xs, zs, w)
        claimed = collections.Counter(pl for t in (pin, mcell) for places in t.values() for pl in places)
        print("routing: %d pins, %d macrocells, %d places, %d claimed twice, %d odd observations" % (
            len(pin), len(mcell), len(claimed), sum(1 for v in claimed.values() if v > 1), odd))
        print("  pins missing:", sorted((set(dev["io"]) | set(dev["ded"])) - set(pin)))
        print("  macrocells missing:", [m for m in range(1, dev["fm"].macrocells + 1) if m not in mcell])
    good, bad = check_options(dev, xs + zs + os_)
    print("options (EPM7064S layout): %d macrocells as predicted, %d not" % (good, bad))
    oe, unused, gck, zeros, varying, problems, first = pin_fuses(dev, xs + zs + os_ + us + vs, w)
    print("pin fuses: %d output-enable pairs, %d unused fuses, global-clock fuse %s, %d constant zeros, %d unexplained varying"
          % (len(oe), len(unused), gck, len(zeros), len(varying)))
    if problems:
        print("  pins without a clean match:", problems[:6])
    tables = {"name": dev["name"], "part": dev["part"], "daf": None, "maf": None, "fuses": dev["fm"].fuses,
              "macrocells": dev["fm"].macrocells, "option_base": option_base, "route_base": route_base, "field_width": w,
              "first_pin_fuse": first, "jtag_pins": list(dev["jtag"]), "dedicated": list(dev["ded"]),
              "pin": {str(p): v for p, v in pin.items()}, "macrocell": {str(m): v for m, v in mcell.items()},
              "pin_oe": {str(p): f for p, f in oe.items()}, "pin_unused": {str(p): f for p, f in unused.items()},
              "gclk_unused": gck[0] if len(gck) == 1 else None, "constant_zeros": zeros, "oe_lines": {}}
    if paired:
        tables.update(routing="paired", **paired)
        more = infer_pin_taps(dev, os_, tables)
        for src, offs in more.items():
            tables["taps"][src] = sorted(set(tables["taps"].get(src, [])) | set(offs))
        print("  %d more taps inferred from the random-logic probes; %d taps in all" % (
            sum(len(v) for v in more.values()), sum(len(v) for v in tables["taps"].values())))
    from s7k_probes import DEVICES
    tables["daf"], tables["maf"] = DEVICES[dev["name"]][1], DEVICES[dev["name"]][2]
    if es and not problems:
        lines, unresolved, always = oe_lines(dev, es, oe, unused, tables)
        print("OE lines: %d codes; always-on code(s) %s" % (len(lines), dict(always)))
        for code, (inv, srcs) in sorted(lines.items()):
            print("  %s invert %s sources %s" % ("".join(map(str, code)), inv, srcs))
        if unresolved:
            print("  unresolved:", unresolved[:8])
        tables["oe_lines"] = {"".join(map(str, c)): [inv, {str(f): s for f, s in srcs.items()}] for c, (inv, srcs) in lines.items()}
        used = {f for inv, srcs in lines.values() for f in list(srcs) + [inv]}
        tables["constant_zeros"] = [f for f in zeros if f not in used]
    if "--write" in sys.argv:
        path = ROOT / "apu/data" / (dev["tag"] + ".json")
        path.write_text(json.dumps(tables, indent=0))
        print("wrote", path)


if __name__ == "__main__":
    main()
