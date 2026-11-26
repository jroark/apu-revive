"""Evaluate the combinational part of a parsed AHDL design (the start of native simulation).

Supported: single-bit and group ports, Boolean equations, IF / CASE / TABLE, DEFAULTS, numeric
and group assignments, VCC / GND, the logic operators, == and !=; D and T flip-flops (DFF, DFFE,
TFF, TFFE) with clock, enable, asynchronous clear and preset; state machines with clock, reset
and enable. Inline DFF(...) / TFF(...) calls and the buffer primitives are handled, and instances of
other AHDL designs are flattened when a resolver can supply their text. TRI(in, oe) as the
whole right-hand side of an equation and BIDIR pins are modelled (a pin shows the buffer's value
when enabled, otherwise what the outside drives). Library macrofunctions and machine ports
raise Unsupported, so a result is only returned for designs this code fully understands.

Sequential designs are simulated functionally (no delays): `Simulator.set()` changes inputs,
and every register whose clock went from 0 to 1 takes the value its input had before the edge.

AHDL semantics relied on: assignments to the same node are ORed together; a node that is never
assigned takes its DEFAULTS value, or GND.
"""
from . import ahdl


class Unsupported(Exception):
    pass


def _const(env_consts, node):
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "name" and node[2] is None and node[1] in env_consts:
        return env_consts[node[1]]
    if kind == "un" and node[1] == "-":
        return -_const(env_consts, node[2])
    raise Unsupported("not a constant: %r" % (node,))


