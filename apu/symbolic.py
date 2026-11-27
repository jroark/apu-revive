"""Symbolic evaluation of an elaborated AHDL design with BDDs.

The same semantics as ahdl_eval.Design._run, but every value is a BDD over the design's primary
signals (inputs, register outputs "bit.q", and the outside value of bidirectional pins
"pin@ext"), and IF / CASE / TABLE become enable conditions instead of branches. Used by synthesis
for functions that depend on too many signals to tabulate.
"""
from .ahdl_eval import Unsupported
from .bdd import BDD


class Symbolic:
    def __init__(self, design, cut=()):
        """`cut`: internal nodes to treat as free variables (kept as their own logic cells);
        `node_function(name)` then gives each one's function of the primary signals and the
        other cut nodes."""
        self.d = d = design
        self.b = b = BDD()
        self.primary = list(d.inputs) + [bit + ".q" for bit in d.flops] + [p + "@ext" for p in d.bidir]
        self.cut = [n for n in d.nodes if n in set(cut)]
        self.env = {name: b.var(name) for name in self.primary + self.cut}
        self._settle()

    def node_function(self, name):
        return self.merged(name)

    # -- values ------------------------------------------------------------------------------
    def const(self, value, width):
        return [1 if (value >> i) & 1 else 0 for i in range(width - 1, -1, -1)]

    def value(self, node, width=None):
        d, b, kind = self.d, self.b, node[0]
        if kind == "vcc":
            return [1] * (width or 1)
        if kind == "gnd":
            return [0] * (width or 1)
        if kind == "num" or (kind == "name" and node[2] is None and node[1] in d.constants):
            from .ahdl_eval import _const
            v = _const(d.constants, node)
            w = width or node[2] if kind == "num" and node[2] else (width or max(v.bit_length(), 1))
            return self.const(v, w)
        if kind == "name" and node[2] is None and node[1] in d.states and node[1] not in d.groups:
            v, w = d.states[node[1]]
            return self.const(v, width or w)
        if kind == "group" and any(i is not None and i[0] not in ("name", "port", "group") for i in node[1]):
            out = []
            for item in node[1]:
                if item is not None:
                    out += self.value(item, 1 if item[0] == "num" and item[2] is None else None)
            return out
        if kind in ("name", "group", "port"):
            return [self.env.get(bit, 0) for bit in d._bits(node)]
        if kind == "un" and node[1] == "!":
            return [b.NOT(x) for x in self.value(node[2], width)]
        if kind == "bin":
            op, x, y = node[1], node[2], node[3]
            if op in ("==", "!="):
                left = self.value(x)
                right = self.value(y, len(left))
                if len(left) != len(right):
                    left = self.value(x, len(right))
                same = 1
                for p, q in zip(left, right):
                    same = b.AND(same, b.EQ(p, q))
                return [same if op == "==" else b.NOT(same)]
            if op in ("+", "-", "<", "<=", ">", ">="):
                left = self.value(x, width if op in "+-" else None)
                right = self.value(y, len(left))
                if len(right) > len(left):
                    left = self.value(x, len(right))
                w = max(len(left), len(right))
                left = [0] * (w - len(left)) + left
                right = [0] * (w - len(right)) + right
                if op == "+" or op == "-":
                    sub = op == "-"
                    carry = 1 if sub else 0
                    out = []
                    for p, q in zip(reversed(left), reversed(right)):
                        q = b.NOT(q) if sub else q
                        out.append(b.XOR(b.XOR(p, q), carry))
                        carry = b.OR(b.AND(p, q), b.AND(carry, b.XOR(p, q)))
                    return list(reversed(out))
                # comparisons, MSB first
                lt, eq = 0, 1
                for p, q in zip(left, right):
                    lt = b.OR(lt, b.AND(eq, b.AND(b.NOT(p), q)))
                    eq = b.AND(eq, b.EQ(p, q))
                return [{"<": lt, "<=": b.OR(lt, eq), ">": b.NOT(b.OR(lt, eq)), ">=": b.NOT(lt)}[op]]
            left = self.value(x, width)
            right = self.value(y, len(left))
            if len(left) != len(right):
                if len(right) == 1:
                    right = right * len(left)
                elif len(left) == 1:
                    left = left * len(right)
                else:
                    raise Unsupported("operands of different widths")
            ops = {"&": b.AND, "AND": b.AND, "#": b.OR, "OR": b.OR, "$": b.XOR, "XOR": b.XOR}
            inv = {"!&": b.AND, "NAND": b.AND, "!#": b.OR, "NOR": b.OR, "!$": b.XOR, "XNOR": b.XOR}
            if op in ops:
                return [ops[op](p, q) for p, q in zip(left, right)]
            if op in inv:
                return [b.NOT(inv[op](p, q)) for p, q in zip(left, right)]
            raise Unsupported("operator %s" % op)
        raise Unsupported("expression %r" % (kind,))

    def matches(self, pattern, bits):
        b = self.b
        if self.d._is_dont_care(pattern):
            return 1
        if pattern[0] == "pattern":
            out = 1
            for p, q in zip(pattern[1].rjust(len(bits), "0"), bits):
                if p != "X":
                    out = b.AND(out, q if p == "1" else b.NOT(q))
            return out
        want = self.value(pattern, len(bits))
        out = 1
        for p, q in zip(want, bits):
            out = b.AND(out, b.EQ(p, q))
        return out

    # -- statements --------------------------------------------------------------------------
    def assign(self, drive, assigned, target, value, en):
        b = self.b
        drive[target] = b.OR(drive.get(target, 0), b.AND(en, value))
        assigned[target] = b.OR(assigned.get(target, 0), en)

    def run(self, stmts, en, drive, assigned, defaults):
        b, d = self.b, self.d
        for st in stmts:
            kind = st[0]
            if kind == "eq" and st[2][0] == "call":
                targets = d._bits(st[1], lvalue=True)
                values = self.value(st[2][2][0], len(targets))
                if len(values) == 1 and len(targets) > 1:
                    values = values * len(targets)
                enable = self.value(st[2][2][1])[0]
                for t, v in zip(targets, values):
                    self.assign(drive, assigned, t + "~in", v, en)
                    self.assign(drive, assigned, t + "~oe", enable, en)
            elif kind == "eq":
                targets = d._bits(st[1], lvalue=True)
                values = self.value(st[2], len(targets))
                if len(values) == 1 and len(targets) > 1:
                    values = values * len(targets)
                if len(values) != len(targets):
                    raise Unsupported("assignment width mismatch")
                for t, v in zip(targets, values):
                    self.assign(drive, assigned, t, v, en)
            elif kind == "defaults":
                self.run(st[1], en, defaults, {}, {})
            elif kind == "if":
                rest = en
                for cond, body in st[1]:
                    c = self.value(cond)[0]
                    self.run(body, b.AND(rest, c), drive, assigned, defaults)
                    rest = b.AND(rest, b.NOT(c))
                self.run(st[2], rest, drive, assigned, defaults)
            elif kind == "case":
                subject = self.value(st[1])
                rest = en
                for values, body in st[2]:
                    if values is None:
                        hit = 1
                    else:
                        hit = 0
                        for v in values:
                            hit = b.OR(hit, self.matches(v, subject))
                    self.run(body, b.AND(rest, hit), drive, assigned, defaults)
                    rest = b.AND(rest, b.NOT(hit))
            elif kind == "table":
                ins = [self.value(e) for e in st[1]]
                outs = [d._bits(e, lvalue=True) for e in st[2]]
                for row_in, row_out in st[3]:
                    hit = en
                    for pattern, bits in zip(row_in, ins):
                        hit = b.AND(hit, self.matches(pattern, bits))
                    for expr, targets in zip(row_out, outs):
                        for t, v in zip(targets, self.value(expr, len(targets))):
                            self.assign(drive, assigned, t, v, hit)
            elif kind == "assert":
                pass
            else:
                raise Unsupported("statement %s" % kind)

    def merged(self, key):
        """The key's value as Design._settle's merged dict gives it: the driven value where it
        is assigned, else its DEFAULTS value, else 0."""
        b = self.b
        asg = self.assigned.get(key, 0)
        return b.OR(self.drive.get(key, 0), b.AND(b.NOT(asg), self.defaults.get(key, 0)))

    def _settle(self):
        d, b = self.d, self.b
        cut = set(self.cut)
        internal = [n for n in list(d.outputs) + list(d.nodes) if n not in cut]
        for name in internal:
            self.env.setdefault(name, 0)
        for _ in range(len(internal) + 2):
            drive, assigned, defaults = {}, {}, {}
            self.run(d.statements, 1, drive, assigned, defaults)
            self.drive, self.assigned, self.defaults = drive, assigned, defaults
            changed = False
            for name in internal:
                new = self.merged(name)
                if name in d.tri:
                    oe, ext = drive.get(name + "~oe", 0), self.env.get(name + "@ext", 0)
                    new = b.ite(oe, drive.get(name + "~in", 0), ext)
                elif name in d.bidir:
                    new = b.ite(assigned.get(name, 0), new, self.env.get(name + "@ext", 0))
                if new != self.env[name]:
                    self.env[name], changed = new, True
            if not changed:
                return
        raise Unsupported("combinational loop did not settle")
