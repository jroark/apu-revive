"""Reader for MAX+PLUS II schematic files (.gdf).

Record layouts from the vendor's reader (gio.dll, gio_read_drawing at 0x100167e0 and the
routines it calls). The file is `GDF\\0\\0\\0` + u16 version, then one-byte tags:
    e  four bytes (source type, id, ...)                  -- file header record
    f  starts a graphic: `j` w h, then any of
         k / l / m   line: four i16 (x1 y1 x2 y2) and a u16 (style / width / dots)
         i           marks that the line just read carries a name (a `q` text follows)
         n           arc: six i16 and four u16
         o           circle: two i16 and two u16
         q           text (below);  p  pin stub: two i16
    g  starts the symbol section: for each symbol its graphic (`f` ...), then per instance
         r  i16 x, i16 y, u8 orientation;  h  its texts;  u  more texts (each with a trailing u16)
    h  top-level texts;  t  end of file
A text record is: tag, u8 type, u8 font, i16 x, i16 y, u16 attributes; then, if font >= 4, a
font string and two i16 (size); else, inside a graphic or `u` list, two i16 when type is 0x32 or
0x37; then the text and an alternative text. Strings are optional records: `v` + u16 length +
bytes + NUL, or `s` + u8 length + bytes + NUL.

This layer only recovers the records. Building a netlist from them (which line touches which
pin stub, which names label which lines) is not done here.
"""
import re
import struct


class GdfError(Exception):
    pass


class _Reader:
    def __init__(self, data):
        self.d, self.p = data, 8

    def peek(self):
        return self.d[self.p] if self.p < len(self.d) else None

    def u8(self):
        self.p += 1
        return self.d[self.p - 1]

    def i16(self):
        self.p += 2
        return struct.unpack_from("<h", self.d, self.p - 2)[0]

    def u16(self):
        self.p += 2
        return struct.unpack_from("<H", self.d, self.p - 2)[0]

    def string(self):
        tag = self.peek()
        if tag == 0x76:                                   # 'v'
            self.p += 1
            n = self.u16()
        elif tag == 0x73:                                 # 's'
            self.p += 1
            n = self.u8()
        else:
            return None
        raw = self.d[self.p:self.p + n]                   # the length excludes the terminating NUL
        self.p += n + 1
        return raw.decode("latin1")


def parse(data: bytes):
    """Return (version, header bytes, [records]). Records are tuples whose first item is the
    kind: ("graphic", w, h), ("line", tag, x1, y1, x2, y2, style), ("named",), ("arc", ...),
    ("circle", ...), ("pin", x, y), ("text", context, type, font, x, y, attrs, font_name, size,
    text, alt, extra), ("symbols",), ("instance", x, y, orientation), ("texts", tag)."""
    if data[:4] != b"GDF\0":
        raise GdfError("not a GDF file")
    version = struct.unpack_from("<H", data, 6)[0]
    r = _Reader(data)
    if r.peek() == 0x00:                                  # one padding byte follows the header
        r.p += 1
    if r.u8() != 0x65:
        raise GdfError("no header record")
    header = bytes(r.u8() for _ in range(4))
    records, context = [], 7
    while True:
        tag = r.peek()
        if tag is None:
            raise GdfError("file ends without a terminator")
        c = chr(tag)
        if c == "t":
            r.p += 1
            break
        if c == "f":
            r.p += 1
            context = 7
            if r.peek() != 0x6A:
                raise GdfError("graphic without a size at %#x" % r.p)
            r.p += 1
            records.append(("graphic", r.i16(), r.i16()))
        elif c in "klm":
            r.p += 1
            records.append(("line", c, r.i16(), r.i16(), r.i16(), r.i16(), r.u16()))
        elif c == "i":
            r.p += 1
            records.append(("named",))
            if r.peek() == 0x71:
                records.append(_text(r, 11))
        elif c == "n":
            r.p += 1
            records.append(("arc",) + tuple(r.i16() for _ in range(6)) + tuple(r.u16() for _ in range(4)))
        elif c == "o":
            r.p += 1
            records.append(("circle", r.i16(), r.i16(), r.u16(), r.u16()))
        elif c == "p":
            r.p += 1
            records.append(("pin", r.i16(), r.i16()))
        elif c == "q":
            records.append(_text(r, context))
        elif c == "g":
            r.p += 1
            records.append(("symbols",))
        elif c == "r":
            r.p += 1
            records.append(("instance", r.i16(), r.i16(), r.u8()))
        elif c in "hu":
            r.p += 1
            context = 8 if c == "h" else 10
            records.append(("texts", c))
        else:
            raise GdfError("unknown record tag %r at %#x" % (c, r.p))
    return version, header, records, len(data) - r.p


