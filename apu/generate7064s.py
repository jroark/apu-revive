"""EPM7064S (44-pin) programming data from a synthesised, fitted design.

The same construction as apu.generate (the EPM7032), on the EPM7064S fuse model in apu.epm7064s,
which was checked by co-simulating 106 Quartus II 13.0 SP1 designs (re/protocol.md,
2026-11-27): product terms in the architecture file's layout, block inputs selected through
the observed (field, slot) places, the 13 option roles per macrocell, two output-enable fuses
per pin, one "pin unused" fuse per pin, the global-clock-unused fuse and the constant rest.
Tri-state outputs: an enable that is an input on pin 44 or pin 2 drives its global OE line
directly (either polarity, one per pin); any other enable (up to five product terms) is computed
by a free macrocell that can drive a free global OE line (apu.epm7064s.OE_LINES), as Quartus
does. Shared expanders are refused.
"""
from . import epm7064s as _EPM7064S
from .generate import CannotGenerate, _complement, _single_term

JTAG_PINS = _EPM7064S.JTAG_PINS           # TDI, TMS, TCK, TDO: JTAG stays enabled
GCLK_PIN, GCLR_PIN = 43, 1


def _roles_from_word(w, kind):
    """apu.generate's EPM7032 word p0..p11 -> EPM7064S option roles."""
    r = {"t0": w[7], "t1": w[6], "t2": w[5], "t3": w[4], "t4": w[3], "clk": w[2], "gclr": w[1],
         "nused": 1 - w[8], "byp": w[9], "inv": w[10], "T": w[11], "T2": 0, "gck": 1}
    if kind == "reg":
        r["gck"] = 0 if w[2] else 1                  # a product-term clock clears gck
    else:
        r["clk"] = 1                                 # unclocked cells read clk = 1 (Quartus does)
    if kind == "lender":                             # T2 alone in an unclocked cell lends the OR up
        r.update(T2=1, T=0, clk=1, gck=1, byp=0, nused=0)
    return r


UNUSED_ROLES = {"t0": 1, "t1": 1, "t2": 1, "t3": 1, "t4": 0, "clk": 1, "gclr": 0, "nused": 1,
                "byp": 0, "inv": 0, "T": 0, "T2": 0, "gck": 1}


