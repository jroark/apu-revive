"""Reduce a flat AHDL design to two-level equations — the form a MAX 7000 macrocell implements.

For every output pin and every register this produces a minimised sum of products over the
design's inputs and register outputs, plus the register's clock, clear and preset equations.
The whole design is first evaluated symbolically (apu.symbolic: every signal a BDD over the
inputs, register outputs and bidirectional pins). A function whose true support is at most
MAX_SUPPORT signals is then tabulated from its BDD and minimised exactly enough (Quine-McCluskey
prime implicants with a greedy cover); a wider one gets an irredundant cover straight from the
BDD (Minato-Morreale). If the symbolic evaluator cannot handle a construct, each function is
instead tabulated by settling the design for every assignment of its structural support.

Nothing here knows about a device: no product-term limits, expanders, pin assignment or fuses.
"""
import itertools

from . import ahdl_eval
from .symbolic import Symbolic

MAX_SUPPORT = 12


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


class _Concrete:
    """What a signal function sees during exhaustive tabulation: one settled evaluation."""

    def __init__(self, env, drive, assigned):
        self._env, self._drive, self._assigned = env, drive, assigned

    def env(self, name):
        return self._env[name]

    def merged(self, key):
        return self._drive.get(key, 0)

    def assigned(self, key):
        return int(key in self._assigned)

    @staticmethod
    def ite(c, x, y):
        return x if c else y

    @staticmethod
    def OR(x, y):
        return x | y

    @staticmethod
    def XOR(x, y):
        return x ^ y

    @staticmethod
    def NOT(x):
        return 1 - x


class _Bdd:
    """The same interface over BDDs, for functions too wide to tabulate."""

    def __init__(self, sym):
        self.s, b = sym, sym.b
        self.ite, self.OR, self.XOR, self.NOT = b.ite, b.OR, b.XOR, b.NOT

    def env(self, name):
        return self.s.env.get(name, 0)

    def merged(self, key):
        return self.s.merged(key)

    def assigned(self, key):
        return self.s.assigned.get(key, 0)

    def node(self, name):
        return self.s.node_function(name)


def _bdd_equation(b, f):
    """Equation for BDD f: the smaller of irredundant covers of f and of its complement."""
    on, off = b.isop(f), b.isop(b.NOT(f))
    invert = len(off) < len(on)
    return Equation(off if invert else on, invert)


class Netlist:
    """outputs: {pin: Equation}; registers: {bit: {"d" or "t": Equation, "clk": ..., "clrn": ...,
    "prn": ...}} ("t" when the register is better built as a T flip-flop)
    (absent control equations mean not used). Register outputs appear in equations as "bit.q",
    and the value arriving on a bidirectional pin as "pin@ext"."""

    def __init__(self, design, support, outputs, registers, enables=None, nodes=None):
        self.design, self.support, self.outputs, self.registers = design, support, outputs, registers
        self.enables = enables or {}                       # {pin: output-enable Equation} for tri-state pins
        self.nodes = nodes or {}                           # {node: Equation}: buried logic cells, read by name

    def report(self) -> str:
        lines = ["%s = %s;  -- buried" % (n, eq.text()) for n, eq in self.nodes.items()]
        for pin, eq in self.outputs.items():
            lines.append("%s = %s;" % (pin, eq.text()))
            if pin in self.enables:
                lines.append("%s.oe = %s;" % (pin, self.enables[pin].text()))
        for bit, eqs in self.registers.items():
            for port, eq in eqs.items():
                lines.append("%s.%s = %s;" % (bit, port, eq.text()))   # .t: toggle input of a T flip-flop
        return "\n".join(lines)


def synthesise(text: str, resolver=None, keep="auto") -> Netlist:
    """Two-level equations for a design. `keep`: internal nodes to implement as buried logic
    cells of their own instead of flattening them into every equation that reads them --
    "auto" chooses them (a node is kept when that lowers the macrocell count), () keeps none."""
    d = ahdl_eval.Simulator(text, resolver).design
    deps = d.dependencies()
    try:
        Symbolic(d)
    except ahdl_eval.Unsupported:
        return _build(d, deps, None, True)              # settle assignment by assignment
    if keep == "auto":
        keep = _choose_kept(d, deps)
    return _build(d, deps, tuple(keep), True)


def _choose_kept(d, deps):
    """Greedy choice of nodes to keep as logic cells, judged by the macrocells the netlist needs
    (with fast irredundant covers; the final netlist is minimised properly)."""
    from . import fit as fitting

    def cost(kept):
        try:
            return fitting.macrocells_needed(_build(d, deps, kept, False))
        except fitting.DoesNotFit:
            return 10 ** 6
    plain = Symbolic(d)
    candidates = [n for n in d.nodes if len(plain.b.support(plain.env[n])) >= 2]
    kept, best = (), cost(())
    improved = True
    while improved:
        improved = False
        for n in candidates:
            if n in kept:
                continue
            trial = cost(kept + (n,))
            if trial < best:
                kept, best, improved = kept + (n,), trial, True
    # a node kept early may have become pointless once later ones were kept
    for n in list(kept):
        smaller = tuple(k for k in kept if k != n)
        if cost(smaller) <= best:
            kept, best = smaller, cost(smaller)
    return kept


