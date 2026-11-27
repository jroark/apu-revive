"""EPM7064S (44-pin) logic-array-block input routing, as Quartus II 13.0 SP1 programs it.

Each logic block has 36 inputs; block input m (product-term columns 2m, 2m + 1) is selected by
field m XOR 3 of that block. A field is 8 one-hot fuses at 28992 + 8 * (36 * (block - 1) + field)
+ slot (0 = connected). Every source reaches the same (field, slot) places in all four blocks.
Observed from 66 compiled probes (re/protocol.md, 2026-11-26): 432 pin and 505 macrocell-feedback
observations, no conflicts. Sources seen in only one of their two fields have one entry here. Seven places were added from
designs that used them: pins 16, 17, 34, 36, 37 (by slot) and 21 (27, 3: by co-simulation; its other place is slot 2,
so a pin's two fields need not share a slot), and LC39 (7, 6). Pins use slots 0-4,
macrocells 4-7; an output pin's line carries the value it drives.
The MAX+PLUS II architecture file (m7064s.maf) does not match this numbering and is not used.
"""

ROUTING_BASE = 28992

# macrocell (1-64) -> [(field, slot), ...]
MACROCELL = {
    1: [(4, 4), (5, 4)],
    2: [(0, 5), (7, 4)],
    3: [(3, 4), (8, 5)],
    4: [(2, 4), (6, 5)],
    5: [(13, 4), (14, 4)],
    6: [(9, 5), (16, 4)],
    7: [(12, 4), (17, 5)],
    8: [(11, 4), (15, 5)],
    9: [(19, 7), (20, 7)],
    10: [(21, 7)],
    11: [(23, 7), (24, 7)],
    12: [(25, 7), (26, 7)],
    13: [(28, 7), (29, 7)],
    14: [(30, 7)],
    15: [(32, 7)],
    16: [(34, 7)],
    17: [(13, 5), (16, 5)],
    18: [(10, 5), (12, 5)],
    19: [(11, 5), (14, 5)],
    20: [(9, 6)],
    21: [(4, 5), (7, 5)],
    22: [(1, 5), (3, 5)],
    23: [(5, 5)],
    24: [(0, 6), (8, 6)],
    25: [(27, 7), (32, 6)],
    26: [(30, 6)],
    27: [(28, 6)],
    28: [(29, 6)],
    29: [(18, 7), (23, 6)],
    30: [(21, 6), (25, 6)],
    31: [(19, 6), (24, 6)],
    32: [(20, 6), (22, 6)],
    33: [(11, 6), (13, 6)],
    34: [(10, 6), (15, 6)],
    35: [(12, 6), (16, 6)],
    36: [(9, 7), (14, 6)],
    37: [(2, 6), (4, 6)],
    38: [(1, 6), (6, 6)],
    39: [(3, 6), (7, 6)],
    40: [(0, 7), (5, 6)],
    41: [(27, 6), (35, 6)],
    42: [(29, 5), (32, 5)],
    43: [(28, 5), (30, 5)],
    44: [(31, 5), (34, 5)],
    45: [(18, 6), (26, 6)],
    46: [(20, 5), (23, 5)],
    47: [(19, 5), (21, 5)],
    48: [(22, 5)],
    49: [(7, 7), (8, 7)],
    50: [(5, 7), (6, 7)],
    51: [(3, 7)],
    52: [(1, 7), (2, 7)],
    53: [(16, 7), (17, 7)],
    54: [(14, 7), (15, 7)],
    55: [(12, 7), (13, 7)],
    56: [(10, 7), (11, 7)],
    57: [(20, 4), (24, 5)],
    58: [(21, 4), (26, 5)],
    59: [(18, 5)],
    60: [(22, 4)],
    61: [(29, 4)],
    62: [(30, 4)],
    63: [(27, 5), (34, 4)],
    64: [(31, 4), (32, 4)],
}

