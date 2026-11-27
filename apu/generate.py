"""EPM7032 programming data from a synthesised, fitted design.

Input: a synth.Netlist, its fit.Fit, and fit.assign_pins' placement and pin assignment.
Output: the device's 14672 JEDEC fuses, ready for pof.fuses_to_logical_pof or jedec.write.
The encoding is the one apu.fusesim simulates, which was checked against POFs compiled by
MAX+PLUS II 10.2 (re/protocol.md, 2026-11-26).

Each macrocell's terms are placed in slots 1, 2, 3, 4, 0 (skipping those its register needs for
a product-term clear (slot 2), clock or clock enable (3) or preset (4)); used slots go into the
OR, the rest are steered to their special inputs. Unused clear and preset terms of a register
are programmed to GND, as the vendor's compiler does. A cell with more terms than slots takes
the rest from the macrocells just below it (the fitter's lenders), each lending its OR upward
through the parallel-expander chain (p0). Tri-state outputs are handled when enabled by an
input on OE pin 44 or 2 while it is low (the pins are active low); other enables are refused.
"""
from . import fusemap as fusemap_module

UNUSED_WORD = (0, 0, 1, 0, 1, 1, 1, 1, 0, 0, 0, 0)      # every unused cell in the vendor's POFs
GCLK_PIN, GCLR_PIN = 43, 1


class CannotGenerate(Exception):
    pass


def _word_fuses(fm, mc):
    """The twelve option fuses of a macrocell in word order (p0..p11)."""
    rows = {eid: rows[mc] for eid, _, rows in fm.options if mc in rows}
    return [f for eid in (24, 25, 26, 27, 28, 1, 29, 30, 31, 6, 32) for f in rows[eid]]


