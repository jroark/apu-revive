"""MAX 7000S devices described by tables (apu/data/<tag>.json, made by tools/s7k_derive.py from
Quartus-compiled probes): the fuse map for apu.fusesim and the names apu.generate7064s uses.

The model is the EPM7064S's (apu/epm7064s.py, re/protocol.md 2026-11-26/27): product terms in the
architecture file's layout; block input m selected by a one-hot field m XOR 3 of `field_width`
fuses; 13 option roles per macrocell in the EPM7064S's permutation; a 3-bit output-enable
selector per pin choosing always, never or one of six global OE lines.
"""
import json
import pathlib

from . import epm7064s

DATA = pathlib.Path(__file__).parent / "data"

# "Paired" routing (EPM7128S; re/protocol.md 2026-11-27). A block's 504 routing fuses are nine
# periods of 56; a period serves four block inputs ("fields" 4P .. 4P + 3; product-term columns
# 2m, 2m + 1 belong to field m XOR 3). PERIOD[q] says what position q of a period is: f = one of
# the 13 taps of field f's mux, 4 + f = field f's selector fuse. An input reads its own mux, or,
# with its selector programmed (0), one fixed mux of the partner block (block b pairs with
# 9 - b); a mux passes the one source whose tap is programmed. Solved from 276 Quartus designs:
# all 2208 (design, block) tap sets are explained exactly.
PERIOD = [0] * 8 + [1, 1, 4] + [0] * 5 + [1] * 8 + [2] * 4 + [5] + [1] * 3 + [2] * 8 + [3] * 6 + [6, 2, 7] + [3] * 7
assert len(PERIOD) == 56 and all(PERIOD.count(f) == 13 for f in range(4))


