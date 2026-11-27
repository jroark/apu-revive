"""Simulate a programmed EPM7032 from its fuses alone.

The model, established against POFs compiled by MAX+PLUS II 10.2 (see re/protocol.md,
2026-11-26). Each macrocell's twelve option fuses p0..p11 (FuseMap.word) mean:
    p7, p6, p5, p4, p3   steering of terms 0, 1, 2, 3, 4: 0 = into the macrocell's OR,
                         1 = to the term's special use: term 0 = this macrocell's shared
                         expander (offered, inverted, to the block), term 1 = the XOR input,
                         term 2 = clear, term 3 = clock enable (global clock) or clock (p2),
                         term 4 = preset (clear and preset are active high)
    p2                   clock: 0 = the global clock pin, 1 = term 3
    p1                   the global clear pin (active low) also clears
    p8, p9               p8 = 1: the macrocell is in use; p9 = 1: register bypassed
    p0                   parallel expander: this macrocell's OR goes to the next macrocell's OR
                         (chains of 8); the cell's own XOR then sees 1 instead
    p10                  inverts the output of the XOR
    p11                  T flip-flop: the register toggles when its input is 1
An empty term (all fuses intact) is true where it drives a special input but adds nothing to
the OR. The XOR's second input is the complement of term 1 if term 1 is steered to it, else 0
(term 0's special use is the shared expander, never the XOR). So output = (p0 ? 1 : OR(sum terms, plus any OR lent from below)) XOR that XOR p10. Registers
power up at 0. A pin is driven by its macrocell according to element 34 (0 never, 3 always,
1 / 2 while OE1 (pin 44) / OE2 (pin 2) is low: they are active low); otherwise it carries the value from outside.
A shared expander is a macrocell's term 0, inverted, offered to its whole block (see
expander_cell for the numbering).
"""
from .fusemap import FuseMap

GCLK, GCLR, OE1, OE2 = 43, 1, 44, 2
# dedicated input k of the architecture file (I/O entry 32 + k) -> its pin (probe s05, 2026-11-26)
DEDICATED_INPUT = {0: GCLK, 1: OE1, 2: GCLR, 3: OE2}