def generate(net, fitted, placement, pins, fm):
    d = net.design
    fuses = [1] * fm.fuses
    mc_of_pin = {p: m for m, p in fm.pin_of.items()}
    cell_mc = dict(placement)                           # cell name -> macrocell
    reg_mc = {bit: cell_mc[bit] for bit in net.registers}

    def source(signal):
        """The architecture file's name for where a signal enters the PIA."""
        if signal.endswith(".q") and signal[:-2] in reg_mc:
            return ("mc", reg_mc[signal[:-2]] - 1)
        if signal in cell_mc:
            return ("mc", cell_mc[signal] - 1)
        if signal.endswith("@ext") and signal[:-4] in pins:  # a bidirectional pin's outside value
            signal = signal[:-4]
        if signal in pins:
            mc = mc_of_pin.get(pins[signal])
            if not mc:
                from .fusesim import DEDICATED_INPUT
                dedicated = {pin: k for k, pin in DEDICATED_INPUT.items()}
                if pins[signal] not in dedicated:
                    raise CannotGenerate("%s is on pin %d, which has no input path" % (signal, pins[signal]))
                return ("hio", fm.macrocells + dedicated[pins[signal]])
            return ("hio", mc - 1)
        raise CannotGenerate("no source for signal %s" % signal)

    # -- what each macrocell implements -----------------------------------------------------
    cells = {}                                          # mc -> dict
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

    # -- terms and options -------------------------------------------------------------------
    terms_of = {}                                        # mc -> [slot term or None/"GND"] x5
    words = {}
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
        own, rest = eq.terms[:len(order)], eq.terms[len(order):]
        used = set()
        for term, slot in zip(own, order):
            slots[slot] = dict(term)
            used.add(slot)
        for k in range(lenders):                         # the macrocells just below lend their ORs
            lender = mc - 1 - k
            part, rest = rest[:5], rest[5:]
            lw = [0] * 12
            lw[0], lw[8], lw[9] = 1, 1, 1                 # p0: OR goes up to the next macrocell
            lslots = [None] * 5
            for term, slot in zip(part, (1, 2, 3, 4, 0)):
                lslots[slot] = dict(term)
            for slot, bit in ((0, 7), (1, 6), (2, 5), (3, 4), (4, 3)):
                lw[bit] = 0 if lslots[slot] is not None else 1
            terms_of[lender], words[lender] = lslots, lw
        if not eq.terms and not eq.invert:              # a constant 0: nothing in the OR
            pass
        w[10] = 1 if eq.invert else 0                   # output = OR(used) XOR p10
        # steering: 0 = into the OR, 1 = special (term 0 special = shared expander, unused)
        for slot, bit in ((0, 7), (1, 6), (2, 5), (3, 4), (4, 3)):
            w[bit] = 0 if slot in used else 1
        if cell["kind"] == "reg":
            for slot in (2, 4):                          # an unused clear or preset never fires
                if slot not in special and slot not in used:
                    slots[slot] = "GND"
        terms_of[mc], words[mc] = slots, w

    # -- routing -----------------------------------------------------------------------------
    block_signal_m = {}
    for block in range(1, fm.macrocells // 16 + 1):
        needed = []
        for mc, slots in terms_of.items():
            if (mc - 1) // 16 + 1 != block:
                continue
            for t in slots:
                if isinstance(t, dict):
                    needed += [s for s in t if s not in needed]
        assign = _match(fm, block, [(s, source(s)) for s in needed])
        block_signal_m[block] = assign
        for m in range(36):
            a = fm.routing_addresses[(block - 1) * 36 + m]
            v = assign.get(m)
            if v is None:
                fuses[a], fuses[a + 1] = 1, 1             # left erased
            else:
                fuses[a], fuses[a + 1] = v[1] & 1, v[1] >> 1

    # -- write terms --------------------------------------------------------------------------
    for mc, slots in terms_of.items():
        block = (mc - 1) // 16 + 1
        m_of = {sig: m for m, (sig, _) in block_signal_m[block].items()}
        base = fm.starts[mc - 1]
        for t, term in enumerate(slots):
            start = base + t * fm.inputs
            if term is None:
                continue
            if term == "GND":
                fuses[start:start + fm.inputs] = [0] * fm.inputs
                continue
            for signal, value in term.items():
                m = m_of[signal]
                fuses[start + 2 * m + (1 if value else 0)] = 0     # 2m + 1 true, 2m complement

    # -- option words and output drivers --------------------------------------------------------
    for mc in range(1, fm.macrocells + 1):
        w = words.get(mc, UNUSED_WORD)
        for f, bit in zip(_word_fuses(fm, mc), w):
            fuses[f] = bit
    enable = {mc_of_pin[p]: 3 for name, p in pins.items() if name in d.outputs and p in mc_of_pin}
    for name, eq in net.enables.items():
        # the OE pins are active low and have no inversion: only "enable while the pin is low"
        if len(eq.terms) != 1 or len(eq.terms[0]) != 1:
            raise CannotGenerate("the enable of %s is not a single input (only OE pins 44 and 2 are supported)" % name)
        (signal, value), = eq.terms[0].items()
        line = {44: 1, 2: 2}.get(pins.get(signal))
        if line is None:
            raise CannotGenerate("the enable of %s, %s, is not on OE pin 44 or 2" % (name, signal))
        if (value == 1) != eq.invert:
            raise CannotGenerate("the enable of %s is active high; the EPM7032's OE pins enable while low" % name)
        enable[mc_of_pin[pins[name]]] = line
    for eid, _, rows in fm.options:
        if eid != 34:
            continue
        for mc, fs in rows.items():
            value = enable.get(mc, 0)
            fuses[fs[0]], fuses[fs[1]] = value >> 1, value & 1
    return fuses


def _match(fm, block, signals):
    """{block input m: (signal, select value)} for every (signal, source); Raises
    CannotGenerate when the block's input multiplexers cannot take them all."""
    options = {}
    for sig, src in signals:
        options[sig] = [(m, v) for m in range(36) for v, s in fm.block_sources(block, m).items() if s == src]
        if not options[sig]:
            raise CannotGenerate("no input of block %d can select %s" % (block, sig))
    taken = {}                                          # m -> (signal, value)

    def place(sig, seen):
        for m, v in options[sig]:
            if m in seen:
                continue
            seen.add(m)
            if m not in taken or place(taken[m][0], seen):
                taken[m] = (sig, v)
                return True
        return False
    for sig in sorted(options, key=lambda s: len(options[s])):
        if not place(sig, set()):
            raise CannotGenerate("block %d cannot route all of its %d input signals" % (block, len(signals)))
    return taken


def _complement(eq):
    from .synth import Equation
    return Equation(eq.terms, not eq.invert)


def _single_term(eq, what):
    """The one product term an equation must be (clocks, clears and presets each get one)."""
    if eq.invert:
        # !(t1 # t2 ...) is a single term only when it is the complement of one literal
        if len(eq.terms) == 1 and len(eq.terms[0]) == 1:
            (s, v), = eq.terms[0].items()
            return {s: 1 - v}
        raise CannotGenerate("%s is not a single product term" % what)
    if len(eq.terms) != 1:
        raise CannotGenerate("%s is not a single product term" % what)
    return dict(eq.terms[0])