# pin (PLCC44 number; 1, 2, 43, 44 are the dedicated inputs) -> [(field, slot), ...]
PIN = {
    1: [(15, 4), (17, 4)],
    2: [(6, 4)],
    4: [(14, 1), (17, 1)],
    5: [(13, 1), (15, 1)],
    6: [(0, 1), (3, 0)],
    8: [(27, 4), (28, 4)],
    9: [(22, 3), (26, 3)],
    11: [(21, 3), (23, 3)],
    12: [(18, 4), (19, 4)],
    14: [(2, 1), (3, 1)],
    16: [(10, 1), (16, 1)],
    17: [(24, 2), (25, 2)],
    18: [(18, 3), (20, 2)],
    19: [(33, 2), (34, 2)],
    20: [(28, 3), (32, 2)],
    21: [(27, 3), (29, 2)],
    24: [(28, 2), (35, 2)],
    25: [(29, 1), (30, 1)],
    26: [(28, 1), (34, 1)],
    27: [(19, 2), (26, 2)],
    28: [(19, 1), (25, 1)],
    29: [(15, 2), (16, 2)],
    31: [(1, 3), (5, 2)],
    33: [(23, 1), (26, 1)],
    34: [(22, 1), (24, 1)],
    36: [(18, 0), (24, 0)],
    37: [(32, 1), (35, 1)],
    39: [(4, 3), (8, 3)],
    40: [(12, 3)],
    41: [(9, 4), (10, 4)],
    43: [(33, 4)],
    44: [(24, 4)],
}


def field_fuse(block, field, slot):
    return ROUTING_BASE + 8 * (36 * (block - 1) + field) + slot


def check():
    """No two sources share a (field, slot)."""
    seen = {}
    for kind, table in (("mc", MACROCELL), ("pin", PIN)):
        for src, places in table.items():
            for place in places:
                if place in seen:
                    raise ValueError("%r and %r both at %r" % (seen[place], (kind, src), place))
                seen[place] = (kind, src)
    return len(seen)

# --- macrocell option fuses -------------------------------------------------------------------
# 13 per macrocell, 832 from OPTION_BASE; the POF permutes them within groups of eight
# macrocells (104 fuses), the same way in every group. Offsets below are within the group of
# macrocell mc (group start OPTION_BASE + 104 * ((mc - 1) // 8)), by position (mc - 1) % 8.
# Found from single-feature probes at LC1-LC20 (re/protocol.md, 2026-11-27). Roles, with the
# EPM7032 word bit each corresponds to (apu/fusesim.py):
#   t0..t4  steering of product term 0..4 (p7..p3): 0 = into the OR, 1 = special use
#   clk     p2: 1 = clock from term 3, 0 = global clock
#   gclr    p1: 1 = the global clear pin also clears
#   nused   NOT p8: 0 = macrocell in use
#   byp     p9: 1 = register bypassed (combinational)
#   inv     p10: output inverted
#   T, T2   both 1 for a T flip-flop
#   gck     0 when the clock comes from term 3 (1 for global-clock, combinational, unused cells)
OPTION_BASE = 28160
OPTION_ROLES = {
    0: {"T": 0, "gclr": 1, "clk": 2, "t4": 3, "t3": 4, "t2": 5, "t1": 6, "t0": 7,
        "gck": 11, "nused": 12, "byp": 13, "inv": 14, "T2": 15},
    1: {"t2": 8, "t1": 9, "t0": 10, "byp": 16, "inv": 17, "T": 18, "T2": 19, "gclr": 20,
        "clk": 21, "t4": 22, "t3": 23, "gck": 30, "nused": 31},
    2: {"clk": 24, "t4": 25, "t3": 26, "t2": 27, "t1": 28, "t0": 29, "gck": 33, "nused": 34,
        "byp": 35, "inv": 36, "T": 37, "T2": 38, "gclr": 39},
    3: {"t0": 32, "T": 40, "T2": 41, "gclr": 42, "clk": 43, "t4": 44, "t3": 45, "t2": 46,
        "t1": 47, "gck": 52, "nused": 53, "byp": 54, "inv": 55},
    4: {"t3": 48, "t2": 49, "t1": 50, "t0": 51, "nused": 56, "byp": 57, "inv": 58, "T": 59,
        "T2": 60, "gclr": 61, "clk": 62, "t4": 63, "gck": 71},
    5: {"gclr": 64, "clk": 65, "t4": 66, "t3": 67, "t2": 68, "t1": 69, "t0": 70, "gck": 74,
        "nused": 75, "byp": 76, "inv": 77, "T": 78, "T2": 79},
    6: {"t1": 72, "t0": 73, "inv": 80, "T": 81, "T2": 82, "gclr": 83, "clk": 84, "t4": 85,
        "t3": 86, "t2": 87, "gck": 93, "nused": 94, "byp": 95},
    7: {"t4": 88, "t3": 89, "t2": 90, "t1": 91, "t0": 92, "gck": 96, "nused": 97, "byp": 98,
        "inv": 99, "T": 100, "T2": 101, "gclr": 102, "clk": 103},
}


