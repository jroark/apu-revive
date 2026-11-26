"""Reduce a flat AHDL design to two-level equations — the form a MAX 7000 macrocell implements.

For every output pin and every register this produces a minimised sum of products over the
design's inputs and register outputs, plus the register's clock, clear and preset equations.
The method is exhaustive: each function is tabulated with the evaluator over all values of the
signals it can depend on, irrelevant signals are dropped, and the table is minimised
(Quine-McCluskey prime implicants with a greedy cover). Which signals a function can depend on
comes from the structure of the design's statements, so the limit is MAX_SUPPORT signals per
function, not per design.

Nothing here knows about a device: no product-term limits, expanders, pin assignment or fuses.
"""
import itertools

from . import ahdl_eval

MAX_SUPPORT = 14


class TooLarge(Exception):
    pass


def _primes(minterms, nvars):
    """Prime implicants of a set of minterms, as (value, mask) with mask bits = don't care."""
    current, primes = {(m, 0) for m in minterms}, set()
    while current:
        merged, used = set(), set()
        groups = {}
        for value, mask in current:
            groups.setdefault(mask, []).append(value)
        for mask, values in groups.items():
            present = set(values)
            for value in values:
                for bit in range(nvars):
                    b = 1 << bit
                    if mask & b or value & b:
                        continue
                    if value | b in present:
                        merged.add((value, mask | b))
                        used.add((value, mask))
                        used.add((value | b, mask))
        primes |= current - used
        current = merged
    return primes


def minimise(minterms, nvars):
    """A small cover of `minterms` (ints over nvars variables) by cubes (value, mask)."""
    minterms = set(minterms)
    if not minterms:
        return []
    primes = list(_primes(minterms, nvars))
    covers = {p: {m for m in minterms if (m & ~p[1]) == p[0]} for p in primes}
    chosen, left = [], set(minterms)
    while left:
        # essential primes first, then the one covering most of what is left
        best = None
        for m in left:
            owners = [p for p in primes if m in covers[p]]
            if len(owners) == 1:
                best = owners[0]
                break
        if best is None:
            best = max(primes, key=lambda p: (len(covers[p] & left), bin(p[1]).count("1")))
        chosen.append(best)
        left -= covers[best]
    return sorted(chosen)


class Equation:
    """value = OR of terms, each term an AND of literals {signal: 0 or 1}; inverted if `invert`."""

    def __init__(self, terms, invert=False):
        self.terms, self.invert = terms, invert

    def evaluate(self, env) -> int:
        value = int(any(all(env[s] == v for s, v in term.items()) for term in self.terms))
        return 1 - value if self.invert else value

    def text(self) -> str:
        if not self.terms:
            return "VCC" if self.invert else "GND"
        body = " # ".join(" & ".join(("" if v else "!") + s for s, v in sorted(t.items())) or "VCC" for t in self.terms)
        return "!(%s)" % body if self.invert else body


def _equation(table, support):
    """Minimised Equation for {assignment tuple: 0/1} over the ordered `support` signals."""
    relevant = []
    for i, name in enumerate(support):
        for values, out in table.items():
            if values[i] == 0:
                other = values[:i] + (1,) + values[i + 1:]
                if table[other] != out:
                    relevant.append(i)
                    break
    n = len(relevant)
    on, off = set(), set()
    for values, out in table.items():
        m = sum(values[i] << k for k, i in enumerate(relevant))
        (on if out else off).add(m)
    cover_on, cover_off = minimise(on, n), minimise(off, n)
    invert = len(cover_off) < len(cover_on)             # the macrocell can invert its sum
    cover = cover_off if invert else cover_on
    terms = [{support[relevant[k]]: (value >> k) & 1 for k in range(n) if not (mask >> k) & 1} for value, mask in cover]
    return Equation(terms, invert)


class Netlist:
    """outputs: {pin: Equation}; registers: {bit: {"d" or "t": Equation, "clk": ..., "clrn": ...,
    "prn": ...}} ("t" when the register is better built as a T flip-flop)
    (absent control equations mean not used). Register outputs appear in equations as "bit.q",
    and the value arriving on a bidirectional pin as "pin@ext"."""

    def __init__(self, design, support, outputs, registers, enables=None):
        self.design, self.support, self.outputs, self.registers = design, support, outputs, registers
        self.enables = enables or {}                       # {pin: output-enable Equation} for tri-state pins

    def report(self) -> str:
        lines = []
        for pin, eq in self.outputs.items():
            lines.append("%s = %s;" % (pin, eq.text()))
            if pin in self.enables:
                lines.append("%s.oe = %s;" % (pin, self.enables[pin].text()))
        for bit, eqs in self.registers.items():
            for port, eq in eqs.items():
                lines.append("%s.%s = %s;" % (bit, port, eq.text()))   # .t: toggle input of a T flip-flop
        return "\n".join(lines)


