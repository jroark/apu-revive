"""Structural view of a MAX 7000 fuse list, built from the device's assembler file.

The logic array is pt_wls input columns ("word lines") per product term and pterms_per_mc terms
per macrocell. A programmed fuse (0) in the array connects an input column into a product term.

What is established and what is not:
  - array geometry and per-macrocell option fuse positions: from the assembler file, and a real
    EPM7032's contents fit it (terms are empty, fully connected, or carry a few literals);
  - columns 72-87 being the sixteen shared-expander inputs: the assembler file's exp_wl table (expander word lines);
  - which signal each of columns 0-71 carries: decoded for the EPM7032 when the architecture file
    is supplied (block input m feeds columns 2m and 2m+1; see block_inputs). On larger devices
    the routing decode is not established and columns are shown as cN;
  - what most option elements do: NOT known. Elements are reported by number.
From asm.dll's product-term routine (0x10004c83): a term with no fuses programmed is the constant
VCC and one with all its fuses programmed is GND; each shared expander has one word line. A
signal on block input m uses columns 2m and 2m + 1; vendor-compiled POFs show 2m + 1 is the true
literal and 2m the complement (my earlier reading of asm.dll had them the other way round).
"""
from . import daf as daf_module


def _epm7032_option_fuse(f: int) -> int:
    """Where an EPM7032 option fuse the assembler file names really is (found from
    vendor-compiled POFs, 2026-11-26).

    The file gives each macrocell twelve consecutive fuses from 14080. In the POFs the
    macrocells come in pairs sharing a 24-fuse row of six nibbles N0..N5: the odd macrocell's
    twelve fuses are N0 N1 N3 and the even one's N5 N2 N4 (unused cells and identical used
    cells then read the same in both positions). The per-pin fuses from 14608 (element 34, two
    per pin) are numbered in reversed groups of four: the file's row for macrocell k is really
    macrocell ((k - 1) XOR 3) + 1's, the same reversal as the routing fields."""
    if 14080 <= f < 14464:
        offset = f - 14080
        pair, i = divmod(offset, 24)
        odd = i < 12
        k = i if odd else i - 12                           # position in the file's twelve
        nibble = (0, 1, 3)[k // 4] if odd else (5, 2, 4)[k // 4]
        return 14080 + pair * 24 + nibble * 4 + k % 4
    if 14608 <= f < 14672:
        pin, bit = divmod(f - 14608, 2)
        return 14608 + 2 * (pin ^ 3) + bit
    return f


class FuseMap:
    def __init__(self, daf, maf=None):
        n = daf.named()
        self.routing = None
        if maf is not None:
            from . import maf as maf_module
            fields = maf_module.routing(maf)
            if {w for w, _ in fields.values()} == {2}:        # decode confirmed for 2-fuse fields only
                self.routing = [fields[a] for a in sorted(fields)]
                self.routing_addresses = sorted(fields)
        self.inputs, self.terms = n["pt_wls"], n["pterms_per_mc"]
        # A term's fuses start at (block - 1) * lab_offset + (mc_pterm + term * mc_pt_offset) *
        # pt_offset (asm.dll); 88 apart on every MAX 7000 file (the EPM7032 stores mc_pt_offset 88
        # and pt_offset 1, the larger devices 11 and 8).
        self.stride = n["mc_pt_offset"] * n["pt_offset"]
        if self.stride != self.inputs or not self.terms or n["wl_offset"] != 1:
            raise ValueError("this device's logic-array layout is not understood yet")
        self.step = self.stride * self.terms
        self.macrocells, self.fuses = n["n_mcs"], n["total_bits"]
        self.starts = [(daf.tables["mc_lab"][m] - 1) * n["lab_offset"] + daf.tables["mc_pterm"][m] * n["pt_offset"]
                       for m in range(1, self.macrocells + 1)]
        self.pin_of = {mc: pin for pin, mc in enumerate(daf.tables["pin_mc"]) if mc}
        # virtual product terms: index -> (input equation type, physical term)
        self.vpterms = {i: v for i, v in enumerate(daf.tables["vpterm_array"]) if v[0] != 0xFFFFFFFF}
        # One-fuse steering elements: {element id: {fuse value: {(equation type, term)}}}. An
        # element's arch_vpterm entries name a virtual term and a "condition". For a plain one-bit
        # element the condition is the fuse value (asm.dll's case for element 27 does exactly
        # this); for a select element the condition indexes select[], which gives the value
        # (asm.dll's cases for elements 24 and 26 call the select setter with these indices).
        self.steering = {}
        for e in daf.group1:
            if not e.fields[5] or e.fields[7] != 1:           # needs vpterm entries and one fuse
                continue
            table = {}
            for _, vp, cond, _ in e.b[1:]:
                if vp not in self.vpterms:
                    continue
                if e.fields[2] == 1:                           # plain bit: the entry holds the value
                    value = cond
                elif cond < len(e.a):                          # select element: value = select[cond]
                    value = e.a[cond]
                else:
                    continue
                if value in (0, 1):
                    table.setdefault(value, set()).add(self.vpterms[vp])
            self.steering[e.fields[0]] = table
        self.options = daf_module.fuse_groups(daf)          # [(element id, width, {mc: fuses})]
        if self.fuses == 14672 and self.macrocells == 32:   # EPM7032: see _epm7032_option_fuse
            self.options = [(eid, w, {mc: tuple(_epm7032_option_fuse(f) for f in fs) for mc, fs in rows.items()})
                            for eid, w, rows in self.options]
        # exp_wl[k] is the single word line of shared expander k
        self.expander_of = {v: k for k, v in enumerate(daf.tables["exp_wl"]) if k and v != 0xFFFFFFFF}
        self.expander_columns = sorted(self.expander_of)

    def product_terms(self, fuses, macrocell: int):
        """[term] for a macrocell (numbered from 1); a term is None when it has no connections,
        "all" when every column is connected (a term that can never be true), or the sorted
        list of connected columns."""
        base = self.starts[macrocell - 1]
        out = []
        for t in range(self.terms):
            bits = fuses[base + t * self.inputs: base + (t + 1) * self.inputs]
            cols = [c for c, b in enumerate(bits) if b == 0]
            out.append(None if not cols else "all" if len(cols) == self.inputs else cols)
        return out

    def word(self, fuses, macrocell: int):
        """The macrocell's twelve option fuses p0..p11 in the order apu.fusesim describes (the
        assembler file's element order 24 25 26 27 28 1 29 30 31 31 6 32, at their real
        positions)."""
        out = []
        for eid, _, rows in self.options:
            if eid in (24, 25, 26, 27, 28, 1, 29, 30, 31, 6, 32) and macrocell in rows:
                out.append((eid, [fuses[f] for f in rows[macrocell]]))
        order = (24, 25, 26, 27, 28, 1, 29, 30, 31, 6, 32)
        bits = dict(out)
        return [b for eid in order for b in bits[eid]]

    def option_bits(self, fuses, macrocell: int):
        """{element id: value} with the element's fuses read most significant first."""
        out = {}
        for eid, _, rows in self.options:
            if macrocell in rows:
                value = 0
                for f in rows[macrocell]:
                    value = value << 1 | fuses[f]
                out[eid] = value
        return out

    def term_roles(self, fuses, macrocell: int):
        """{term: sorted equation types it can be serving}, narrowed by the one-fuse steering
        elements. Type 0 is the macrocell's sum of products; what types 1-5 are is not known."""
        roles = {}
        for eq, term in self.vpterms.values():
            roles.setdefault(term, set()).add(eq)
        values = self.option_bits(fuses, macrocell)
        for eid, table in self.steering.items():
            if eid not in values:
                continue
            # An element decides only the term it governs (the term most of its entries name), and
            # only if it chooses between at least two roles for it. A single-entry element (the
            # E family's 82, set to 1 on every macrocell of two real chips) is a dependency note,
            # not a select; cross-term entries (element 25 naming term 0) are ignored.
            listed = {pair for pairs in table.values() for pair in pairs}
            terms = [t for _, t in listed]
            governed = max(set(terms), key=terms.count) if terms else None
            mine = {p for p in listed if p[1] == governed}
            if len(mine) < 2:
                continue
            allowed = table.get(values[eid], set())
            for eq, term in mine - allowed:
                roles[term].discard(eq)
        return {term: sorted(eqs) for term, eqs in sorted(roles.items())}

    def block_value(self, fuses, block: int, m: int) -> int:
        """Select value of block input m (product-term columns 2m and 2m + 1): the two fuses at
        the block's m-th routing-field address, first fuse the LOW bit."""
        address = self.routing_addresses[(block - 1) * 36 + m]
        return fuses[address + 1] << 1 | fuses[address]

    def block_sources(self, block: int, m: int) -> dict:
        """{value: source} for block input m. The architecture file's source lists sit four
        apart from their fields in reversed groups: block input m is read with the list the
        file gives for field m XOR 3 of the same block."""
        return self.routing[(block - 1) * 36 + (m ^ 3)][1]

    def block_inputs(self, fuses, block: int):
        """Names of the 36 signals routed into a logic array block (numbered from 1), or None
        when this device's routing decode is not established.

        Established on the EPM7032 from vendor-compiled POFs of designs with known pins
        (2026-11-26; all 32 observations of two probes, no exceptions): block input m's value
        is `block_value`, looked up in `block_sources`; I/O entry i is the pin of macrocell
        i + 1. Names: "pinN" = the pin of macrocell N used as an input, "mcN" = macrocell N's
        feedback, "inK" = dedicated input K. Values 0 and 1 (macrocell feedback in the file)
        follow the same binary reading but were not yet seen with known sources.
        """
        if self.routing is None:
            return None
        names = []
        for m in range(36):
            source = self.block_sources(block, m).get(self.block_value(fuses, block, m))
            if source is None:
                names.append("?")
            elif source[0] == "mc":
                names.append("mc%d" % (source[1] + 1))
            elif source[1] < self.macrocells:
                names.append("pin%d" % (source[1] + 1))
            else:
                names.append("in%d" % (source[1] - self.macrocells))
        return names

    def other_fuses(self):
        """Fuse numbers not in the logic array or in any per-macrocell option element."""
        used = set()
        for s in self.starts:
            used.update(range(s, s + self.step))
        for _, _, rows in self.options:
            for fs in rows.values():
                used.update(fs)
        return [f for f in range(self.fuses) if f not in used]

    def report(self, fuses) -> str:
        if len(fuses) != self.fuses:
            raise ValueError("fuse count does not match the device")
        lines = []
        for mc in range(1, self.macrocells + 1):
            terms = self.product_terms(fuses, mc)
            opts = " ".join("e%d=%d" % kv for kv in self.option_bits(fuses, mc).items())
            pin = (" (pin %d)" % self.pin_of[mc]) if mc in self.pin_of else ""
            lines.append("macrocell %d%s: %s" % (mc, pin, opts))
            roles = self.term_roles(fuses, mc)
            for i, t in enumerate(terms):
                role = "/".join("sum" if r == 0 else "eq%d" % r for r in roles.get(i, []))
                if t is None:
                    text = "VCC  (no connections: always true)"
                elif t == "all":
                    text = "GND  (every column connected: never true)"
                else:
                    names = self.block_inputs(fuses, (mc - 1) // 16 + 1)

                    def literal(c):
                        if c in self.expander_of:
                            return "exp%d" % self.expander_of[c]
                        # Block input m feeds columns 2m (complement) and 2m + 1 (true signal):
                        # checked on vendor-compiled EPM7032 POFs (an AND of two inputs uses the
                        # odd columns; a shared expander of complemented inputs the even ones).
                        name = names[c // 2] if names else "in%d" % (c // 2)
                        return ("" if c % 2 else "!") + name
                    text = " & ".join(literal(c) for c in t)
                lines.append("    term %d [%s]: %s" % (i, role, text))
        rest = self.other_fuses()
        lines.append("other fuses %d..%d: %s" % (rest[0], rest[-1], "".join(str(fuses[f]) for f in rest)))
        return "\n".join(lines)