def _text(r, context):
    r.p += 1
    kind, font = r.u8(), r.u8()
    x, y, attrs = r.i16(), r.i16(), r.u16()
    font_name, size = None, None
    if font >= 4:
        font_name = r.string()
        size = (r.i16(), r.i16())
    elif context in (7, 10) and kind in (0x32, 0x37):
        size = (r.i16(), r.i16())
    text = r.string()
    alt = r.string()
    extra = r.u16() if context == 10 else None
    return ("text", context, kind, font, x, y, attrs, font_name, size, text, alt, extra)


# Text kinds seen in the files (the reader calls this the text's "attr type"):
KIND_SYMBOL_NAME, KIND_INPUT_PIN, KIND_OUTPUT_PIN, KIND_NODE_NAME, KIND_INSTANCE_ID = 1, 2, 3, 6, 41
KIND_USER_SYMBOL_NAME = 19            # name of a symbol made from the user's own design


def symbols(records):
    """[{name, size, pins: [(pin name, kind, x, y)], instances: [{x, y, orientation, id}]}] from
    a parsed file's symbol section. A pin is the text record just before a pin stub."""
    out, current, in_symbols, last_text = [], None, False, None
    for rec in records:
        kind = rec[0]
        if kind == "symbols":
            in_symbols = True
            current = None
        elif not in_symbols:
            continue
        elif kind == "graphic":
            current = {"name": None, "size": (rec[1], rec[2]), "pins": [], "instances": []}
            out.append(current)
        elif current is None:
            continue
        elif kind == "text" and rec[1] == 7:
            last_text = rec
            if rec[2] in (KIND_SYMBOL_NAME, KIND_USER_SYMBOL_NAME):
                current["name"] = rec[9]
        elif kind == "pin" and last_text is not None:
            current["pins"].append((last_text[9], last_text[2], rec[1], rec[2]))
        elif kind == "instance":
            current["instances"].append({"x": rec[1], "y": rec[2], "orientation": rec[3], "id": None})
        elif kind == "text" and rec[1] == 8 and rec[2] == KIND_INSTANCE_ID and current["instances"]:
            current["instances"][-1]["id"] = rec[9]
    return out


def wires(records):
    """[(x1, y1, x2, y2, name or None)] for the top-level lines; a name is the node-name text
    attached to that line."""
    out = []
    for i, rec in enumerate(records):
        if rec[0] == "symbols":
            break
        if rec[0] == "line":
            name = None
            if i + 2 < len(records) and records[i + 1][0] == "named" and records[i + 2][0] == "text":
                name = records[i + 2][9]
            out.append((rec[2], rec[3], rec[4], rec[5], name))
    return out


# --- netlist ------------------------------------------------------------------------------------
KIND_PORT_NAME, KIND_DEFAULT = 5, 7
GATES = {"AND": ("&", False, False), "OR": ("#", False, False), "NAND": ("&", False, True), "NOR": ("#", False, True),
         "BAND": ("&", True, False), "BOR": ("#", True, False), "BNAND": ("&", True, True), "BNOR": ("#", True, True)}
BUFFERS = ("SOFT", "LCELL", "MCELL", "GLOBAL", "CARRY", "CASCADE", "WIRE")
# Primitive pins are numbered: 1 is the output, then the inputs in the order of the primitive's
# AHDL prototype (checked against the symbols' pin positions for DFF, DFFE, JKFF, LATCH, TRI).
REGISTERS = {"DFF": ("d", "clk", "clrn", "prn"), "DFFE": ("d", "clk", "clrn", "prn", "ena"),
             "TFF": ("t", "clk", "clrn", "prn"), "TFFE": ("t", "clk", "clrn", "prn", "ena")}


