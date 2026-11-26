"""AHDL (.tdf) front end: lexer and parser for Altera's hardware description language.

Written from the language as used in the MAX+PLUS II tutorial designs; no Altera code. This is
the start of native design entry: it builds a syntax tree. Elaboration into logic is separate
(see apu/ahdl_eval.py) and covers far less.

Tree nodes are tuples whose first element names the kind:
    ("design", titles, constants, includes, prototypes, subdesign)
    ("subdesign", name, ports, variables, statements)
    port: (names, direction, default)        name entries are ("name", id, range or None)
    variable: (names, type)                   type: ("prim", NAME) | ("machine", bits, states) | ...
    statements: ("eq", lhs, expr) | ("if", [(cond, stmts), ...], else_stmts) |
                ("case", expr, [(values, stmts), ...]) | ("table", inputs, outputs, rows) |
                ("defaults", stmts) | ("assert", ...) | ("for", var, lo, hi, stmts)
    expressions: ("num", value, width or None) | ("name", id, range) | ("port", id, port) |
                 ("un", op, e) | ("bin", op, a, b) | ("call", name, args) | ("group", [e, ...]) |
                 ("vcc",) | ("gnd",) | ("x",)
"""
import re

KEYWORDS = {"SUBDESIGN", "BEGIN", "END", "IF", "THEN", "ELSIF", "ELSE", "CASE", "IS", "WHEN", "OTHERS",
            "TABLE", "VARIABLE", "CONSTANT", "INCLUDE", "TITLE", "FUNCTION", "RETURNS", "DEFAULTS",
            "INPUT", "OUTPUT", "BIDIR", "MACHINE", "OF", "BITS", "WITH", "STATES", "NODE", "TRI_STATE_NODE",
            "AND", "OR", "NOT", "NAND", "NOR", "XOR", "XNOR", "VCC", "GND", "PARAMETERS", "ASSERT",
            "REPORT", "SEVERITY", "FOR", "IN", "TO", "GENERATE", "OPTIONS", "DEFINE", "DESIGN", "MOD",
            "DIV", "LOG2", "CEIL", "FLOOR", "USED"}
TOKEN = re.compile(r'''
    (?P<ws>\s+) | (?P<c1>%[^%]*%) | (?P<c2>--[^\n]*) |
    (?P<based>[BbOoHhXx]"[0-9A-Fa-fXx_ ]*") | (?P<str>"[^"]*") |
    (?P<id>\d*[A-Za-z_/][A-Za-z0-9_/~]*|'[^']*') | (?P<num>\d+) |
    (?P<op>=>|==|!=|<=|>=|\.\.|!&|!\#|!\$|[-+*=<>!&\#$()\[\],;:.?^])
''', re.X)


class AhdlError(Exception):
    pass


def tokenize(text):
    out, pos, line = [], 0, 1
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise AhdlError("line %d: unexpected character %r" % (line, text[pos]))
        kind, value = m.lastgroup, m.group()
        line += value.count("\n")
        pos = m.end()
        if kind in ("ws", "c1", "c2"):
            continue
        if kind == "id" and value.upper() in KEYWORDS:
            kind, value = "kw", value.upper()
        elif kind == "id":
            value = value.lower()                           # AHDL names are not case-sensitive
        out.append((kind, value, line))
    out.append(("eof", "", line))
    return out


BASES = {"B": 2, "O": 8, "H": 16, "X": 16}
BINARY = [("#", "!#", "OR", "NOR"), ("$", "!$", "XOR", "XNOR"), ("&", "!&", "AND", "NAND"),
          ("==", "!=", "<", "<=", ">", ">="), ("+", "-"), ("*", "DIV", "MOD")]