class Tables:
    OE_ALWAYS, OE_NEVER = (1, 1, 1), (1, 0, 0)
    OPTION_ROLES = epm7064s.OPTION_ROLES

    def __init__(self, t):
        self.name, self.part, self.daf, self.maf = t["name"], t["part"], t["daf"], t["maf"]
        self.OPTION_BASE, self.ROUTING_BASE, self.FIELD_WIDTH = t["option_base"], t["route_base"], t["field_width"]
        self.MACROCELL = {int(k): [tuple(p) for p in v] for k, v in t["macrocell"].items()}
        self.PIN = {int(k): [tuple(p) for p in v] for k, v in t["pin"].items()}
        self.PIN_OE = {int(k): v for k, v in t["pin_oe"].items()}
        self.PIN_UNUSED = {int(k): v for k, v in t["pin_unused"].items()}
        self.GLOBAL_CLOCK_UNUSED = t["gclk_unused"]
        self.CONSTANT_ZEROS = list(t["constant_zeros"])
        self.JTAG_PINS = tuple(t["jtag_pins"])
        self.dedicated_pins = tuple(t["dedicated"])                    # gclk, gclr, oe1, oe2
        gclk, gclr, oe1, oe2 = self.dedicated_pins
        self.DEDICATED = {gclk: 0, oe1: 1, gclr: 2, oe2: 3}            # pin -> apu.fusesim input number
        self.OE_LINES = {tuple(int(c) for c in code): (inv, {int(f): s for f, s in srcs.items()})
                         for code, (inv, srcs) in t.get("oe_lines", {}).items()}
        self.routing = t.get("routing", "flat")
        if self.routing == "paired":
            # input field i of a right-hand (left-hand) block can take this mux of its partner
            self.PARTNER_RIGHT, self.PARTNER_LEFT = list(t["partner_right"]), list(t["partner_left"])
            self.TAPS = {k: list(v) for k, v in t["taps"].items()}          # "pin4" / "mc17" -> offsets
            self.TAP_SOURCE = {o: k for k, v in self.TAPS.items() for o in v}
            self.blocks = t["macrocells"] // 16
            self.selector_offset = {4 * p + f: 56 * p + PERIOD.index(4 + f) for p in range(9) for f in range(4)}
        # OE pin -> (line code, source fuse, invert fuse)
        self.OE_PIN_LINES = {int(s[3:]): (code, f, inv) for code, (inv, srcs) in self.OE_LINES.items()
                             for f, s in srcs.items() if isinstance(s, str)}

    @classmethod
    def load(cls, tag):
        return cls(json.loads((DATA / (tag + ".json")).read_text()))

    def field_fuse(self, block, field, slot):
        return self.ROUTING_BASE + self.FIELD_WIDTH * (36 * (block - 1) + field) + slot

    # -- paired routing ---------------------------------------------------------------------------
    def region(self, block):
        return self.ROUTING_BASE + 504 * (block - 1)

    def partner(self, block, field):
        """(partner block, the partner's mux) a block input can take through its selector."""
        other = self.blocks + 1 - block
        return other, (self.PARTNER_RIGHT if block > self.blocks // 2 else self.PARTNER_LEFT)[field]

    def mux_source(self, fuses, block, field):
        """The source name on mux `field` of `block`: None (no tap), "multi", or a TAPS key / "?"."""
        base, p, f = self.region(block), field // 4, field % 4
        hits = [56 * p + q for q in range(56) if PERIOD[q] == f and not fuses[base + 56 * p + q]]
        if not hits:
            return None
        return self.TAP_SOURCE.get(hits[0], "?") if len(hits) == 1 else "multi"

    def input_source(self, fuses, block, field):
        if not fuses[self.region(block) + self.selector_offset[field]]:
            block, field = self.partner(block, field)
        return self.mux_source(fuses, block, field)

    def route(self, needs):
        """needs: {block: [(signal, source key)]} -> ({block: {signal: block input m}}, fuses to
        program). A signal reaches a block input through the block's own mux (a tap in the
        block's region) or through the partner block's paired mux (a tap in the partner's region
        plus the input's selector fuse). A mux carries one source. Raises ValueError."""
        m_of, program = {b: {} for b in needs}, []
        used = {}                                                     # (block, mux field) -> source
        for block in sorted(needs):
            other = self.blocks + 1 - block
            options = {}                                              # signal -> [(input, mux, tap fuse, selector?)]
            for sig, src in needs[block]:
                opts = []
                for o in self.TAPS.get(src, ()):
                    f = 4 * (o // 56) + PERIOD[o % 56]
                    opts.append((f, (block, f), self.region(block) + o, False))          # own mux
                    for i in range(36):                                                 # partner mux f, read by input i
                        if self.partner(block, i) == (other, f):
                            opts.append((i, (other, f), self.region(other) + o, True))
                if not opts:
                    raise ValueError("no routing tap known for %s" % src)
                options[sig] = (src, opts)
            taken = {}                                                # input -> (signal, option)

            def place(sig, seen):
                src, opts = options[sig]
                for opt in opts:
                    i, mux = opt[0], opt[1]
                    if i in seen or used.get(mux, src) != src:
                        continue
                    seen.add(i)
                    if i not in taken or place(taken[i][0], seen):
                        taken[i] = (sig, opt)
                        return True
                return False
            for sig in sorted(options, key=lambda x: len(options[x][1])):
                if not place(sig, set()):
                    raise ValueError("block %d cannot route all of its %d input signals" % (block, len(options)))
            for i, (sig, (_, mux, tap, via_partner)) in taken.items():
                used[mux] = options[sig][0]
                program.append(tap)
                if via_partner:
                    program.append(self.region(block) + self.selector_offset[i])
                m_of[block][sig] = i ^ 3
        return m_of, sorted(set(program))

    def option_fuse(self, mc, role):
        return self.OPTION_BASE + 104 * ((mc - 1) // 8) + self.OPTION_ROLES[(mc - 1) % 8][role]

    def option_word(self, fuses, mc):
        return {role: fuses[self.option_fuse(mc, role)] for role in self.OPTION_ROLES[0]}

    def oe_selector(self, fuses, pin):
        return (fuses[self.PIN_UNUSED[pin] + 1], fuses[self.PIN_OE[pin]], fuses[self.PIN_OE[pin] + 1])


class FuseMap:
    """A table-described MAX 7000S in the interface apu.fusesim.Device uses (see
    apu.epm7064s.FuseMap for the option-word reading)."""
    empty_term_in_or_is_true = True

    def __init__(self, daf_fm, tables):
        self.base, self.T = daf_fm, tables
        self.macrocells, self.fuses, self.inputs = daf_fm.macrocells, daf_fm.fuses, daf_fm.inputs
        self.pin_of, self.expander_of = daf_fm.pin_of, daf_fm.expander_of
        self.dedicated_pins = tables.dedicated_pins
        mc_of_pin = {p: m for m, p in daf_fm.pin_of.items()}
        self.source_at = {}
        for mc, places in tables.MACROCELL.items():
            for place in places:
                self.source_at[place] = "mc%d" % mc
        for pin, places in tables.PIN.items():
            name = "in%d" % tables.DEDICATED[pin] if pin in tables.DEDICATED else "pin%d" % mc_of_pin[pin]
            for place in places:
                self.source_at[place] = name

    def product_terms(self, fuses, mc):
        return self.base.product_terms(fuses, mc)

    def word(self, fuses, mc):
        w = self.T.option_word(fuses, mc)
        t, t2 = w["T"], w["T2"]
        if t2 and not t and w["clk"] and w["gck"]:
            p0, p11 = 1, 0
        else:
            p0, p11 = t & t2, t | t2
        return [p0, w["gclr"], w["clk"], w["t4"], w["t3"], w["t2"], w["t1"], w["t0"],
                1 - w["nused"], w["byp"], w["inv"], p11]

    def option_bits(self, fuses, mc):
        pin = self.pin_of.get(mc)
        return {34: 3 if pin in self.T.PIN_OE and self.T.oe_selector(fuses, pin) == self.T.OE_ALWAYS else 0}

    def output_enabled(self, dev, mc):
        T, pin = self.T, self.pin_of.get(mc)
        if pin not in T.PIN_OE:
            return False
        sel = T.oe_selector(dev.fuses, pin)
        if sel == T.OE_ALWAYS:
            return True
        if sel not in T.OE_LINES:
            return False
        inv, sources = T.OE_LINES[sel]
        src = [s for f, s in sources.items() if dev.fuses[f] == 0]
        if len(src) != 1:
            return False
        s = src[0]
        level = dev.pins.get(int(s[3:]), 1) if isinstance(s, str) else dev.value.get(s, 0)
        return level == 1 if dev.fuses[inv] == 0 else level == 0

    def block_inputs(self, fuses, block):
        names = []
        if self.T.routing == "paired":
            mc_of_pin = {p: m for m, p in self.pin_of.items()}
            for m in range(36):
                src = self.T.input_source(fuses, block, m ^ 3)
                if src is None:
                    names.append("none")
                elif src.startswith("pin"):
                    pin = int(src[3:])
                    names.append("in%d" % self.T.DEDICATED[pin] if pin in self.T.DEDICATED else "pin%d" % mc_of_pin[pin])
                else:
                    names.append(src)                            # "mcN", "?" or "multi"
            return names
        for m in range(36):
            field = m ^ 3
            hits = [s for s in range(self.T.FIELD_WIDTH) if not fuses[self.T.field_fuse(block, field, s)]]
            names.append(self.source_at.get((field, hits[0]), "?") if len(hits) == 1 else ("none" if not hits else "multi"))
        return names