def option_fuse(mc, role):
    return OPTION_BASE + 104 * ((mc - 1) // 8) + OPTION_ROLES[(mc - 1) % 8][role]


def option_word(fuses, mc):
    return {role: fuses[option_fuse(mc, role)] for role in OPTION_ROLES[0]}


# --- output enables ---------------------------------------------------------------------------
# pin -> its two output-enable fuses: both 1 = the pin's macrocell always drives it, both 0 = an
# input (matches exactly, over 316 compiled designs, which pins are outputs). Other enable modes
# (the OE1 / OE2 pins, product-term enables) are not decoded yet.
PIN_OE = {4: 30294, 5: 30274, 6: 30260, 8: 30216, 9: 30222, 11: 30212, 12: 30192, 14: 30370,
          16: 30336, 17: 30342, 18: 30312, 19: 30318, 20: 30308, 21: 30288, 24: 30384, 25: 30404,
          26: 30414, 27: 30408, 28: 30438, 29: 30432, 31: 30466, 33: 30480, 34: 30500, 36: 30510,
          37: 30504, 39: 30528, 40: 30562, 41: 30582}          # the second fuse is the next one


def drives(fuses, pin):
    f = PIN_OE[pin]
    return fuses[f] == 1 and fuses[f + 1] == 1


# Each pin's enable is a 3-bit selector (PIN_UNUSED[pin] + 1, PIN_OE[pin], PIN_OE[pin] + 1):
OE_ALWAYS, OE_NEVER, OE_LINE1, OE_LINE2 = (1, 1, 1), (1, 0, 0), (1, 0, 1), (0, 0, 0)
# global OE line 1 (selector 1 0 1) from pin 44, line 2 (0 0 0) from pin 2: source fuse 0 =
# connected; the lines are active low unless their invert fuse is 0 (tri-state probes q00-q03)
OE_LINE_SOURCE = {1: (30148, 30159), 2: (30161, 30173)}      # line -> (source fuse, invert fuse)
OE_LINE_PIN = {1: 44, 2: 2}


# All six global OE lines (tri-state probes e00-e11, q00-q04): selector code -> (invert fuse,
# {source fuse: source}); a source is "pin<n>" or a macrocell number. Source fuse 0 = connected.
# The line enables while its source is low, unless its invert fuse is 0 (then while high).
# Only the sources Quartus chose in the probes are known; each line may have others.
OE_LINES = {
    (1, 0, 1): (30159, {30148: "pin44"}),
    (0, 0, 0): (30173, {30161: "pin2", 30162: 4, 30163: 24, 30164: 40}),
    (0, 0, 1): (30180, {30169: 5, 30170: 19, 30171: 35, 30172: 53}),
    (0, 1, 0): (30187, {30176: 17, 30177: 20, 30178: 33}),
    (0, 1, 1): (30194, {30184: 62, 30185: 41, 30186: 25}),
    (1, 1, 0): (30166, {30154: 1, 30155: 3, 30157: 49, 30158: 51}),
}


JTAG_PINS = (7, 13, 32, 38)                # TDI, TMS, TCK, TDO
# OE pin -> (line code, source fuse, invert fuse), for apu.generate7064s
OE_PIN_LINES = {44: (OE_LINE1, 30148, 30159), 2: (OE_LINE2, 30161, 30173)}


def oe_selector(fuses, pin):
    return (fuses[PIN_UNUSED[pin] + 1], fuses[PIN_OE[pin]], fuses[PIN_OE[pin] + 1])


# --- the rest (30144-30608) ---------------------------------------------------------------------
# pin -> fuse that is 1 exactly when the design does not use the pin (321 designs)
PIN_UNUSED = {4: 30292, 5: 30272, 6: 30258, 8: 30230, 9: 30220, 11: 30210, 12: 30206, 14: 30368, 16: 30350, 17: 30340, 18: 30326, 19: 30316, 20: 30306, 21: 30302, 24: 30398, 25: 30402, 26: 30412, 27: 30422, 28: 30436, 29: 30446, 31: 30464, 33: 30494, 34: 30498, 36: 30508, 37: 30518, 39: 30542, 40: 30560, 41: 30580}
GLOBAL_CLOCK_UNUSED = 30576        # 1 exactly when no register uses the global clock (pin 43)
# every other fuse from 30144 on is the same in all 321 designs: 1, except these
CONSTANT_ZEROS = [30244, 30246, 30247, 30388, 30390, 30391, 30484, 30486, 30487, 30532, 30577, 30608]


# --- a fuse map for apu.fusesim ------------------------------------------------------------------
DEDICATED = {43: 0, 44: 1, 1: 2, 2: 3}          # pin -> apu.fusesim dedicated input number


class FuseMap:
    """The EPM7064SLC44 in the interface apu.fusesim.Device uses. Product-term layout, pins and
    shared-expander columns come from the architecture files (s7064044.daf, checked: region k
    is LC k); routing and options from the tables above.
    The two T bits: T or T2 alone makes the register a T flip-flop toggled by the macrocell's
    logic; both together make it toggle on every enabled clock (the logic input reads 1, as with
    the EPM7032's p0). T2 alone in a cell with no clock (clk = 1 with gck = 1) is the
    parallel-expander bit p0: the cell lends its OR to the macrocell above. Found by co-simulating 106 Quartus designs (re/protocol.md, 2026-11-27).
    t2_role "lend" / "T" select the two readings that were ruled out, for comparison."""

    # an empty product term steered into the OR reads as true (Quartus relies on it for constant
    # data inputs and fills unused OR slots with all-connected terms instead)
    empty_term_in_or_is_true = True

    def __init__(self, daf_fm, t2_role="both"):
        self.base = daf_fm
        self.macrocells, self.fuses, self.inputs = daf_fm.macrocells, daf_fm.fuses, daf_fm.inputs
        self.pin_of, self.expander_of = daf_fm.pin_of, daf_fm.expander_of
        self.t2_role = t2_role
        mc_of_pin = {p: m for m, p in daf_fm.pin_of.items()}
        self.source_at = {}
        for mc, places in MACROCELL.items():
            for place in places:
                self.source_at[place] = "mc%d" % mc
        for pin, places in PIN.items():
            name = "in%d" % DEDICATED[pin] if pin in DEDICATED else "pin%d" % mc_of_pin[pin]
            for place in places:
                self.source_at[place] = name

    def product_terms(self, fuses, mc):
        return self.base.product_terms(fuses, mc)

    def word(self, fuses, mc):
        w = option_word(fuses, mc)
        t, t2 = w["T"], w["T2"]
        if self.t2_role == "lend":
            p0, p11 = t2, t
        elif self.t2_role == "T":
            p0, p11 = 0, t | t2
        elif t2 and not t and w["clk"] and w["gck"]:
            p0, p11 = 1, 0                     # an unclocked cell: T2 lends its OR upward (p0)
        else:                                  # either bit makes a T flip-flop; both together
            p0, p11 = t & t2, t | t2           # force its toggle input to 1 (like p0)
        return [p0, w["gclr"], w["clk"], w["t4"], w["t3"], w["t2"], w["t1"], w["t0"],
                1 - w["nused"], w["byp"], w["inv"], p11]

    def option_bits(self, fuses, mc):
        """{34: enable} in apu.fusesim's terms: 3 always, 1 / 2 from OE pin 44 / 2, 0 never (other
        global OE lines are not decoded and read as never)."""
        pin = self.pin_of.get(mc)
        if pin not in PIN_OE:
            return {34: 0}
        sel = oe_selector(fuses, pin)
        code = {OE_ALWAYS: 3, OE_LINE1: 1}.get(sel, 0)
        if sel == OE_LINE2 and not fuses[OE_LINE_SOURCE[2][0]]:
            code = 2                         # 0 0 0 is also an unconnected line 2
        return {34: code}

    def output_enabled(self, dev, mc):
        """For apu.fusesim: is mc's pin driven, given the device state (all six OE lines)."""
        pin = self.pin_of.get(mc)
        if pin not in PIN_OE:
            return False
        sel = oe_selector(dev.fuses, pin)
        if sel == OE_ALWAYS:
            return True
        if sel not in OE_LINES:
            return False
        inv, sources = OE_LINES[sel]
        src = [s for f, s in sources.items() if dev.fuses[f] == 0]
        if len(src) != 1:
            return False                       # unconnected (or an unknown source)
        s = src[0]
        level = dev.pins.get(int(s[3:]), 1) if isinstance(s, str) else dev.value.get(s, 0)
        return level == 1 if dev.fuses[inv] == 0 else level == 0

    def oe_active_high(self, fuses):
        """{1, 2: whether the line enables while its pin is high}."""
        return {line: fuses[inv] == 0 for line, (src, inv) in OE_LINE_SOURCE.items()}

    def block_inputs(self, fuses, block):
        names = []
        for m in range(36):
            field = m ^ 3
            hits = [s for s in range(8) if not fuses[field_fuse(block, field, s)]]
            names.append(self.source_at.get((field, hits[0]), "?") if len(hits) == 1 else ("none" if not hits else "multi"))
        return names
