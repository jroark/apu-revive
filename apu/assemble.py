"""Fuse-level assembly records for MAX 7000 devices, and their encoding into fuse lists.

An Assembly describes a device's configuration in the terms the fuse map is built from:
  - terms[mc]: the five product terms of each macrocell (numbered from 1), each None (no
    connection: the constant VCC), "GND" (every column connected), or a list of literals:
    ("in", m, inverted) for logic-block input m, ("exp", k) for shared expander k;
  - routing[block]: the select value written into each of the block's 36 input fields
    (as a number, first fuse high; see fusemap.FuseMap.block_inputs for the sources);
  - options[mc]: {element id: value} for the per-macrocell option elements, fuses MSB first;
  - other: the values of every fuse that is none of the above, by fuse number.

decode() reads a fuse list into an Assembly and encode() writes one back; the two are exact
inverses over every fuse of the device, which tests/test_assemble.py checks on a real chip.
Turning a fitted netlist into an Assembly needs the option elements' meanings, which are not
established (see re/protocol.md), so that step is not here.
"""
from .fusemap import FuseMap


class Assembly:
    def __init__(self, terms, routing, options, other):
        self.terms, self.routing, self.options, self.other = terms, routing, options, other


def _routing_fields(fmap: FuseMap):
    if fmap.routing is None:
        raise ValueError("this device's routing encoding is not established")
    widths = {w for w, _ in fmap.routing}
    if widths != {2}:
        raise ValueError("routing fields of width %s are not understood" % sorted(widths))
    return fmap.routing_addresses


def layout(fmap: FuseMap):
    """Every fuse's owner: {fuse: ("term", mc, t, column) | ("route", index, bit) |
    ("option", mc, element, bit)}; fuses with no owner are the `other` fuses. Raises
    ValueError if two owners claim one fuse."""
    owner = {}

    def claim(f, what):
        if f in owner:
            raise ValueError("fuse %d is both %r and %r" % (f, owner[f], what))
        owner[f] = what
    for mc in range(1, fmap.macrocells + 1):
        base = fmap.starts[mc - 1]
        for t in range(fmap.terms):
            for c in range(fmap.inputs):
                claim(base + t * fmap.inputs + c, ("term", mc, t, c))
    for i, address in enumerate(_routing_fields(fmap)):
        claim(address, ("route", i, 1))
        claim(address + 1, ("route", i, 0))
    for eid, _, rows in fmap.options:
        for mc, fs in rows.items():
            for k, f in enumerate(fs):
                claim(f, ("option", mc, eid, len(fs) - 1 - k))
    return owner


def _column(fmap, literal):
    if literal[0] == "exp":
        return next(c for c, k in fmap.expander_of.items() if k == literal[1])
    _, m, inverted = literal
    return 2 * m + (0 if inverted else 1)                 # 2m + 1 true, 2m complement


def decode(fmap: FuseMap, fuses) -> Assembly:
    if len(fuses) != fmap.fuses:
        raise ValueError("fuse count does not match the device")
    terms = {}
    for mc in range(1, fmap.macrocells + 1):
        out = []
        for t in fmap.product_terms(fuses, mc):
            if t is None or t == "all":
                out.append(None if t is None else "GND")
            else:
                out.append([("exp", fmap.expander_of[c]) if c in fmap.expander_of else ("in", c // 2, not c % 2)
                            for c in t])
        terms[mc] = out
    addresses = _routing_fields(fmap)
    routing = {}
    for i, a in enumerate(addresses):
        routing.setdefault(i // 36 + 1, []).append(fuses[a] << 1 | fuses[a + 1])
    options = {mc: fmap.option_bits(fuses, mc) for mc in range(1, fmap.macrocells + 1)}
    owned = layout(fmap)
    other = {f: fuses[f] for f in range(fmap.fuses) if f not in owned}
    return Assembly(terms, routing, options, other)


def encode(fmap: FuseMap, asm: Assembly):
    fuses = [1] * fmap.fuses                              # erased
    for mc, terms in asm.terms.items():
        base = fmap.starts[mc - 1]
        for t, term in enumerate(terms):
            start = base + t * fmap.inputs
            if term is None:
                continue
            if term == "GND":
                fuses[start:start + fmap.inputs] = [0] * fmap.inputs
                continue
            for literal in term:
                fuses[start + _column(fmap, literal)] = 0     # programmed = literal present
    addresses = _routing_fields(fmap)
    for block, values in asm.routing.items():
        for m, v in enumerate(values):
            a = addresses[(block - 1) * 36 + m]
            fuses[a], fuses[a + 1] = (v >> 1) & 1, v & 1
    for eid, _, rows in fmap.options:
        for mc, fs in rows.items():
            value = asm.options.get(mc, {}).get(eid)
            if value is None:
                continue
            for k, f in enumerate(fs):
                fuses[f] = (value >> (len(fs) - 1 - k)) & 1
    for f, v in asm.other.items():
        fuses[f] = v
    return fuses