def flatten(tree, resolver=None, prefix="", counter=None):
    """Expand instances of other designs into one flat design.

    `resolver(name)` returns the AHDL text of the design called `name`, or None. Every name of
    an instantiated design is prefixed with "<instance>/"; its ports become nodes of the parent.
    Returns (constants, ports, variables, statements) for the flat design.
    """
    counter = counter if counter is not None else [0]
    _, _, constants, includes, protos, sub = tree
    _, _, ports, variables, statements = sub
    protos = {name: (ins, outs) for name, ins, outs in protos}

    def generate_condition(node):
        """Value of an IF ... GENERATE condition. The target is a MAX 7000 device, so the
        FAMILY_* tests other than MAX 7000 are false; USED(port) is taken as true."""
        kind = node[0]
        if kind == "num":
            return node[1]
        if kind == "call":
            name = node[1].lower()
            if name == "used":
                return 1
            if name.startswith("family_"):
                return int("max7000" in name or name in ("family_max7k", "family_max"))
            raise Unsupported("function %s in a GENERATE condition" % node[1])
        if kind == "un" and node[1] == "!":
            return int(not generate_condition(node[2]))
        if kind == "bin":
            a, b = generate_condition(node[2]), generate_condition(node[3])
            ops = {"==": a == b, "!=": a != b, "&": a and b, "AND": a and b, "#": a or b, "OR": a or b}
            if node[1] in ops:
                return int(bool(ops[node[1]]))
        raise Unsupported("GENERATE condition %r" % (node[0],))

    def resolve_generates(items, is_variables):
        out = []
        for item in items:
            node = item[1] if is_variables else item
            if isinstance(node, tuple) and node and node[0] == "ifgen":
                chosen = node[2] if generate_condition(node[1]) else node[3]
                out.extend(resolve_generates(chosen, is_variables))
            else:
                out.append(item)
        return out

    variables = resolve_generates(variables, True)
    statements = resolve_generates(statements, False)
    for include in includes:                              # prototypes from INCLUDE "name[.inc]"
        text = resolver(include if include.lower().endswith(".inc") else include + ".inc") if resolver else None
        if text:
            try:
                for name, ins, outs in ahdl.parse_prototypes(text):
                    protos.setdefault(name, (ins, outs))
            except ahdl.AhdlError:
                pass
    instances = {}
    for names, vtype in variables:
        if vtype[0] == "instance":
            for entry in names:
                if entry[2] is not None:
                    raise Unsupported("arrays of instances")
                instances[entry[1]] = vtype[1]
    builtin = set(REGISTER_ARGS) | set(BUFFERS) | {"EXP", "TRI"}
    out_vars, out_stmts, out_consts = [], [], []

    def load(kind):
        text = resolver(kind) if resolver else None
        if text is None:
            raise Unsupported("no design found for %s" % kind)
        return ahdl.parse(text)

    def expand(inst, kind):
        """Flatten one instance; returns its (input entries, output entries) under the prefix."""
        sub_tree = load(kind)
        c, p, v, st = flatten(sub_tree, resolver, prefix + inst + "/", counter)
        out_consts.extend(c)
        ins, outs, defaults = [], [], []
        for names, direction, default in p:
            if direction not in ("INPUT", "OUTPUT"):
                raise Unsupported("%s port on an instantiated design" % direction)
            out_vars.append((names, ("prim", "NODE")))
            (ins if direction == "INPUT" else outs).extend(names)
            if default is not None:
                defaults.extend(("eq", n if n[2] is None else ("name", n[1], ()), default) for n in names)
        out_vars.extend(v)
        if defaults:
            out_stmts.append(("defaults", defaults))
        out_stmts.extend(st)
        return ins, outs

    def whole(entry):
        return entry if entry[2] is None else ("name", entry[1], ())

    declared = {e[1] for names, _, _ in ports for e in names}
    declared |= {e[1] for names, vtype in variables if names for e in names}

    def rename(node, extra):
        if not isinstance(node, tuple) or not node:
            return node
        kind = node[0]
        if node == ("name", "x", None) and "x" not in declared:
            return ("x",)                                  # AHDL's don't-care, not a signal
        if kind == "name":
            rng = node[2]
            if rng:
                rng = tuple(rename(r, extra) for r in rng)
            return ("name", prefix + node[1], rng)
        if kind == "port":
            if node[1][1] in instances:
                rng = node[2][2]
                if rng:
                    rng = tuple(rename(r, extra) for r in rng)
                return ("name", "%s%s/%s" % (prefix, node[1][1], node[2][1]), rng)
            return ("port", rename(node[1], extra), node[2])
        if kind == "un":
            return ("un", node[1], rename(node[2], extra))
        if kind == "bin":
            return ("bin", node[1], rename(node[2], extra), rename(node[3], extra))
        if kind == "group":
            return ("group", [rename(i, extra) if i else None for i in node[1]])
        if kind == "call":
            args = [rename(a, extra) if a else None for a in node[2]]
            if node[1].upper() in builtin:
                return ("call", node[1], args)
            counter[0] += 1
            inst = "~i%d" % counter[0]
            ins, outs = expand(inst, node[1])
            if node[1] in protos:                            # the prototype fixes the argument order
                order = {e[1]: e for e in ins}
                ins = [("name", "%s%s/%s" % (prefix, inst, e[1]), e[2]) for e in protos[node[1]][0] if True]
                outs = [("name", "%s%s/%s" % (prefix, inst, e[1]), e[2]) for e in protos[node[1]][1]]
            def split(node):                             # a constant-range group, bit by bit
                if node and node[0] == "name" and node[2] and len(node[2]) == 2 and \
                        node[2][0][0] == "num" and node[2][1][0] == "num":
                    hi, lo = node[2][0][1], node[2][1][1]
                    step = -1 if hi >= lo else 1
                    return [("name", node[1], (("num", i, None),)) for i in range(hi, lo + step, step)]
                return [node]

            def port_width(entry):
                rng = entry[2]
                if rng and len(rng) == 2 and rng[0][0] == "num" and rng[1][0] == "num":
                    return abs(rng[0][1] - rng[1][1]) + 1
                return 1
            if len(args) < len(ins) and any(a is not None and len(split(a)) > 1 for a in args):
                bits = [b for a in args for b in (split(a) if a is not None else [None])]
                k = 0
                for port in ins:
                    w = port_width(port)
                    part, k = bits[k:k + w], k + w
                    if part and None not in part:
                        extra.append(("eq", whole(port), part[0] if w == 1 else ("group", part)))
            else:
                for port, arg in zip(ins, args):
                    if arg is not None:
                        extra.append(("eq", whole(port), arg))
            outs = [whole(o) for o in outs]
            return outs[0] if len(outs) == 1 else ("group", outs)
        return node

    def stmts(items):
        result = []
        for st in items:
            extra = []
            kind = st[0]
            if kind == "eq":
                lhs, rhs = rename(st[1], extra), rename(st[2], extra)
                if lhs[0] == "group" and rhs[0] == "group" and None in lhs[1]:
                    def width(item):                    # an empty position stands for one bit
                        if item is None or item[0] != "name" or not item[2]:
                            return 1
                        if len(item[2]) == 2 and item[2][0][0] == "num" and item[2][1][0] == "num":
                            return abs(item[2][0][1] - item[2][1][1]) + 1
                        return 1
                    values, k = [v for v in rhs[1]], 0
                    for item in lhs[1]:
                        w = width(item)
                        part = values[k:k + w]
                        k += w
                        if item is not None and part and None not in part:
                            result.append(("eq", item, part[0] if w == 1 else ("group", part)))
                else:
                    result.append(("eq", lhs, rhs))
            elif kind == "if":
                result.append(("if", [(rename(c, extra), stmts(b)) for c, b in st[1]], stmts(st[2])))
            elif kind == "case":
                result.append(("case", rename(st[1], extra),
                               [(None if v is None else [rename(x, extra) for x in v], stmts(b)) for v, b in st[2]]))
            elif kind == "table":
                result.append(("table", [rename(e, extra) for e in st[1]], [rename(e, extra) for e in st[2]],
                               [([rename(e, extra) for e in a], [rename(e, extra) for e in b]) for a, b in st[3]]))
            elif kind == "defaults":
                result.append(("defaults", stmts(st[1])))
            else:
                result.append(st)
            result.extend(extra)
        return result

    flat_ports = [([rename(n, []) for n in names], direction, default if default is None else rename(default, []))
                  for names, direction, default in ports]
    for name, expr in constants:
        out_consts.append((prefix + name, rename(expr, [])))
    for names, vtype in variables:
        if vtype[0] == "instance":
            for entry in names:
                expand(entry[1], vtype[1])
            continue
        if vtype[0] == "machine" and vtype[2]:
            vtype = ("machine", [rename(b, []) for b in vtype[1]],
                     [(prefix + state, value if value is None else rename(value, [])) for state, value in vtype[2]])
        out_vars.append(([rename(n, []) for n in names], vtype))
    body = stmts(statements)
    return out_consts, flat_ports, out_vars, out_stmts + body