class Device:
    def __init__(self, fmap: FuseMap, fuses, expander_source=None):
        self.fm, self.fuses = fmap, fuses
        self.n = fmap.macrocells
        self.word = {mc: fmap.word(fuses, mc) for mc in range(1, self.n + 1)}
        self.oe = {mc: fmap.option_bits(fuses, mc).get(34, 0) for mc in range(1, self.n + 1)}
        self.terms = {mc: fmap.product_terms(fuses, mc) for mc in range(1, self.n + 1)}
        self.inputs = {b: fmap.block_inputs(fuses, b) for b in range(1, self.n // 16 + 1)}
        # column -> macrocell whose term 0 is that shared expander
        self.expander_source = expander_source or {}
        self.mc_of_pin = {pin: mc for mc, pin in fmap.pin_of.items()}
        # whether an empty term steered into the OR is true (EPM7064S as Quartus programs it) or
        # adds nothing (EPM7032 as MAX+PLUS II programs it)
        self.empty_in_or = getattr(fmap, "empty_term_in_or_is_true", False)
        # the package's dedicated pins: global clock, global clear, OE1, OE2 (44-pin by default)
        self.GCLK, self.GCLR, self.OE1, self.OE2 = getattr(fmap, "dedicated_pins", (GCLK, GCLR, OE1, OE2))
        self.dedicated_input = {0: self.GCLK, 1: self.OE1, 2: self.GCLR, 3: self.OE2}
        # OE pins are active low unless the fuse map says a line is inverted (EPM7064S)
        self.oe_high = fmap.oe_active_high(fuses) if hasattr(fmap, "oe_active_high") else {1: False, 2: False}
        self.q = {mc: 0 for mc in range(1, self.n + 1)}
        self.pins = {}                                   # outside values, by pin number
        self.value = {}                                  # macrocell outputs (combinational or q)
        self._settle()

    # -- evaluation ------------------------------------------------------------------------
    def _signal(self, name):
        if name.startswith("mc"):
            return self.value.get(int(name[2:]), 0)
        if name.startswith("pin"):
            return self.pin_value(self.fm.pin_of[int(name[3:])])
        if name.startswith("in"):
            return self.pins.get(self.dedicated_input[int(name[2:])], 0)
        return 0

    def _term(self, mc, t):
        cols = self.terms[mc][t]
        if cols is None:
            return 1
        if cols == "all":
            return 0
        names = self.inputs[(mc - 1) // 16 + 1]
        for c in cols:
            if c in self.fm.expander_of:
                src = self.expander_source.get(c) or self.expander_cell(mc, c)
                v = 1 - self._term(src, 0)
            else:
                v = self._signal(names[c // 2])
                if not c % 2:
                    v = 1 - v
            if not v:
                return 0
        return 1

    def expander_cell(self, mc, column):
        """The macrocell whose term 0 is the shared expander on `column` of mc's block. Shared
        expander k (the assembler file's exp_wl[k] column) comes from local macrocell
        ((k - 1) XOR 6) + 1 of the block (found from vendor POFs: chiptrip's fourteen
        expanders and p08's two, 2026-11-26)."""
        k = self.fm.expander_of[column]
        return (mc - 1) // 16 * 16 + ((k - 1) ^ 6) + 1

    def _own_or(self, mc):
        w = self.word[mc]
        special = {0: w[7], 1: w[6], 2: w[5], 3: w[4], 4: w[3]}
        return int(any(self._term(mc, t) for t in range(5) if not special[t] and (self.empty_in_or or self.terms[mc][t] is not None)))

    def _lent_in(self, mc):
        """The OR a lower macrocell lends to mc through the parallel-expander chain (chains are
        macrocells 1-8, 9-16, ... of each block; a lender passes on what it borrowed too)."""
        below = mc - 1
        if (mc - 1) % 8 == 0 or not self.word[below][0]:
            return 0
        return self._own_or(below) | self._lent_in(below)

    def _logic(self, mc):
        """(combinational value, clear, preset, clock, clock enable) of a macrocell."""
        w = self.word[mc]
        special = {0: w[7], 1: w[6], 2: w[5], 3: w[4], 4: w[3]}
        terms = [self._term(mc, t) for t in range(5)]
        # An empty term (no array connections) adds nothing to the OR, though it reads as true
        # where it drives a special input (an empty clock-enable term enables).
        total = int(any(terms[t] for t in range(5) if not special[t] and (self.empty_in_or or self.terms[mc][t] is not None))) | self._lent_in(mc)
        x = 1 - terms[1] if special[1] else 0             # term 0's special use is the shared expander
        # p0 = 1: this OR goes up to the next macrocell (parallel expander); the cell's own XOR
        # then sees 1 in its place
        value = (1 if w[0] else total) ^ x ^ w[10]
        clear = (special[2] and terms[2]) or (w[1] and not self.pins.get(self.GCLR, 1))
        preset = special[4] and terms[4]
        if w[2]:
            clock, enable = terms[3] if special[3] else 0, 1
        else:
            clock, enable = self.pins.get(self.GCLK, 0), terms[3] if special[3] else 1
        return value, clear, preset, clock, enable

    def registered(self, mc):
        w = self.word[mc]
        return w[8] == 1 and w[9] == 0

    def _settle(self):
        for _ in range(4 * self.n + 4):
            changed = False
            for mc in range(1, self.n + 1):
                value, clear, preset, _, _ = self._logic(mc)
                if self.registered(mc):
                    if clear:
                        self.q[mc] = 0
                    elif preset:
                        self.q[mc] = 1
                    value = self.q[mc]
                if self.value.get(mc) != value:
                    self.value[mc], changed = value, True
            if not changed:
                return
        raise RuntimeError("combinational loop in the fuse map does not settle")

    # -- pins --------------------------------------------------------------------------------
    def driving(self, mc):
        if hasattr(self.fm, "output_enabled"):          # the fuse map decodes its own enables
            return self.fm.output_enabled(self, mc)
        oe = self.oe.get(mc, 0)
        # the dedicated OE pins are active low (the compiler feeds them an inverted enable)
        def line(n, pin):
            level = self.pins.get(pin, 0 if self.oe_high[n] else 1)
            return level == 1 if self.oe_high[n] else level == 0
        return oe == 3 or (oe == 1 and line(1, self.OE1)) or (oe == 2 and line(2, self.OE2))

    def pin_value(self, pin):
        mc = self.mc_of_pin.get(pin)
        if mc and self.driving(mc):
            return self.value.get(mc, 0)
        return self.pins.get(pin, 0)

    def set(self, **pins):
        """Apply outside pin values {"p<n>": 0/1}; registers whose clock rises take the value
        their input had just before."""
        before = {mc: self._logic(mc) for mc in range(1, self.n + 1) if self.registered(mc)}
        for k, v in pins.items():
            self.pins[int(k[1:])] = int(bool(v))
        self._settle()
        for mc, (value, _, _, clock, enable) in before.items():
            now = self._logic(mc)
            if not clock and now[3] and enable:
                self.q[mc] = (self.q[mc] ^ value) if self.word[mc][11] else value
        self._settle()