def _build(d, deps, kept, exact) -> Netlist:
    """The netlist with the nodes in `kept` as logic cells (kept=None: no BDDs, exhaustive
    tabulation). `exact` False uses irredundant covers throughout (fast, for comparing choices)."""
    machine_of = {bit: name for name, (bits, _, _) in d.machines.items() for bit in bits}
    order = list(d.inputs) + [bit + ".q" for bit in d.flops] + [b + "@ext" for b in d.bidir]
    symbolic = None if kept is None else Symbolic(d, kept)
    if symbolic is not None:
        order += symbolic.cut

    def support_of(*keys):
        found = set()
        for key in keys:
            found |= deps.get(key, set())
        return [s for s in order if s in found]

    def tabulate(support, fn):
        """Minimised equation of fn. With BDDs: exact two-level minimisation over the function's
        true support when that is at most MAX_SUPPORT signals, else an irredundant cover.
        Without: exhaustive tabulation over the structural support."""
        if symbolic is not None:
            b = symbolic.b
            f = fn(_Bdd(symbolic))
            names = [s for s in order if s in b.support(f)]
            if len(names) > MAX_SUPPORT or not exact:
                return _bdd_equation(b, f)
            table = {values: b.evaluate(f, dict(zip(names, values)))
                     for values in itertools.product((0, 1), repeat=len(names))}
            return _equation(table, names)
        if len(support) > MAX_SUPPORT:
            raise TooLarge("%s depends on %d signals; the exhaustive method stops at %d" % (fn, len(support), MAX_SUPPORT))
        table = {}
        base = {s: 0 for s in order}
        for values in itertools.product((0, 1), repeat=len(support)):
            env = dict(base)
            env.update(zip(support, values))
            drive, assigned = d._settle(env)
            table[values] = fn(_Concrete(env, drive, assigned))
        return _equation(table, support)

    outputs, enables = {}, {}
    for pin in d.outputs:
        if pin in d.tri:                                  # the buffer's input and its enable, separately
            outputs[pin] = tabulate(support_of(pin + "~in"), lambda c, pin=pin: c.merged(pin + "~in"))
            enables[pin] = tabulate(support_of(pin + "~oe"), lambda c, pin=pin: c.merged(pin + "~oe"))
        else:
            outputs[pin] = tabulate(support_of(pin), lambda c, pin=pin: c.env(pin))
    registers = {}
    for bit, kind in d.flops.items():
        owner = machine_of.get(bit, bit)
        ctl = lambda port: "%s.%s" % (owner, port)

        def control(port, default):
            return lambda c: c.ite(c.assigned(ctl(port)), c.merged(ctl(port)), default)
        if kind == "MACHINE":
            bits, _, first = d.machines[owner]
            nexts = [b + ".next" for b in bits]
            high = (first >> (len(bits) - 1 - bits.index(bit))) & 1

            def nxt(c, bit=bit, nexts=nexts):
                any_next = 0
                for key in nexts:
                    any_next = c.OR(any_next, c.assigned(key))
                value = c.ite(any_next, c.merged(bit + ".next"), c.env(bit + ".q"))
                return c.ite(control("ena", 1)(c), value, c.env(bit + ".q"))
            data_keys = nexts + [ctl("ena")]
        else:
            port = "t" if kind.startswith("T") else "d"

            def nxt(c, bit=bit, port=port):
                value = c.merged("%s.%s" % (bit, port))
                if port == "t":
                    value = c.XOR(value, c.env(bit + ".q"))
                return c.ite(control("ena", 1)(c), value, c.env(bit + ".q"))
            data_keys = ["%s.%s" % (bit, port), ctl("ena")]
        deps.setdefault("%s#data" % bit, set()).update({bit + ".q"})
        support = support_of("%s#data" % bit, *data_keys)
        eqs = {"d": tabulate(support, nxt)}
        # A MAX 7000 macrocell can also run its register as a T flip-flop (the XOR feeds back
        # the register output), so keep the toggle form when it needs fewer product terms --
        # the original compiler does this for counters (TFFE in its report).
        toggle = tabulate(support, lambda c, nxt=nxt, bit=bit: c.XOR(nxt(c), c.env(bit + ".q")))
        if len(toggle.terms) < len(eqs["d"].terms):
            eqs = {"t": toggle}
        if ctl("clk") in deps:
            eqs["clk"] = tabulate(support_of(ctl("clk")), control("clk", 0))
        if kind == "MACHINE":
            if ctl("reset") in deps:
                reset = tabulate(support_of(ctl("reset")), lambda c: c.NOT(control("reset", 0)(c)))
                eqs["prn" if high else "clrn"] = reset
        else:
            for port in ("clrn", "prn"):
                if ctl(port) in deps:
                    eqs[port] = tabulate(support_of(ctl(port)), control(port, 1))
        for port in ("clrn", "prn"):                    # tied inactive: the register has no such control
            if port in eqs and eqs[port].invert and not eqs[port].terms:
                del eqs[port]
        registers[bit] = eqs
    nodes = {}
    if symbolic is not None:
        for name in symbolic.cut:
            nodes[name] = tabulate(None, lambda c, name=name: c.node(name))
    # drop registers and kept nodes that nothing observable depends on
    live, todo = set(), [n for eq in list(outputs.values()) + list(enables.values()) for t in eq.terms for n in t]
    while todo:
        n = todo.pop()
        if n in live:
            continue
        live.add(n)
        for eq in list(registers.get(n[:-2], {}).values()) if n.endswith(".q") else []:
            todo += [m for t in eq.terms for m in t]
        if n in nodes:
            todo += [m for t in nodes[n].terms for m in t]
    registers = {bit: eqs for bit, eqs in registers.items() if bit + ".q" in live}
    nodes = {n: eq for n, eq in nodes.items() if n in live}
    return Netlist(d, order, outputs, registers, enables, nodes)