def synthesise(text: str, resolver=None) -> Netlist:
    sim = ahdl_eval.Simulator(text, resolver)
    d = sim.design
    deps = d.dependencies()
    machine_of = {bit: name for name, (bits, _, _) in d.machines.items() for bit in bits}
    order = list(d.inputs) + [bit + ".q" for bit in d.flops] + [b + "@ext" for b in d.bidir]

    def support_of(*keys):
        found = set()
        for key in keys:
            found |= deps.get(key, set())
        if len(found) > MAX_SUPPORT:
            raise TooLarge("%s depends on %d signals; the exhaustive method stops at %d" % (keys[0], len(found), MAX_SUPPORT))
        return [s for s in order if s in found]

    def tabulate(support, fn):
        table = {}
        base = {s: 0 for s in order}
        for values in itertools.product((0, 1), repeat=len(support)):
            env = dict(base)
            env.update(zip(support, values))
            drive, assigned = d._settle(env)
            table[values] = fn(env, drive, assigned)
        return _equation(table, support)

    outputs, enables = {}, {}
    for pin in d.outputs:
        if pin in d.tri:                                  # the buffer's input and its enable, separately
            outputs[pin] = tabulate(support_of(pin + "~in"), lambda env, drive, assigned, pin=pin: drive.get(pin + "~in", 0))
            enables[pin] = tabulate(support_of(pin + "~oe"), lambda env, drive, assigned, pin=pin: drive.get(pin + "~oe", 0))
        else:
            outputs[pin] = tabulate(support_of(pin), lambda env, drive, assigned, pin=pin: env[pin])
    registers = {}
    for bit, kind in d.flops.items():
        owner = machine_of.get(bit, bit)
        ctl = lambda port: "%s.%s" % (owner, port)

        def control(port, default):
            return lambda env, drive, assigned: drive[ctl(port)] if ctl(port) in assigned else default
        if kind == "MACHINE":
            bits, _, first = d.machines[owner]
            nexts = [b + ".next" for b in bits]
            high = (first >> (len(bits) - 1 - bits.index(bit))) & 1

            def nxt(env, drive, assigned, bit=bit, nexts=nexts):
                value = drive.get(bit + ".next", 0) if set(nexts) & assigned else env[bit + ".q"]
                return value if control("ena", 1)(env, drive, assigned) else env[bit + ".q"]
            data_keys = nexts + [ctl("ena")]
        else:
            port = "t" if kind.startswith("T") else "d"

            def nxt(env, drive, assigned, bit=bit, port=port):
                value = drive.get("%s.%s" % (bit, port), 0)
                if port == "t":
                    value ^= env[bit + ".q"]
                return value if control("ena", 1)(env, drive, assigned) else env[bit + ".q"]
            data_keys = ["%s.%s" % (bit, port), ctl("ena")]
        deps.setdefault("%s#data" % bit, set()).update({bit + ".q"})
        support = support_of("%s#data" % bit, *data_keys)
        eqs = {"d": tabulate(support, nxt)}
        # A MAX 7000 macrocell can also run its register as a T flip-flop (the XOR feeds back
        # the register output), so keep the toggle form when it needs fewer product terms --
        # the original compiler does this for counters (TFFE in its report).
        toggle = tabulate(support, lambda env, drive, assigned, nxt=nxt, bit=bit:
                          nxt(env, drive, assigned) ^ env[bit + ".q"])
        if len(toggle.terms) < len(eqs["d"].terms):
            eqs = {"t": toggle}
        if ctl("clk") in deps:
            eqs["clk"] = tabulate(support_of(ctl("clk")), control("clk", 0))
        if kind == "MACHINE":
            if ctl("reset") in deps:
                reset = tabulate(support_of(ctl("reset")), lambda env, drive, assigned: 1 - control("reset", 0)(env, drive, assigned))
                eqs["prn" if high else "clrn"] = reset
        else:
            for port in ("clrn", "prn"):
                if ctl(port) in deps:
                    eqs[port] = tabulate(support_of(ctl(port)), control(port, 1))
        registers[bit] = eqs
    return Netlist(d, order, outputs, registers, enables)