REGISTER_ARGS = {"DFF": ("d", "clk", "clrn", "prn"), "DFFE": ("d", "clk", "clrn", "prn", "ena"),
                 "TFF": ("t", "clk", "clrn", "prn"), "TFFE": ("t", "clk", "clrn", "prn", "ena")}
BUFFERS = ("LCELL", "MCELL", "SOFT", "GLOBAL", "WIRE")      # logically transparent


def _inline(stmts, flops):
    """Replace inline primitive calls: DFF(d, clk, clrn, prn) and friends become anonymous
    registers with their port equations added after the statement; buffer primitives vanish;
    EXP(a) becomes !a. Returns the new statement list."""
    def expr(node, extra, top=False):
        if not isinstance(node, tuple) or not node:
            return node
        if node[0] == "call":
            name, args = node[1].upper(), [expr(a, extra) if a else None for a in node[2]]
            if name in REGISTER_ARGS:
                reg = "~ff%d" % (len(flops) + 1)
                flops[reg] = name
                for port, arg in zip(REGISTER_ARGS[name], args):
                    if arg is not None:
                        extra.append(("eq", ("port", ("name", reg, None), ("name", port, None)), arg))
                return ("name", reg, None)
            if name == "TRI" and len(args) == 2 and top:
                return ("call", "tri", args)
            if name in BUFFERS and len(args) == 1:
                return args[0]
            if name == "EXP" and len(args) == 1:
                return ("un", "!", args[0])
            raise Unsupported("call to %s" % node[1])
        if node[0] == "un":
            return ("un", node[1], expr(node[2], extra))
        if node[0] == "bin":
            return ("bin", node[1], expr(node[2], extra), expr(node[3], extra))
        if node[0] == "group":
            return ("group", [expr(i, extra) if i else None for i in node[1]])
        return node

    out = []
    for st in stmts:
        extra = []
        if st[0] == "eq":
            out.append(("eq", st[1], expr(st[2], extra, top=True)))
        elif st[0] == "if":
            out.append(("if", [(expr(c, extra), _inline(b, flops)) for c, b in st[1]], _inline(st[2], flops)))
        elif st[0] == "case":
            out.append(("case", expr(st[1], extra), [(v, _inline(b, flops)) for v, b in st[2]]))
        elif st[0] == "defaults":
            out.append(("defaults", _inline(st[1], flops)))
        else:
            out.append(st)
        out.extend(extra)
    return out


