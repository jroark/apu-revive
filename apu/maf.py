"""Reader for MAX+PLUS II architecture files (.maf): the device's routing description.

Layout from GE.dll's loader (ge_open_maf, 0x10023453 and the readers it calls):
    +0     "MAF\\0\\0\\0", u16 version
    +8     six u32: h0, n_table, n_dims, n_blocks, n_entries, h5
    then   n_dims records of 0x28 bytes: 32-byte name ("ROW", "COLUMN") + two u32
           n_blocks records of 0x3C bytes: 32-byte name (MCELL, GH, PIA, HIO, ...) + seven u32;
               the first u32 is the block's type number and the second its count
           n_table u32
           n_entries routing entries: five i32 (type, index, f2, n_targets, n_fuses), then
               n_targets i32 (indices of other entries this one drives) and n_fuses pairs
               (fuse address, flags)
On the EPM7032 the entries are, in file order, the 32 macrocells (type 0), 68 GH lines (type 1),
72 PIA entries (type 2: 36 inputs for each of the two logic array blocks) and 36 HIO entries
(type 3). A macrocell or HIO entry targets the GH line it drives; a GH line targets the PIA
inputs it can reach, and for each gives the fuse address of that input's select field and, in
flags bits 20 and up, a select value (see routing() for what is and is not known about it).
"""
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    type: int
    index: int
    f2: int
    targets: tuple
    fuses: tuple          # ((address, flags), ...)


@dataclass
class Maf:
    version: int
    header: tuple
    dims: list
    blocks: list
    table: tuple
    entries: list
    trailing: int

    def block(self, name):
        for n, fields in self.blocks:
            if n == name:
                return fields
        raise KeyError(name)

    def of_type(self, name):
        t = self.block(name)[0] if name != "MCELL" else 0
        return [e for e in self.entries if e.type == t]


def parse(data: bytes) -> Maf:
    if data[:4] != b"MAF\0":
        raise ValueError("not a MAF file")
    version = struct.unpack_from("<H", data, 6)[0]
    pos = 8
    header = struct.unpack_from("<6I", data, pos)
    pos += 24
    dims, blocks = [], []
    for _ in range(header[2]):
        dims.append((data[pos:pos + 32].split(b"\0")[0].decode("latin1"), struct.unpack_from("<2I", data, pos + 32)))
        pos += 0x28
    for _ in range(header[3]):
        blocks.append((data[pos:pos + 32].split(b"\0")[0].decode("latin1"), struct.unpack_from("<7I", data, pos + 32)))
        pos += 0x3C
    table = struct.unpack_from("<%dI" % header[1], data, pos)
    pos += 4 * header[1]
    entries = []
    for _ in range(header[4]):
        if pos + 20 > len(data):
            raise ValueError("MAF entries run past the end of the file")
        t, index, f2, n_targets, n_fuses = struct.unpack_from("<5i", data, pos)
        pos += 20
        n_targets, n_fuses = max(n_targets, 0), max(n_fuses, 0)
        targets = struct.unpack_from("<%di" % n_targets, data, pos)
        pos += 4 * n_targets
        fuses = tuple(struct.unpack_from("<2i", data, pos + 8 * j) for j in range(n_fuses))
        pos += 8 * n_fuses
        entries.append(Entry(t, index, f2, targets, fuses))
    return Maf(version, header, dims, blocks, table, entries, len(data) - pos)


def summary(data: bytes) -> dict:
    m = parse(data)
    return {"blocks": {n: f[1] for n, f in m.blocks}, "entries": len(m.entries), "trailing": m.trailing,
            "labs": struct.unpack_from("<I", data, 0x0C)[0]}


def routing(maf: Maf):
    """{select-field fuse address: (width in fuses, {value: source})} for every logic-block input.

    A GH line's fuse entries are (address, flags) with flags bits 0-9 = position in the line's
    own list, bits 10-15 = field width in fuses, bits 20-27 = the value that selects this line.
    The source is whatever entry drives the line: ("mc", n) for a macrocell's feedback or
    ("hio", n) for an I/O or dedicated-input entry (numbers as in the file, from 0).
    CAUTION: reading the value as "the number the field's fuses hold" is not confirmed and does
    not fit real chips on the larger devices. An EPM7064 and an EPM7160E both show only
    one-fuse-programmed patterns in used fields (3 and 4 patterns respectively) although the file
    lists 7-8 and 14-15 sources per field, so there the value must combine with something else
    (the E family has further entries, type 4, carrying a second fuse per input). On the EPM7032
    all four 2-fuse patterns occur. Also not established: the fuse order within a field and
    which pin each HIO entry is.
    """
    driver = {}
    for e in maf.entries:
        if e.type in (0, 3):
            for target in e.targets:
                driver[target] = ("mc" if e.type == 0 else "hio", e.index)
    fields = {}
    for i, e in enumerate(maf.entries):
        if e.type != 1:
            continue
        for address, flags in e.fuses:
            width = (flags >> 10) & 0x3F
            fields.setdefault(address, (width, {}))[1][(flags >> 20) & 0xFF] = driver.get(i, ("gh", e.index))
    return fields