def _instances(records):
    """Symbol placements with their texts: [{name, x, y, orientation, pins, texts: {kind: text}}]."""
    out, current, in_symbols, last_text, placed = [], None, False, None, None
    for rec in records:
        kind = rec[0]
        if kind == "symbols":
            in_symbols, current, placed = True, None, None
        elif not in_symbols:
            continue
        elif kind == "graphic":
            current, placed = {"name": None, "pins": []}, None
        elif current is None:
            continue
        elif kind == "text" and rec[1] == 7:
            last_text = rec
            if rec[2] in (KIND_SYMBOL_NAME, KIND_USER_SYMBOL_NAME):
                current["name"] = rec[9]
        elif kind == "pin" and last_text is not None and placed is None:
            current["pins"].append((last_text[9], last_text[2], rec[1], rec[2]))
        elif kind == "instance":
            placed = {"name": current["name"], "x": rec[1], "y": rec[2], "orientation": rec[3],
                      "pins": current["pins"], "texts": {}}
            out.append(placed)
        elif kind == "text" and rec[1] in (8, 10) and placed is not None:
            placed["texts"].setdefault(rec[2], rec[9])
    return out


def _on_segment(p, a, b):
    (px, py), (ax, ay), (bx, by) = p, a, b
    if (bx - ax) * (py - ay) != (by - ay) * (px - ax):
        return False
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def to_ahdl(data: bytes, name: str) -> str:
    """Convert a schematic to AHDL text for the same logic.

    Checked on the library's 4count: the converted design counts up and down, gives carry-out at
    15 and 0, loads, holds with carry-in low and clears asynchronously.

    Wires that share an endpoint, or where one ends on another, are one net; a pin stub that lies
    on a wire joins its net; wires with the same node name are one net. Supported symbols: INPUT
    and OUTPUT pins (with VCC / GND defaults), VCC, GND, NOT, EXP, the AND / OR / NAND / NOR
    families and their bubble-input (B-prefixed) forms, XOR, XNOR, the buffer primitives, TRI,
    D and T flip-flops; any other symbol becomes an instance of the design of that name.
    Buses are carried as AHDL groups (a bus pin, port or wire name such as "dir[1..0]").
    Not supported (GdfError): rotated or mirrored symbols, bus lists ("a, b"), BIDIR pins,
    latches, JK and SR flip-flops.
    """
    _, _, records, _ = parse(data)
    segments = wires(records)
    placed = [p for p in _instances(records) if p["name"] not in (None, "TITLE")]
    parent = {}

    def find(p):
        parent.setdefault(p, p)
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def union(a, b):
        parent[find(a)] = find(b)

    points = set()
    for x1, y1, x2, y2, label in segments:
        if label and "," in label:
            raise GdfError("bus list %r is not supported" % label)
        union((x1, y1), (x2, y2))
        points.update(((x1, y1), (x2, y2)))
    pins = []
    for inst in placed:
        if inst["orientation"] != 0:
            raise GdfError("rotated symbol %s is not supported" % inst["name"])
        for pin_name, kind, px, py in inst["pins"]:
            point = (inst["x"] + px, inst["y"] + py)
            pins.append((inst, pin_name, kind, point))
            points.add(point)
    for p in points:
        for x1, y1, x2, y2, _ in segments:
            if _on_segment(p, (x1, y1), (x2, y2)):
                union(p, (x1, y1))
    labels = {}
    for x1, y1, x2, y2, label in segments:
        if label:
            key = label.lower()
            if key in labels:
                union((x1, y1), labels[key])
            labels[key] = (x1, y1)

    net_of_pin = {(id(inst), pin_name): find(point) for inst, pin_name, _, point in pins}
    signal = {}                                            # net root -> AHDL signal name
    inputs, outputs, nodes, variables, equations = [], [], [], [], []
    for label, point in labels.items():
        signal[find(point)] = label

    def ident(text):
        return text.lower()

    for inst in placed:
        if inst["name"] == "INPUT":
            port = ident(inst["texts"].get(KIND_PORT_NAME) or "")
            if not port:
                raise GdfError("INPUT symbol without a name")
            default = inst["texts"].get(KIND_DEFAULT)
            inputs.append((port, default if default in ("VCC", "GND") else None))
            signal[net_of_pin[(id(inst), "1")]] = port
    counter = [0]

    def net(inst, pin_name, driven=False):
        root = net_of_pin[(id(inst), pin_name)]
        if root not in signal:
            counter[0] += 1
            bus = re.search(r"\[[^\]]+\]$", pin_name)          # a bus pin makes a bus node
            signal[root] = "n%d%s" % (counter[0], bus.group(0) if bus else "")
            nodes.append(signal[root])
        return signal[root]

    def connected(inst, pin_name):
        point = next(pt for i, n, _, pt in pins if i is inst and n == pin_name)
        return any(_on_segment(point, (x1, y1), (x2, y2)) for x1, y1, x2, y2, _ in segments) or \
            sum(1 for _, _, _, pt in pins if pt == point) > 1

    for inst in placed:
        kind, uid = inst["name"], inst["texts"].get(KIND_INSTANCE_ID, str(id(inst)))
        # the file lists a symbol's pins in drawing order, not by number
        order = lambda p: (0, int(p[0])) if p[0].isdigit() else (1, 0)
        ins = [p[0] for p in sorted(inst["pins"], key=order) if p[1] == KIND_INPUT_PIN]
        outs = [p[0] for p in sorted(inst["pins"], key=order) if p[1] == KIND_OUTPUT_PIN]
        base = kind.rstrip("0123456789")
        if kind == "INPUT":
            continue
        if kind == "OUTPUT":
            port = ident(inst["texts"].get(KIND_PORT_NAME) or "")
            outputs.append(port)
            source = net(inst, ins[0])
            if source != port:                         # a wire may carry the port's own name
                equations.append("%s = %s;" % (port, source))
        elif kind in ("VCC", "GND"):
            equations.append("%s = %s;" % (net(inst, outs[0]), kind))
        elif kind in ("BIDIR", "LATCH", "JKFF", "JKFFE", "SRFF", "SRFFE"):
            raise GdfError("%s symbols are not supported" % kind)
        elif kind == "NOT" or kind == "EXP":
            equations.append("%s = !%s;" % (net(inst, outs[0]), net(inst, ins[0])))
        elif kind in BUFFERS:
            equations.append("%s = %s;" % (net(inst, outs[0]), net(inst, ins[0])))
        elif kind in ("XOR", "XNOR"):
            body = " $ ".join(net(inst, p) for p in ins)
            equations.append("%s = %s;" % (net(inst, outs[0]), body if kind == "XOR" else "!(%s)" % body))
        elif base in GATES and kind[len(base):].isdigit():
            op, bubbles, invert = GATES[base]
            body = (" %s " % op).join(("!" if bubbles else "") + net(inst, p) for p in ins)
            equations.append("%s = %s;" % (net(inst, outs[0]), "!(%s)" % body if invert else body))
        elif kind == "TRI":
            equations.append("%s = TRI(%s, %s);" % (net(inst, outs[0]), net(inst, ins[0]), net(inst, ins[1])))
        elif kind in REGISTERS:
            reg = "ff%s" % uid
            variables.append("%s : %s;" % (reg, kind))
            for pin_name, port in zip(ins, REGISTERS[kind]):
                if connected(inst, pin_name):
                    equations.append("%s.%s = %s;" % (reg, port, net(inst, pin_name)))
            equations.append("%s = %s.q;" % (net(inst, outs[0]), reg))
        else:                                              # an instance of another design
            sub = "u%s" % uid
            variables.append("%s : %s;" % (sub, ident(kind)))
            for pin_name in ins:
                if connected(inst, pin_name):
                    equations.append("%s.%s = %s;" % (sub, ident(pin_name), net(inst, pin_name)))
            for pin_name in outs:
                if connected(inst, pin_name):
                    equations.append("%s = %s.%s;" % (net(inst, pin_name), sub, ident(pin_name)))
    port_names = {p for p, _ in inputs} | set(outputs)
    nodes = [n for n in dict.fromkeys(nodes) if n not in port_names]
    nodes += [s for s in dict.fromkeys(signal.values()) if s not in port_names and s not in nodes]
    lines = ["SUBDESIGN %s" % ident(name), "("]
    lines += ["    %s : INPUT%s;" % (p, " = %s" % d if d else "") for p, d in inputs]
    lines += ["    %s : OUTPUT;" % p for p in outputs]
    lines += [")"]
    if nodes or variables:
        lines.append("VARIABLE")
        lines += ["    %s : NODE;" % n for n in nodes]
        lines += ["    " + v for v in variables]
    lines += ["BEGIN"] + ["    " + e for e in equations] + ["END;"]
    return "\n".join(lines) + "\n"