class Design:
    def __init__(self, text: str, resolver=None):
        tree = ahdl.parse(text)
        self.titles, self.name = tree[1], tree[5][1]
        constants, ports, variables, self.statements = flatten(tree, resolver)
        inline_flops = {}
        self.statements = _inline(self.statements, inline_flops)
        self.constants = {}
        for name, expr in constants:
            self.constants[name] = _const(self.constants, expr)
        self.inputs, self.outputs, self.groups, self.aliases, self.bidir = [], [], {}, {}, []
        self.tri = set()                                   # signals driven through TRI(in, oe)
        for names, direction, _ in ports:
            for entry in names:
                bits = self._declare(entry)
                if direction not in ("INPUT", "OUTPUT", "BIDIR"):
                    raise Unsupported("port direction %s" % direction)
                (self.inputs if direction == "INPUT" else self.outputs).extend(bits)
                if direction == "BIDIR":
                    self.bidir.extend(bits)
        self.nodes, self.flops, self.machines, self.states = [], {}, {}, {}
        self.known = set(self.inputs) | set(self.outputs)
        for names, vtype in variables:
            if vtype == ("prim", "NODE"):
                for entry in names:
                    self.nodes.extend(self._declare(entry))
            elif vtype[0] == "prim" and vtype[1] in ("DFF", "DFFE", "TFF", "TFFE"):
                for entry in names:
                    for bit in self._declare(entry):
                        self.flops[bit] = vtype[1]
            elif vtype[0] == "machine" and vtype[2]:
                for entry in names:
                    self._declare_machine(entry[1], vtype[1], vtype[2])
            else:
                raise Unsupported("variable type %r" % (vtype[:2],))
        self.known |= set(self.nodes)
        self.flops.update(inline_flops)
        self._find_tri(self.statements)

    def _find_tri(self, stmts):
        for st in stmts:
            if st[0] == "eq" and st[2][0] == "call":
                self.tri.update(self._bits(st[1], lvalue=True))
            elif st[0] == "if":
                for _, body in st[1]:
                    self._find_tri(body)
                self._find_tri(st[2])
            elif st[0] == "case":
                for _, body in st[2]:
                    self._find_tri(body)
            elif st[0] == "defaults":
                self._find_tri(st[1])

    def _declare_machine(self, name, bit_names, states):
        values, nxt = {}, 0
        for state, value in states:
            if value is not None:
                nxt = _const(self.constants, value)
            values[state] = nxt
            nxt += 1
        width = max(max(values.values()).bit_length(), len(bit_names), 1)
        bits = ["%s~%d" % (name, i) for i in range(width - 1, -1, -1)]
        self.groups[name] = bits
        for bit in bits:
            self.flops[bit] = "MACHINE"
        self.machines[name] = (bits, values, next(iter(values.values())))
        for state, value in values.items():
            self.states.setdefault(state, (value, width))
        for entry, bit in zip([e for b in bit_names for e in self._bits(b)] if False else
                              [b[1] for b in bit_names], bits[len(bits) - len(bit_names):]):
            self.aliases[entry] = bit

    def _declare(self, entry):
        _, name, rng = entry
        if rng is None:
            return [name]
        hi, lo = (_const(self.constants, rng[0]), _const(self.constants, rng[-1]))
        step = -1 if hi >= lo else 1
        bits = ["%s%d" % (name, i) for i in range(hi, lo + step, step)]
        self.groups[name] = bits                           # most significant first
        return bits

    # -- widths and bit lists -------------------------------------------------------------
    def _bits(self, node, lvalue=False):
        """Signal names for an l-value or a plain name/group reference, MSB first. A flip-flop
        named on the left of an equation means its input; elsewhere, its output."""
        kind = node[0]
        if kind == "name":
            _, name, rng = node
            if rng is None:
                bits = list(self.groups[name]) if name in self.machines else [self.aliases.get(name, name)]
            elif rng == () and name not in self.groups:
                # a group whose bits were declared one by one (e.g. by a converted schematic)
                import re as _re
                found = sorted((int(m.group(1)), b) for b in self.known
                               for m in [_re.fullmatch(_re.escape(name) + r"(\d+)", b)] if m)
                if not found:
                    raise Unsupported("unknown group %s" % name)
                bits = [b for _, b in reversed(found)]
            elif rng == ():
                bits = list(self.groups[name])
            elif len(rng) == 1:
                bits = ["%s%d" % (name, _const(self.constants, rng[0]))]
            else:
                hi, lo = _const(self.constants, rng[0]), _const(self.constants, rng[1])
                step = -1 if hi >= lo else 1
                bits = ["%s%d" % (name, i) for i in range(hi, lo + step, step)]
            return [self._pin(b, None, lvalue) for b in bits]
        if kind == "port":
            port = node[2][1].lower()
            if node[1][1] in self.machines:
                return ["%s.%s" % (node[1][1], port)]
            return [self._pin(b.split(".")[0], port, lvalue) for b in self._bits(node[1], lvalue=True)]
        if kind == "group":
            return [b for item in node[1] if item is not None for b in self._bits(item, lvalue)]
        raise Unsupported("not a signal reference: %r" % (node,))

    def _pin(self, bit, port, lvalue):
        kind = self.flops.get(bit)
        if kind is None:
            if port is not None:
                raise Unsupported("port .%s of something that is not a register" % port)
            return bit
        if port is None:
            port = ("next" if kind == "MACHINE" else "t" if kind.startswith("T") else "d") if lvalue else "q"
        return "%s.%s" % (bit, port)

    def _value(self, node, env, width=None):
        """Evaluate to a list of bits (MSB first)."""
        kind = node[0]
        if kind == "vcc":
            return [1] * (width or 1)
        if kind == "gnd":
            return [0] * (width or 1)
        if kind == "num" or (kind == "name" and node[2] is None and node[1] in self.constants):
            value = _const(self.constants, node)
            w = width or node[2] if kind == "num" and node[2] else (width or max(value.bit_length(), 1))
            return [(value >> i) & 1 for i in range(w - 1, -1, -1)]
        if kind == "name" and node[2] is None and node[1] in self.states and node[1] not in self.groups:
            value, w = self.states[node[1]]
            w = width or w
            return [(value >> i) & 1 for i in range(w - 1, -1, -1)]
        if kind == "group" and any(i is not None and i[0] not in ("name", "port", "group") for i in node[1]):
            out = []
            for item in node[1]:
                if item is not None:
                    out += self._value(item, env, 1 if item[0] == "num" and item[2] is None else None)
            return out
        if kind in ("name", "group", "port"):
            bits = self._bits(node)
            for b in bits:
                if b not in env and b.split(".")[0] not in self.flops and b not in self.known:
                    raise Unsupported("unknown signal %s" % b)
            return [env.get(b, 0) for b in bits]
        if kind == "un" and node[1] == "!":
            return [1 - b for b in self._value(node[2], env, width)]
        if kind == "bin":
            op, a, b = node[1], node[2], node[3]
            if op in ("==", "!="):
                left = self._value(a, env)
                right = self._value(b, env, len(left))
                if len(left) != len(right):
                    left = self._value(a, env, len(right))
                same = int(left == right)
                return [same if op == "==" else 1 - same]
            if op in ("+", "-", "<", "<=", ">", ">="):
                left = self._value(a, env, width if op in "+-" else None)
                right = self._value(b, env, len(left))
                if len(right) > len(left):
                    left = self._value(a, env, len(right))
                x = int("".join(map(str, left)), 2)
                y = int("".join(map(str, right)), 2)
                w = max(len(left), len(right))
                if op in "+-":
                    total = (x + y if op == "+" else x - y) & ((1 << w) - 1)
                    return [(total >> i) & 1 for i in range(w - 1, -1, -1)]
                return [int({"<": x < y, "<=": x <= y, ">": x > y, ">=": x >= y}[op])]
            left = self._value(a, env, width)
            right = self._value(b, env, len(left))
            if len(left) != len(right):
                if len(right) == 1:
                    right = right * len(left)
                elif len(left) == 1:
                    left = left * len(right)
                else:
                    raise Unsupported("operands of different widths")
            table = {"&": lambda x, y: x & y, "AND": lambda x, y: x & y,
                     "#": lambda x, y: x | y, "OR": lambda x, y: x | y,
                     "$": lambda x, y: x ^ y, "XOR": lambda x, y: x ^ y,
                     "!&": lambda x, y: 1 - (x & y), "NAND": lambda x, y: 1 - (x & y),
                     "!#": lambda x, y: 1 - (x | y), "NOR": lambda x, y: 1 - (x | y),
                     "!$": lambda x, y: 1 - (x ^ y), "XNOR": lambda x, y: 1 - (x ^ y)}
            if op not in table:
                raise Unsupported("operator %s" % op)
            return [table[op](x, y) for x, y in zip(left, right)]
        raise Unsupported("expression %r" % (node[0],))

    # -- statements -------------------------------------------------------------------------
    def _is_dont_care(self, node):
        return node[0] == "x" or (node == ("name", "x", None) and "x" not in self.known and "x" not in self.flops)

    def _matches(self, pattern, bits):
        if self._is_dont_care(pattern):
            return True
        if pattern[0] == "pattern":
            return all(p == "X" or int(p) == b for p, b in zip(pattern[1].rjust(len(bits), "0"), bits))
        return None

    def _run(self, stmts, env, drive, defaults):
        for st in stmts:
            kind = st[0]
            if kind == "eq" and st[2][0] == "call":            # target = TRI(in, oe)
                targets = self._bits(st[1], lvalue=True)
                values = self._value(st[2][2][0], env, len(targets))
                if len(values) == 1 and len(targets) > 1:
                    values = values * len(targets)
                enable = self._value(st[2][2][1], env)[0]
                for t, v in zip(targets, values):
                    drive[t + "~in"] = drive.get(t + "~in", 0) | v
                    drive[t + "~oe"] = drive.get(t + "~oe", 0) | enable
            elif kind == "eq":
                targets = self._bits(st[1], lvalue=True)
                values = self._value(st[2], env, len(targets))
                if len(values) == 1 and len(targets) > 1:
                    values = values * len(targets)          # one node drives every bit of a group
                if len(values) != len(targets):
                    raise Unsupported("assignment width mismatch")
                for t, v in zip(targets, values):
                    drive[t] = drive.get(t, 0) | v
            elif kind == "defaults":
                self._run(st[1], env, defaults, {})
            elif kind == "if":
                for cond, body in st[1]:
                    if self._value(cond, env)[0]:
                        self._run(body, env, drive, defaults)
                        break
                else:
                    self._run(st[2], env, drive, defaults)
            elif kind == "case":
                subject = self._value(st[1], env)
                for values, body in st[2]:
                    if values is None or any(self._value(v, env, len(subject)) == subject for v in values):
                        self._run(body, env, drive, defaults)
                        break
            elif kind == "table":
                ins = [self._value(e, env) for e in st[1]]
                outs = [self._bits(e, lvalue=True) for e in st[2]]
                for row_in, row_out in st[3]:
                    ok = True
                    for pattern, bits in zip(row_in, ins):
                        m = self._matches(pattern, bits)
                        if m is None:
                            m = self._value(pattern, env, len(bits)) == bits
                        ok = ok and m
                    if ok:
                        for expr, targets in zip(row_out, outs):
                            for t, v in zip(targets, self._value(expr, env, len(targets))):
                                drive[t] = drive.get(t, 0) | v
            elif kind == "assert":
                pass
            else:
                raise Unsupported("statement %s" % kind)

    # -- structural dependencies ------------------------------------------------------------
    def _refs(self, node):
        """Signals an expression reads: (per-bit list or None, set of all)."""
        kind = node[0] if node else None
        if kind == "group" and any(i is not None and i[0] not in ("name", "port", "group") for i in node[1]):
            found = set()
            for item in node[1]:
                if item is not None:
                    found |= self._refs(item)[1]
            return None, found
        if kind in ("name", "port", "group"):
            if kind == "name" and node[2] is None and (node[1] in self.constants or
                                                       (node[1] in self.states and node[1] not in self.groups)):
                return None, set()
            bits = self._bits(node)
            return [{b} for b in bits], set(bits)
        if kind == "un":
            return self._refs(node[2])
        if kind == "bin":
            la, sa = self._refs(node[2])
            lb, sb = self._refs(node[3])
            bitwise = node[1] in ("&", "#", "$", "!&", "!#", "!$", "AND", "OR", "XOR", "NAND", "NOR", "XNOR")
            if bitwise and la and lb and len(la) == len(lb):
                return [x | y for x, y in zip(la, lb)], sa | sb
            if bitwise and la and (lb is None and not sb or lb and len(lb) == 1):
                extra = lb[0] if lb else set()
                return [x | extra for x in la], sa | sb
            if bitwise and lb and (la is None and not sa or la and len(la) == 1):
                extra = la[0] if la else set()
                return [x | extra for x in lb], sa | sb
            return None, sa | sb
        if kind in ("num", "vcc", "gnd", "x", "pattern", "str"):
            return None, set()
        raise Unsupported("expression %r" % (kind,))

    def _collect(self, stmts, conds, deps):
        for st in stmts:
            kind = st[0]
            if kind == "eq" and st[2][0] == "call":
                targets = self._bits(st[1], lvalue=True)
                per_bit, everything = self._refs(st[2][2][0])
                enable = self._refs(st[2][2][1])[1]
                for i, t in enumerate(targets):
                    src = per_bit[i] if per_bit and len(per_bit) == len(targets) else everything
                    deps.setdefault(t + "~in", set()).update(src | conds)
                    deps.setdefault(t + "~oe", set()).update(enable | conds)
                    deps.setdefault(t, set()).update(src | enable | conds | {t + "@ext"})
            elif kind == "eq":
                targets = self._bits(st[1], lvalue=True)
                per_bit, everything = self._refs(st[2])
                for i, t in enumerate(targets):
                    src = per_bit[i] if per_bit and len(per_bit) == len(targets) else everything
                    deps.setdefault(t, set()).update(src | conds)
            elif kind == "defaults":
                self._collect(st[1], conds, deps)
            elif kind == "if":
                inner = set(conds)
                for cond, _ in st[1]:
                    inner |= self._refs(cond)[1]
                for _, body in st[1]:
                    self._collect(body, inner, deps)
                self._collect(st[2], inner, deps)
            elif kind == "case":
                inner = conds | self._refs(st[1])[1]
                for values, body in st[2]:
                    self._collect(body, inner, deps)
            elif kind == "table":
                inner = set(conds)
                for e in st[1]:
                    inner |= self._refs(e)[1]
                for row_in, _ in st[3]:
                    for e in row_in:
                        if e[0] not in ("x", "pattern", "num") and not self._is_dont_care(e):
                            inner |= self._refs(e)[1]
                for e in st[2]:
                    for t in self._bits(e, lvalue=True):
                        deps.setdefault(t, set()).update(inner)
            elif kind != "assert":
                raise Unsupported("statement %s" % kind)

    def dependencies(self):
        """{driven signal: set of primary signals (inputs and register outputs) it can depend on},
        from the structure of the statements; conservative."""
        direct = {}
        self._collect(self.statements, set(), direct)
        primary = set(self.inputs) | {bit + ".q" for bit in self.flops} | {b + "@ext" for b in self.bidir}
        resolved = {}

        def resolve(sig, seen):
            if sig in primary:
                return {sig}
            if sig in resolved:
                return resolved[sig]
            if sig in seen:
                return set()
            out = set()
            for d in direct.get(sig, ()):
                out |= resolve(d, seen | {sig})
            if not seen:
                resolved[sig] = out
            return out
        return {sig: resolve(sig, set()) for sig in direct}

    def _settle(self, env):
        """Recompute every combinational signal for the inputs and register outputs in env.
        Returns (driven values, names that were explicitly assigned)."""
        internal = self.outputs + self.nodes
        for b in internal:
            env.setdefault(b, 0)
        for _ in range(len(internal) + 2):               # iterate to a fixed point
            drive, defaults = {}, {}
            self._run(self.statements, env, drive, defaults)
            new = {b: drive.get(b, defaults.get(b, 0)) for b in internal}
            for b in internal:
                outside = env.get(b + "@ext", 0)
                if b in self.tri:                           # enabled: the buffer; else whatever is outside
                    new[b] = drive.get(b + "~in", 0) if drive.get(b + "~oe", 0) else outside
                elif b in self.bidir and b not in drive:    # a BIDIR pin nothing drives is an input
                    new[b] = outside
            if all(env[b] == v for b, v in new.items()):
                merged = dict(defaults)
                merged.update(drive)
                return merged, set(drive)
            env.update(new)
        raise Unsupported("combinational loop did not settle")

    def _input_env(self, inputs):
        env = {}
        for name, value in inputs.items():
            if name in self.groups:
                bits = self.groups[name]
                for i, b in enumerate(bits):
                    env[b + "@ext" if b in self.bidir else b] = (value >> (len(bits) - 1 - i)) & 1
            else:
                env[name + "@ext" if name in self.bidir else name] = int(bool(value))
        missing = [i for i in self.inputs if i not in env]
        if missing:
            raise ValueError("missing inputs: %s" % ", ".join(missing))
        return env

    def evaluate(self, **inputs):
        """Outputs {name: 0/1} of a purely combinational design (groups may be passed as
        integers)."""
        if self.flops:
            raise Unsupported("design has registers; use Simulator")
        env = self._input_env(inputs)
        self._settle(env)
        return {b: env[b] for b in self.outputs}