class Parser:
    def __init__(self, text):
        self.t, self.i = tokenize(text), 0

    # -- token helpers ------------------------------------------------------------------------
    def peek(self, k=0):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def at(self, *values):
        return self.peek()[1] in values and self.peek()[0] in ("kw", "op")

    def take(self, value=None, kind=None):
        tok = self.peek()
        if (value is not None and tok[1] != value) or (kind is not None and tok[0] != kind):
            raise AhdlError("line %d: expected %s, found %r" % (tok[2], value or kind, tok[1]))
        self.i += 1
        return tok

    def accept(self, value):
        if self.at(value):
            self.i += 1
            return True
        return False

    def ident(self):
        tok = self.peek()
        if tok[0] not in ("id", "kw", "num"):            # macrofunction names may be all digits
            raise AhdlError("line %d: expected a name, found %r" % (tok[2], tok[1]))
        self.i += 1
        return tok[1].strip("'")

    # -- top level ----------------------------------------------------------------------------
    def design(self):
        titles, constants, includes, protos, sub = [], [], [], [], None
        while self.peek()[0] != "eof":
            if self.accept("TITLE"):
                titles.append(self.take(kind="str")[1].strip('"')); self.take(";")
            elif self.accept("INCLUDE"):
                includes.append(self.take(kind="str")[1].strip('"')); self.take(";")
            elif self.accept("CONSTANT"):
                name = self.ident(); self.take("="); constants.append((name, self.expr())); self.take(";")
            elif self.accept("FUNCTION"):
                protos.append(self.prototype())
            elif self.accept("PARAMETERS") or self.accept("OPTIONS") or self.accept("DEFINE"):
                self.skip_to(";")
            elif self.accept("ASSERT"):
                self.skip_to(";")
            elif self.at("SUBDESIGN"):
                sub = self.subdesign()
            elif self.accept(";"):
                pass
            else:
                tok = self.peek()
                raise AhdlError("line %d: unexpected %r at top level" % (tok[2], tok[1]))
        if sub is None:
            raise AhdlError("no SUBDESIGN section")
        return ("design", titles, constants, includes, protos, sub)

    def skip_to(self, value):
        depth = 0
        while self.peek()[0] != "eof":
            v = self.peek()[1]
            self.i += 1
            if v == "(":
                depth += 1
            elif v == ")":
                depth -= 1
            elif v == value and depth <= 0:
                return

    def prototype(self):
        name = self.ident()
        self.take("(")
        ins = self.name_list(")")
        self.take(")")
        outs = []
        if self.accept("WITH"):
            self.take("("); self.skip_to(")")
        if self.accept("RETURNS"):
            self.take("("); outs = self.name_list(")"); self.take(")")
        self.take(";")
        return (name, ins, outs)

    def name_list(self, closer):
        names = []
        while not self.at(closer):
            if self.accept(","):
                continue
            names.append(self.name_entry())
        return names

    def name_entry(self):
        name = self.ident()
        rng = None
        while self.at("["):
            self.take("[")
            rng = self.range_body() if not self.at("]") else ()
            self.take("]")
        return ("name", name, rng)

    def range_body(self):
        lo = self.expr()
        if self.accept(".."):
            return (lo, self.expr())
        return (lo,)

    def subdesign(self):
        self.take("SUBDESIGN")
        name = self.ident()
        self.take("(")
        ports = []
        while not self.at(")"):
            names = self.name_list(":")
            self.take(":")
            direction = self.ident().upper()
            if direction == "MACHINE":
                direction += " " + self.ident().upper()
            default = None
            if self.accept("="):
                default = self.expr()
            ports.append((names, direction, default))
            self.accept(";")
        self.take(")")
        variables = []
        if self.accept("VARIABLE"):
            variables = self.variable_items(("BEGIN",))
        self.take("BEGIN")
        stmts = self.statements(("END",))
        self.take("END")
        self.accept(";")
        return ("subdesign", name, ports, variables, stmts)

    def variable_items(self, stop):
        items = []
        while not self.at(*stop) and self.peek()[0] != "eof":
            if self.at("IF"):                              # IF cond GENERATE ... [ELSE GENERATE ...] END GENERATE;
                self.take("IF")
                cond = self.expr()
                self.take("GENERATE")
                then = self.variable_items(("ELSE", "END"))
                other = []
                if self.accept("ELSE"):
                    self.take("GENERATE")
                    other = self.variable_items(("END",))
                self.take("END"); self.take("GENERATE"); self.take(";")
                items.append((None, ("ifgen", cond, then, other)))
                continue
            names = self.name_list(":")
            self.take(":")
            items.append((names, self.var_type()))
            self.take(";")
        return items

    def var_type(self):
        if self.accept("MACHINE"):
            bits, states = [], []
            if self.accept("OF"):
                self.take("BITS"); self.take("("); bits = self.name_list(")"); self.take(")")
            if not self.at("WITH"):
                return ("machine", bits, None)              # machine alias declaration
            self.take("WITH"); self.take("STATES"); self.take("(")
            while not self.at(")"):
                state = self.ident()
                value = None
                if self.accept("="):
                    value = self.expr()
                states.append((state, value))
                self.accept(",")
            self.take(")")
            return ("machine", bits, states)
        name = self.ident()
        if self.at("WITH"):
            self.take("WITH"); self.take("("); self.skip_to(")")
        return ("prim", name.upper()) if name.upper() in PRIMITIVES else ("instance", name)

    # -- statements ---------------------------------------------------------------------------
    def statements(self, stop):
        out = []
        while not self.at(*stop) and self.peek()[0] != "eof":
            out.append(self.statement())
        return out

    def statement(self):
        if self.accept("IF"):
            branches = []
            cond = self.expr()
            if self.accept("GENERATE"):                    # conditional generate, resolved at elaboration
                then = self.statements(("ELSE", "END"))
                other = []
                if self.accept("ELSE"):
                    self.take("GENERATE")
                    other = self.statements(("END",))
                self.take("END"); self.take("GENERATE"); self.take(";")
                return ("ifgen", cond, then, other)
            self.take("THEN")
            branches.append((cond, self.statements(("ELSIF", "ELSE", "END"))))
            other = []
            while True:
                if self.accept("ELSIF"):
                    cond = self.expr(); self.take("THEN")
                    branches.append((cond, self.statements(("ELSIF", "ELSE", "END"))))
                elif self.accept("ELSE"):
                    other = self.statements(("END",))
                else:
                    break
            self.take("END"); self.take("IF"); self.take(";")
            return ("if", branches, other)
        if self.accept("CASE"):
            subject = self.expr(); self.take("IS")
            arms = []
            while self.accept("WHEN"):
                if self.accept("OTHERS"):
                    values = None
                else:
                    values = [self.expr()]
                    while self.accept(","):
                        values.append(self.expr())
                self.take("=>")
                arms.append((values, self.statements(("WHEN", "END"))))
            self.take("END"); self.take("CASE"); self.take(";")
            return ("case", subject, arms)
        if self.accept("TABLE"):
            ins = self.expr_list("=>"); self.take("=>"); outs = self.expr_list(";"); self.take(";")
            rows = []
            while not self.at("END"):
                a = self.expr_list("=>"); self.take("=>"); b = self.expr_list(";"); self.take(";")
                rows.append((a, b))
            self.take("END"); self.take("TABLE"); self.take(";")
            return ("table", ins, outs, rows)
        if self.accept("DEFAULTS"):
            body = self.statements(("END",))
            self.take("END"); self.take("DEFAULTS"); self.take(";")
            return ("defaults", body)
        if self.accept("ASSERT"):
            self.skip_to(";")
            return ("assert",)
        if self.accept("FOR"):
            var = self.ident(); self.take("IN"); lo = self.expr(); self.take("TO"); hi = self.expr()
            self.take("GENERATE")
            body = self.statements(("END",))
            self.take("END"); self.take("GENERATE"); self.take(";")
            return ("for", var, lo, hi, body)
        lhs = self.expr()
        self.take("=")
        rhs = self.expr()
        self.take(";")
        return ("eq", lhs, rhs)

    def expr_list(self, closer):
        items = [self.expr()]
        while self.accept(","):
            items.append(self.expr())
        return items

    # -- expressions --------------------------------------------------------------------------
    def expr(self, level=0):
        if level == len(BINARY):
            return self.unary()
        left = self.expr(level + 1)
        while self.peek()[1] in BINARY[level] and self.peek()[0] in ("op", "kw"):
            op = self.take()[1]
            left = ("bin", op, left, self.expr(level + 1))
        return left

    def unary(self):
        if self.at("!", "NOT"):
            self.take()
            return ("un", "!", self.unary())
        if self.at("-"):
            self.take()
            return ("un", "-", self.unary())
        return self.primary()

    def primary(self):
        kind, value, line = self.peek()
        if kind == "num" and self.peek(1)[1] != "(":
            self.i += 1
            return ("num", int(value), None)
        if kind == "based":
            self.i += 1
            digits = value[2:-1].replace("_", "").replace(" ", "")
            base = BASES[value[0].upper()]
            width = len(digits) * {2: 1, 8: 3, 16: 4}[base]
            if "X" in digits.upper() and base == 2:
                return ("pattern", digits.upper())
            return ("num", int(digits, base), width)
        if kind == "str":
            self.i += 1
            return ("str", value.strip('"'))
        if self.accept("VCC"):
            return ("vcc",)
        if self.accept("GND"):
            return ("gnd",)
        if self.accept("("):
            items, expect = [], True
            while not self.at(")"):
                if self.accept(","):
                    if expect:
                        items.append(None)                  # an empty position
                    expect = True
                    continue
                items.append(self.expr())
                expect = False
            self.take(")")
            if expect and items:
                items.append(None)
            return items[0] if len(items) == 1 else ("group", items)
        if kind in ("id", "kw", "num"):
            entry = self.name_entry()
            if self.at("("):
                self.take("(")
                args, expect = [], True
                while not self.at(")"):
                    if self.accept(","):
                        if expect:
                            args.append(None)
                        expect = True
                        continue
                    args.append(self.expr()); expect = False
                if expect and args:
                    args.append(None)
                self.take(")")
                node = ("call", entry[1], args)
                if self.accept("RETURNS"):
                    self.take("("); self.skip_to(")")
                return node
            if self.at("."):
                self.take(".")
                if self.at("("):
                    self.take("(")
                    ports = self.name_list(")")
                    self.take(")")
                    return ("group", [("port", entry, p) for p in ports])
                port = self.name_entry()
                return ("port", entry, port)
            return entry
        raise AhdlError("line %d: unexpected %r in expression" % (line, value))


PRIMITIVES = {"DFF", "DFFE", "TFF", "TFFE", "JKFF", "JKFFE", "SRFF", "SRFFE", "LATCH", "NODE", "TRI_STATE_NODE",
              "LCELL", "MCELL", "SOFT", "GLOBAL", "TRI", "EXP", "CARRY", "CASCADE", "OPNDRN", "WIRE"}


def parse(text: str):
    return Parser(text).design()


def parse_prototypes(text: str):
    """[(name, inputs, outputs)] from an include file of FUNCTION prototypes; anything else in
    the file (CONSTANT, PARAMETERS ...) is skipped."""
    p = Parser(text)
    out = []
    while p.peek()[0] != "eof":
        if p.accept("FUNCTION"):
            out.append(p.prototype())
        else:
            p.i += 1
    return out