class NetlistSimulator:
    """Functional simulation of a synthesised Netlist: the equations a device would implement,
    with the same clocking rules as ahdl_eval.Simulator (registers clocked by a 0 -> 1 change
    take their pre-edge input; clear and preset act at once, clear first)."""

    def __init__(self, net, q=None):
        self.net = net
        self.q = {bit: (q or {}).get(bit, 0) for bit in net.registers}   # power-up values, default 0
        d = net.design
        self.inputs = {name: 0 for name in d.inputs}
        self.inputs.update({p + "@ext": 0 for p in d.bidir})
        self._settle()

    def _settle(self):
        env = dict(self.inputs)
        env.update({bit + ".q": v for bit, v in self.q.items()})
        pending = dict(self.net.nodes)
        while pending:                                     # kept nodes read each other acyclically
            ready = [n for n, eq in pending.items() if all(m in env for t in eq.terms for m in t)]
            if not ready:
                raise ahdl_eval.Unsupported("kept nodes form a loop")
            for n in ready:
                env[n] = pending.pop(n).evaluate(env)
        for pin, eq in self.net.outputs.items():
            value = eq.evaluate(env)
            if pin in self.net.enables and not self.net.enables[pin].evaluate(env):
                value = env.get(pin + "@ext", 0)              # not driven: whatever is outside
            env[pin] = value
        self.env = env

    def _port(self, bit, port, default):
        eq = self.net.registers[bit].get(port)
        return eq.evaluate(self.env) if eq else default

    def _next(self, bit):
        eqs = self.net.registers[bit]
        if "t" in eqs:
            return self.q[bit] ^ eqs["t"].evaluate(self.env)
        return eqs["d"].evaluate(self.env)

    def _async(self):
        changed = False
        for bit in self.q:
            value = self.q[bit]
            if not self._port(bit, "clrn", 1):
                value = 0
            elif not self._port(bit, "prn", 1):
                value = 1
            if value != self.q[bit]:
                self.q[bit], changed = value, True
        return changed

    def set(self, **inputs):
        d = self.net.design
        before = {bit: self._port(bit, "clk", 0) for bit in self.q}
        captured = {bit: self._next(bit) for bit in self.q}
        for name, value in inputs.items():
            if name in d.groups:
                bits = d.groups[name]
                for i, b in enumerate(bits):
                    self.inputs[b + "@ext" if b in d.bidir else b] = (value >> (len(bits) - 1 - i)) & 1
            elif name in d.bidir:
                self.inputs[name + "@ext"] = int(bool(value))
            elif name in self.inputs:
                self.inputs[name] = int(bool(value))
            else:
                raise ValueError("no input named %s" % name)
        self._settle()
        for bit in [b for b in self.q if not before[b] and self._port(b, "clk", 0)]:
            self.q[bit] = captured[bit]
        for _ in range(4):
            self._settle()
            if not self._async():
                break
        self._settle()


def _is_single_term(eq):
    """apu.generate._single_term accepts it: one product term, or the complement of one literal."""
    if eq.invert:
        return len(eq.terms) == 1 and len(eq.terms[0]) == 1
    return len(eq.terms) == 1


def hoist_controls(net):
    """A register's clock, clear or preset must be one product term (terms 3, 2 and 4 of its
    macrocell). One that is not is computed in a node of its own (one macrocell, shared by every
    register with the same control) and the register reads that node as a single literal, as
    the vendor's compilers do. Changes `net` in place and returns it; idempotent."""
    made = {}
    for bit, eqs in net.registers.items():
        for key in ("clk", "clrn", "prn"):
            eq = eqs.get(key)
            if eq is None:
                continue
            asserted = eq if key == "clk" else Equation(eq.terms, not eq.invert)
            if _is_single_term(asserted):
                continue
            text = eq.text()
            if text not in made:
                name = "~ctl%d" % len([n for n in net.nodes if n.startswith("~ctl")])
                net.nodes[name] = Equation([dict(t) for t in eq.terms], eq.invert)
                made[text] = name
            eqs[key] = Equation([{made[text]: 1}])
    return net
