"""Part names to device files, from the user's MAX+PLUS II partinfo.crf.

Each line: part name, assembler file (.daf), architecture file (.maf), device file (.pcf),
device description file (.ddf), part ID, then further fields; the eighth field names the
delay model (.dmf) for that speed grade.
"""
import pathlib


class Part:
    def __init__(self, name, fields):
        self.name = name
        self.daf, self.maf, self.pcf, self.ddf = fields[0], fields[1], fields[2], fields[3]
        self.part_id = int(fields[4]) if fields[4].isdigit() else None
        self.dmf = fields[6] if len(fields) > 6 else None


def load(path):
    parts = {}
    for line in pathlib.Path(path).read_text(encoding="latin1").splitlines()[1:]:
        words = line.split()
        if len(words) >= 8:
            parts[words[0].upper()] = Part(words[0], words[1:])
    return parts


def lookup(parts, name):
    """The Part for a name, allowing the speed grade to be left off (EPM7032LC44 for
    EPM7032LC44-6): an exact match first, then the bare part name."""
    key = name.upper()
    if key in parts:
        return parts[key]
    return parts.get(key.split("-")[0])