def compile_netlist(net, part, sevenk_dir, fixed, base, tables=None):
    """Fit and generate, refitting with a smaller share for any block whose inputs cannot all be
    routed (each source reaches a block at only one or two fixed places). Returns (fuses, fitted)."""
    import re
    from . import fit as fit_module, project
    caps = [fit_module.BLOCK_SIZE] * (base.macrocells // fit_module.BLOCK_SIZE)
    last = None
    for _ in range(4 * fit_module.BLOCK_SIZE):
        fitted = project.fit_part(net, part, sevenk_dir, fixed, caps)
        try:
            return generate(net, fitted.result, fitted.placement, fitted.pins, base, tables), fitted
        except CannotGenerate as e:
            m = re.match(r"block (\d+) cannot route", str(e))
            if not m or caps[int(m.group(1)) - 1] <= 1:
                raise
            caps[int(m.group(1)) - 1] -= 1
            last = e
    raise last


def generate(net, fitted, placement, pins, base, tables=None):
    """base: the device's architecture-file FuseMap (apu.fusemap; product-term layout). tables:
    an apu.s7k.Tables for another MAX 7000S device (default: the EPM7064S, apu.epm7064s)."""
    E = tables or _EPM7064S
    d = net.design
    fuses = [1] * base.fuses
    for f in E.CONSTANT_ZEROS:
        fuses[f] = 0
    mc_of_pin = {p: m for m, p in base.pin_of.items()}
    cell_mc = dict(placement)
    reg_mc = {bit: cell_mc[bit] for bit in net.registers}
    for name, p in pins.items():
        if p in E.JTAG_PINS:
            raise CannotGenerate("%s is on pin %d, a JTAG pin" % (name, p))

    def places(signal):
        if signal.endswith(".q") and signal[:-2] in reg_mc:
            return E.MACROCELL[reg_mc[signal[:-2]]]
        if signal in cell_mc:
            return E.MACROCELL[cell_mc[signal]]
        if signal.endswith("@ext") and signal[:-4] in pins:  # a bidirectional pin's outside value
            signal = signal[:-4]
        if signal in pins:
            if pins[signal] not in E.PIN:
                raise CannotGenerate("%s is on pin %d, which has no input path" % (signal, pins[signal]))
            return E.PIN[pins[signal]]
        raise CannotGenerate("no source for signal %s" % signal)

    # -- what each macrocell implements (as apu.generate) -------------------------------------
    cells = {}
    for c in fitted.cells:
        mc = cell_mc[c.name]
        if c.lenders and (mc - 1) // 8 != (mc - 1 - c.lenders) // 8:
            raise CannotGenerate("%s's parallel expanders would cross a chain of 8" % c.name)
        if c.name in net.registers:
            eqs = net.registers[c.name]
            cells[mc] = {"kind": "reg", "data": eqs.get("d") or eqs["t"], "toggle": "t" in eqs, "eqs": eqs, "cell": c}
        elif c.name in net.outputs:
            cells[mc] = {"kind": "comb", "data": net.outputs[c.name], "cell": c}
        else:
            cells[mc] = {"kind": "comb", "data": net.nodes[c.name], "cell": c}

    # -- output enables: OE pins straight onto their lines; anything else is computed by a free
    #    macrocell that can drive a free global OE line (as Quartus does) ----------------------
    lender_mcs = {cell_mc[c.name] - 1 - k for c in fitted.cells for k in range(c.lenders)}
    busy = set(cells) | lender_mcs
    pin_lines, enable_line, mc_lines = {}, {}, {}     # line code -> (active high, source fuse, invert fuse)
    by_eq = {}
    for name, eq in net.enables.items():
        single = len(eq.terms) == 1 and len(eq.terms[0]) == 1
        if single:
            (signal, value), = eq.terms[0].items()
            line = E.OE_PIN_LINES.get(pins.get(signal))
            high = (value == 1) != eq.invert
            if line and pin_lines.get(line[0], (high,))[0] == high:
                pin_lines[line[0]] = (high, line[1], line[2])
                enable_line[name] = line[0]
                continue
        by_eq.setdefault(eq.text(), []).append(name)
    for text, names in by_eq.items():
        eq = net.enables[names[0]]
        if len(eq.terms) > 5:
            raise CannotGenerate("the enable of %s needs %d product terms; an enable cell has 5" % (names[0], len(eq.terms)))
        choice = None
        for code, (inv, sources) in E.OE_LINES.items():
            if code in pin_lines or code in mc_lines:
                continue
            for f, src in sources.items():
                if isinstance(src, int) and src not in busy:
                    choice = (code, f, src)
                    break
            if choice:
                break
        if choice is None:
            raise CannotGenerate("no free global OE line with a free source macrocell for the enable of %s" % names[0])
        code, f, mc = choice
        busy.add(mc)
        mc_lines[code] = (f, mc)
        oe_name = names[0] + "~oe"
        cells[mc] = {"kind": "comb", "data": eq, "cell": _EnableCell(oe_name)}
        cell_mc[oe_name] = mc
        for n in names:
            enable_line[n] = code

    terms_of, roles = {}, {}
    uses_gclk = False
    for mc, cell in cells.items():
        w = [0] * 12
        slots = [None] * 5
        special = {}
        if cell["kind"] == "reg":
            eqs, c = cell["eqs"], cell["cell"]
            clk = c.special.get("clk")
            if clk == "term":
                slots[3] = _single_term(eqs["clk"], "clock of " + c.name)
                special[3], w[2] = True, 1
            else:
                uses_gclk = True
            clr = c.special.get("clrn")
            if clr and clr.startswith("global:"):
                w[1] = 1
            elif clr == "term":
                slots[2] = _single_term(_complement(eqs["clrn"]), "clear of " + c.name)
                special[2] = True
            if "prn" in eqs:
                slots[4] = _single_term(_complement(eqs["prn"]), "preset of " + c.name)
                special[4] = True
            w[8], w[9], w[11] = 1, 0, 1 if cell["toggle"] else 0
        else:
            w[8], w[9] = 1, 1
        eq = cell["data"]
        order = [s for s in (1, 2, 3, 4, 0) if s not in special]
        lenders = cell["cell"].lenders
        if len(eq.terms) > len(order) + 5 * lenders:
            raise CannotGenerate("%s needs %d product terms; %d slots are free" % (cell["cell"].name, len(eq.terms), len(order) + 5 * lenders))
        if any(not t for t in eq.terms):
            raise CannotGenerate("%s has a constant-true product term" % cell["cell"].name)
        own, rest = eq.terms[:len(order)], eq.terms[len(order):]
        used = set()
        for term, slot in zip(own, order):
            slots[slot] = dict(term)
            used.add(slot)
        for k in range(lenders):
            lender = mc - 1 - k
            part, rest = rest[:5], rest[5:]
            lw = [0] * 12
            lw[8], lw[9] = 1, 0
            lslots = [None] * 5
            for term, slot in zip(part, (1, 2, 3, 4, 0)):
                lslots[slot] = dict(term)
            for slot, bit in ((0, 7), (1, 6), (2, 5), (3, 4), (4, 3)):
                lw[bit] = 0 if lslots[slot] is not None else 1
            terms_of[lender], roles[lender] = lslots, _roles_from_word(lw, "lender")
        w[10] = 1 if eq.invert else 0
        for slot, bit in ((0, 7), (1, 6), (2, 5), (3, 4), (4, 3)):
            w[bit] = 0 if slot in used else 1
        if cell["kind"] == "reg":
            for slot in (2, 4):
                if slot not in special and slot not in used:
                    slots[slot] = "GND"
        terms_of[mc], roles[mc] = slots, _roles_from_word(w, cell["kind"])

    # -- routing: every block's signals onto its 36 inputs --------------------------------------
    m_of = {}
    needed_by_block = {}
    for block in range(1, base.macrocells // 16 + 1):
        needed = []
        for mc, slots in terms_of.items():
            if (mc - 1) // 16 + 1 == block:
                for t in slots:
                    if isinstance(t, dict):
                        needed += [s for s in t if s not in needed]
        needed_by_block[block] = needed
    if getattr(E, "routing", "flat") == "paired":
        def key(signal):                                   # the tables' name for a signal's source
            if signal.endswith(".q") and signal[:-2] in reg_mc:
                return "mc%d" % reg_mc[signal[:-2]]
            if signal in cell_mc:
                return "mc%d" % cell_mc[signal]
            if signal.endswith("@ext") and signal[:-4] in pins:
                signal = signal[:-4]
            if signal in pins:
                return "pin%d" % pins[signal]
            raise CannotGenerate("no source for signal %s" % signal)
        try:
            m_of, program = E.route({b: [(s, key(s)) for s in sigs] for b, sigs in needed_by_block.items()})
        except ValueError as e:
            raise CannotGenerate(str(e))
        for f in program:
            fuses[f] = 0
    else:
        for block, needed in needed_by_block.items():
            taken = _match(block, [(s, places(s)) for s in needed])
            m_of[block] = {sig: m for m, (sig, _) in taken.items()}
            for m, (sig, (field, slot)) in taken.items():
                fuses[E.field_fuse(block, field, slot)] = 0

    # -- product terms ----------------------------------------------------------------------------
    for mc, slots in terms_of.items():
        block = (mc - 1) // 16 + 1
        start0 = base.starts[mc - 1]
        for t, term in enumerate(slots):
            start = start0 + t * base.inputs
            if term == "GND":
                fuses[start:start + base.inputs] = [0] * base.inputs
            elif isinstance(term, dict):
                for signal, value in term.items():
                    fuses[start + 2 * m_of[block][signal] + (1 if value else 0)] = 0

    # -- options, pins, globals ---------------------------------------------------------------------
    for mc in range(1, base.macrocells + 1):
        for role, v in roles.get(mc, UNUSED_ROLES).items():
            fuses[E.option_fuse(mc, role)] = v
    used_pins = set(pins.values())
    outputs = {pins[n] for n in d.outputs if n in pins}
    selector = {p: E.OE_ALWAYS if p in outputs and mc_of_pin.get(p) in cells else E.OE_NEVER for p in E.PIN_OE}
    for name, code in enable_line.items():
        selector[pins[name]] = code
    for code, (high, src, inv) in pin_lines.items():
        fuses[src], fuses[inv] = 0, 0 if high else 1
    for code, (f, mc) in mc_lines.items():
        inv, _ = E.OE_LINES[code]
        fuses[f], fuses[inv] = 0, 0               # enabled while the enable cell's output is 1
    for p, f in E.PIN_OE.items():
        fuses[E.PIN_UNUSED[p] + 1], fuses[f], fuses[f + 1] = selector[p]
    for p, f in E.PIN_UNUSED.items():
        fuses[f] = 0 if p in used_pins else 1
    fuses[E.GLOBAL_CLOCK_UNUSED] = 0 if uses_gclk else 1
    return fuses


class _EnableCell:
    """A macrocell the generator adds to compute an output enable."""
    def __init__(self, name):
        self.name, self.lenders, self.special = name, 0, {}


def _match(block, signals):
    """{block input m: (signal, (field, slot))}; each place (field, slot) serves input field ^ 3."""
    options = {sig: [(field ^ 3, (field, slot)) for field, slot in pl] for sig, pl in signals}
    taken = {}

    def place(sig, seen):
        for m, pl in options[sig]:
            if m in seen:
                continue
            seen.add(m)
            if m not in taken or place(taken[m][0], seen):
                taken[m] = (sig, pl)
                return True
        return False
    for sig in sorted(options, key=lambda s: len(options[s])):
        if not place(sig, set()):
            raise CannotGenerate("block %d cannot route all of its %d input signals" % (block, len(signals)))
    return taken
