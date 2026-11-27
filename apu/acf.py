"""MAX+PLUS II assignment and configuration files (.acf), and the compiler's fit files (.fit),
which use the same syntax for their CHIP blocks.

A file is a list of sections, each `HEADER words BEGIN entries END;`. An entry is either
`KEY = VALUE;` or `"node" : KIND = VALUE;` (pin and location assignments in CHIP blocks, e.g.
`"accel" : INPUT_PIN = 4;` or `"|auto_max:1|q0" : LOCATION = LC19;`). `--` starts a comment.
Values are kept as text.
"""
import re

_TOKEN = re.compile(r'\s+|--[^\n]*|"(?:[^"]*)"|[:=;]|[^\s:=;"]+')


class Section:
    def __init__(self, header, entries):
        self.header = header                     # e.g. ["CHIP", "chiptrip"] or ["DEFAULT_DEVICES"]
        self.entries = entries                   # [(name, kind or None, value)]

    @property
    def kind(self):
        return self.header[0].upper()

    def get(self, key, default=None):
        for name, kind, value in self.entries:
            if kind is None and name.upper() == key.upper():
                return value
        return default

    def all(self, key):
        return [v for n, k, v in self.entries if k is None and n.upper() == key.upper()]


class AcfError(Exception):
    pass


def _tokens(text):
    for m in _TOKEN.finditer(text):
        t = m.group(0)
        if t.isspace() or t.startswith("--"):
            continue
        yield t


def parse(text: str):
    toks = list(_tokens(text))
    sections, i = [], 0
    while i < len(toks):
        header = []
        while i < len(toks) and toks[i].upper() != "BEGIN":
            header.append(toks[i])
            i += 1
        if i >= len(toks):
            if header:
                raise AcfError("section %s has no BEGIN" % " ".join(header))
            break
        i += 1
        entries = []
        while i < len(toks) and toks[i].upper() != "END":
            j = toks.index(";", i) if ";" in toks[i:] else None
            if j is None:
                raise AcfError("entry without ';' in %s" % " ".join(header))
            words = toks[i:j]
            i = j + 1
            if not words:
                continue
            if "=" in words:
                eq = words.index("=")
                left, value = words[:eq], " ".join(words[eq + 1:])
            elif ":" in words:                         # `"oe~2" : INPUT_PIN;` (a fit file's unplaced pin)
                left, value = words, ""
            else:
                raise AcfError("entry without '=': %s" % " ".join(words))
            if ":" in left:
                c = left.index(":")
                name, kind = " ".join(left[:c]), " ".join(left[c + 1:])
            else:
                name, kind = " ".join(left), None
            entries.append((name.strip('"'), kind, value.strip('"')))
        i += 1                                     # END
        if i < len(toks) and toks[i] == ";":
            i += 1
        sections.append(Section(header, entries))
    return sections


def chip(sections, name=None):
    """The CHIP section for `name` (any CHIP section if None), or None."""
    for s in sections:
        if s.kind == "CHIP" and (name is None or (len(s.header) > 1 and s.header[1].strip('"').lower() == name.lower())):
            return s
    return None


def device(sections, name=None):
    """The device a CHIP block assigns ("AUTO" if it leaves the choice to the compiler), or None."""
    c = chip(sections, name)
    return c.get("DEVICE") if c else None


def auto_devices(sections):
    """DEFAULT_DEVICES' AUTO_DEVICE list, in file order."""
    for s in sections:
        if s.kind == "DEFAULT_DEVICES":
            return s.all("AUTO_DEVICE")
    return []


def pins(sections, name=None):
    """{node: pin number} from a CHIP block's INPUT_PIN / OUTPUT_PIN / BIDIR_PIN assignments."""
    c = chip(sections, name)
    out = {}
    for node, kind, value in (c.entries if c else []):
        if kind and kind.upper() in ("INPUT_PIN", "OUTPUT_PIN", "BIDIR_PIN", "PIN") and value.isdigit():
            out[node.lstrip("|")] = int(value)            # .acf names carry a leading "|"
    return out