class Simulator:
    """Functional simulation of a design with registers and state machines."""

    def __init__(self, text: str, resolver=None):
        self.design = Design(text, resolver)
        d = self.design
        self.q = {bit: 0 for bit in d.flops}
        for name, (bits, _, reset) in d.machines.items():
            for i, bit in enumerate(bits):
                self.q[bit] = (reset >> (len(bits) - 1 - i)) & 1
        self.inputs = {name: 0 for name in d.inputs}
        self.clocks = {}
        self._settle()

    def _settle(self):
        d = self.design
        env = dict(self.inputs)
        env.update({bit + ".q": v for bit, v in self.q.items()})
        self.drive, self.assigned = d._settle(env)
        self.env = env

    def _control(self, bit, port, default):
        d = self.design
        for name, (bits, _, _) in d.machines.items():
            if bit in bits:
                key = "%s.%s" % (name, port)
                return self.drive.get(key, default) if key in self.assigned else default
        key = "%s.%s" % (bit, port)
        return self.drive.get(key, default) if key in self.assigned else default

    def _async(self):
        """Apply asynchronous clear/preset and machine reset; True if anything changed."""
        d, changed = self.design, False
        for bit, kind in d.flops.items():
            value = self.q[bit]
            if kind == "MACHINE":
                name = next(n for n, m in d.machines.items() if bit in m[0])
                bits, _, reset = d.machines[name]
                if self._control(bit, "reset", 0):
                    value = (reset >> (len(bits) - 1 - bits.index(bit))) & 1
            else:
                if not self._control(bit, "clrn", 1):
                    value = 0
                elif not self._control(bit, "prn", 1):
                    value = 1
            if value != self.q[bit]:
                self.q[bit], changed = value, True
        return changed

    def set(self, **inputs):
        """Change inputs. Registers clocked by a 0 -> 1 change take their pre-edge input."""
        d = self.design
        before = {bit: self._control(bit, "clk", 0) for bit in d.flops}
        captured = {}
        for bit, kind in d.flops.items():
            if kind == "MACHINE":
                key = bit + ".next"
                name = next(n for n, m in d.machines.items() if bit in m[0])
                nexts = {b + ".next" for b in d.machines[name][0]}
                captured[bit] = self.drive.get(key, 0) if nexts & self.assigned else self.q[bit]
            elif kind.startswith("T"):
                captured[bit] = self.q[bit] ^ self.drive.get(bit + ".t", 0)
            else:
                captured[bit] = self.drive.get(bit + ".d", 0)
            if not self._control(bit, "ena", 1):
                captured[bit] = self.q[bit]
        for name, value in inputs.items():
            if name in d.groups:
                bits = d.groups[name]
                for i, b in enumerate(bits):
                    self.inputs[b + "@ext" if b in d.bidir else b] = (value >> (len(bits) - 1 - i)) & 1
            elif name in d.bidir:
                self.inputs[name + "@ext"] = int(bool(value))      # what the outside drives onto the pin
            elif name in self.inputs:
                self.inputs[name] = int(bool(value))
            else:
                raise ValueError("no input named %s" % name)
        self._settle()
        edge = [bit for bit in d.flops if not before[bit] and self._control(bit, "clk", 0)]
        for bit in edge:
            self.q[bit] = captured[bit]
        for _ in range(4):
            self._settle()
            if not self._async():
                break
        return self.outputs()

    def clock(self, name="clk", cycles=1):
        for _ in range(cycles):
            self.set(**{name: 1})
            self.set(**{name: 0})
        return self.outputs()

    def outputs(self):
        return {b: self.env.get(b, 0) for b in self.design.outputs}

    def state(self, machine):
        bits, values, _ = self.design.machines[machine]
        code = 0
        for bit in bits:
            code = code << 1 | self.q[bit]
        return next((s for s, v in values.items() if v == code), code)


def directory_resolver(*directories):
    """A resolver over one or more directories, searched in order: `name` finds <name>.tdf, or
    else <name>.gdf converted to AHDL, and `name.inc` finds that include file (names are
    case-insensitive)."""
    import pathlib
    files = {}
    for directory in directories:
        for pattern in ("*.tdf", "*.inc", "*.gdf"):
            for p in pathlib.Path(directory).glob(pattern):
                key = p.name.lower() if p.suffix.lower() == ".inc" else p.stem.lower()
                files.setdefault(key, p)              # an AHDL file wins over a schematic

    def resolve(name):
        path = files.get(name.lower())
        if path is None:
            return None
        if path.suffix.lower() == ".gdf":             # schematics are converted to AHDL
            from . import gdf
            return gdf.to_ahdl(path.read_bytes(), path.stem)
        return path.read_text(encoding="latin1")
    return resolve


def vendor_library(vendor_root):
    """Directories of a MAX+PLUS II installation that hold AHDL library designs and their
    prototype include files (the user's own copy; nothing is bundled here)."""
    import pathlib
    root = pathlib.Path(vendor_root)
    return [d for d in (root / "max2lib" / "mf", root / "max2lib" / "prim", root / "max2inc") if d.exists()]
