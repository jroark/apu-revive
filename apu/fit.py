"""Resource fitting for MAX 7000 devices: does a synthesised design fit, and where would it go?

This is the first half of a fitter. It packs a Netlist's outputs, registers and buried logic cells into macrocells
and logic array blocks under the architecture's published limits and reports the result. It
does not route signals or produce fuses (the routing encoding is not decoded yet), so a design
that passes here is not guaranteed to be routable.

Limits used (MAX 7000 family data sheet; device sizes come from the device's assembler file):
  - 16 macrocells per logic array block, five product terms per macrocell;
  - a product-term clock or clock enable takes term 3, a product-term clear term 2 and a
    product-term preset term 4 (see re/protocol.md; hypotheses from the fuse-map work), so each
    one a macrocell needs leaves one term fewer for its sum;
  - a sum that needs more terms borrows whole neighbouring macrocells' terms through parallel
    expanders, up to 20 terms in all; each lender is counted as a used macrocell;
  - each block can take at most 36 distinct signals from the rest of the device.
"""
from dataclasses import dataclass, field

TERMS_PER_MACROCELL = 5
MAX_TERMS = 20
BLOCK_SIZE = 16
BLOCK_INPUTS = 36


class DoesNotFit(Exception):
    pass


@dataclass
class Cell:
    name: str
    registered: bool
    sum_terms: int
    special: dict                      # {"clk"|"clrn"|"prn"|"oe": "global:<signal>" or "term"}
    inputs: set                        # signals its equations read
    lenders: int = 0                   # extra macrocells lent to it through parallel expanders
    block: int = None

    @property
    def macrocells(self):
        return 1 + self.lenders


@dataclass
class Fit:
    device: str
    cells: list
    blocks: list = field(default_factory=list)      # [[Cell, ...], ...]
    global_clock: str = None
    global_clear: str = None
    macrocells_available: int = 0

    def report(self) -> str:
        used = sum(c.macrocells for c in self.cells)
        lines = ["device %s: %d of %d macrocells used (%d by parallel expanders), %d of %d blocks" % (
            self.device, used, self.macrocells_available, sum(c.lenders for c in self.cells),
            len([b for b in self.blocks if b]), len(self.blocks))]
        if self.global_clock:
            lines.append("global clock: %s" % self.global_clock)
        if self.global_clear:
            lines.append("global clear: %s" % self.global_clear)
        for i, block in enumerate(self.blocks):
            if not block:
                continue
            signals = set().union(*(c.inputs for c in block))
            lines.append("block %d: %d macrocells, %d input signals" % (i + 1, sum(c.macrocells for c in block), len(signals)))
            for c in block:
                extra = ", ".join("%s=%s" % kv for kv in sorted(c.special.items()))
                lines.append("    %-16s %s, %d product terms%s%s" % (
                    c.name, "registered" if c.registered else "combinational", c.sum_terms,
                    (", +%d lender(s)" % c.lenders) if c.lenders else "", (", " + extra) if extra else ""))
        return "\n".join(lines)


def _single_input(eq, inputs):
    """The input pin name if the equation is exactly that pin, uninverted; else None."""
    if not eq.invert and len(eq.terms) == 1 and len(eq.terms[0]) == 1:
        (signal, value), = eq.terms[0].items()
        if value == 1 and signal in inputs:
            return signal
    return None


def _signals(*equations):
    out = set()
    for eq in equations:
        for term in eq.terms:
            out.update(term)
    return out


def cells_of(netlist, global_clocks: int = 1, not_global=()):
    """The macrocells a netlist needs: (cells with their lenders, global clocks, global clear).
    Inputs in `not_global` (assigned to pins other than the dedicated ones) are not made the
    global clock or clear. Raises DoesNotFit when one sum needs more product terms than a
    macrocell can gather."""
    inputs = set(netlist.design.inputs)
    candidates = inputs - set(not_global)
    # choose the global clock: the input pin clocking the most registers
    votes = {}
    for eqs in netlist.registers.values():
        pin = _single_input(eqs["clk"], inputs) if "clk" in eqs else None
        if pin and pin in candidates:
            votes[pin] = votes.get(pin, 0) + 1
    clocks = sorted(votes, key=lambda p: -votes[p])[:global_clocks]
    clears = {}
    for eqs in netlist.registers.values():
        if "clrn" in eqs:
            eq = eqs["clrn"]
            if eq.invert is False and len(eq.terms) == 1 and len(eq.terms[0]) == 1:
                (signal, value), = eq.terms[0].items()
                # the global clear pin is active low: it can only implement clrn = <pin>
                if signal in candidates and value == 1:
                    clears[(signal, value)] = clears.get((signal, value), 0) + 1
    global_clear = max(clears, key=clears.get) if clears else None

    cells = []
    for pin, eq in netlist.outputs.items():
        special, reads = {}, _signals(eq)
        if pin in netlist.enables:
            oe = netlist.enables[pin]
            special["oe"] = "global:%s" % _single_input(oe, inputs) if _single_input(oe, inputs) else "term"
            reads |= _signals(oe)
        # an output that simply repeats a register is that register's macrocell, not a new one
        if len(eq.terms) == 1 and len(eq.terms[0]) == 1 and not eq.invert and "oe" not in special:
            (signal, value), = eq.terms[0].items()
            if value == 1 and signal.endswith(".q") and signal[:-2] in netlist.registers:
                continue
        cells.append(Cell(pin, False, len(eq.terms), special, reads))
    for bit, eqs in netlist.registers.items():
        special, reads = {}, _signals(*eqs.values())
        if "clk" in eqs:
            pin = _single_input(eqs["clk"], inputs)
            special["clk"] = "global:%s" % pin if pin in clocks else "term"
        if "clrn" in eqs:
            eq = eqs["clrn"]
            single = (len(eq.terms) == 1 and len(eq.terms[0]) == 1 and not eq.invert
                      and next(iter(eq.terms[0].items())) == global_clear)
            special["clrn"] = "global:%s" % global_clear[0] if single else "term"
        if "prn" in eqs:
            special["prn"] = "term"
        data = eqs.get("d") or eqs["t"]
        if "t" in eqs:
            special["mode"] = "toggle"
        cells.append(Cell(bit, True, len(data.terms), special, reads))
    for name, eq in getattr(netlist, "nodes", {}).items():     # buried combinational logic cells
        cells.append(Cell(name, False, len(eq.terms), {}, _signals(eq)))

    for c in cells:
        reserved = sum(1 for k, v in c.special.items() if v == "term" and k != "mode")
        own = TERMS_PER_MACROCELL - reserved
        if c.sum_terms > MAX_TERMS - reserved:
            raise DoesNotFit("%s needs %d product terms; a macrocell can gather at most %d" % (
                c.name, c.sum_terms, MAX_TERMS - reserved))
        if c.sum_terms > own:
            c.lenders = -(-(c.sum_terms - own) // TERMS_PER_MACROCELL)
    return cells, clocks, global_clear


def macrocells_needed(netlist) -> int:
    return sum(c.macrocells for c in cells_of(netlist)[0])


def fit(netlist, device: str, macrocells: int, global_clocks: int = 1, pinned=None, not_global=(),
        pin_slots=None, block_caps=None) -> Fit:
    """Pack `netlist` into a device with `macrocells` macrocells. `pinned` = {cell name:
    macrocell} fixes cells (from pin assignments; see pin_constraints). `pin_slots`: per block,
    how many of its macrocells have a pin (cells driving output pins need one); None = all.
    `block_caps`: per block, how many macrocells to fill at most (default 16 each).
    Raises DoesNotFit."""
    cells, clocks, global_clear = cells_of(netlist, global_clocks, not_global)
    pinned = pinned or {}
    needed = sum(c.macrocells for c in cells)
    if needed > macrocells:
        raise DoesNotFit("needs %d macrocells; %s has %d" % (needed, device, macrocells))

    blocks = [[] for _ in range(macrocells // BLOCK_SIZE)]
    for c in cells:                                   # assigned cells go to their macrocell's block
        if c.name in pinned:
            c.block = (pinned[c.name] - 1) // BLOCK_SIZE
            blocks[c.block].append(c)
    for i, block in enumerate(blocks):
        if sum(x.macrocells for x in block) > BLOCK_SIZE:
            raise DoesNotFit("the cells assigned to block %d need more than %d macrocells" % (i + 1, BLOCK_SIZE))
    owners = {_owner(netlist, o) for o in netlist.design.outputs if o in netlist.outputs}
    # largest input sets first; put each cell where it adds the fewest new block inputs
    caps = block_caps or [BLOCK_SIZE] * len(blocks)
    for c in sorted((c for c in cells if c.name not in pinned), key=lambda c: -len(c.inputs)):
        best = None
        for i, block in enumerate(blocks):
            if sum(x.macrocells for x in block) + c.macrocells > caps[i]:
                continue
            if pin_slots is not None and c.name in owners and sum(x.name in owners for x in block) >= pin_slots[i]:
                continue
            present = set().union(*(x.inputs for x in block)) if block else set()
            total = len(present | c.inputs)
            if total > BLOCK_INPUTS:
                continue
            cost = (total - len(present), i)
            if best is None or cost < best[0]:
                best = (cost, i)
        if best is None:
            raise DoesNotFit("%s cannot be placed: no block has room within the %d-input limit%s" % (
                c.name, BLOCK_INPUTS, " and a free pin" if pin_slots is not None and c.name in owners else ""))
        c.block = best[1]
        blocks[best[1]].append(c)
    return Fit(device, cells, blocks, clocks[0] if clocks else None,
               ("%s%s" % ("" if global_clear[1] else "!", global_clear[0])) if global_clear else None, macrocells)


# Dedicated input pins by package: (global clock, global clear, output enable 1, output enable 2
# / second clock). From the MAX 7000 data sheet pin-out tables; the assembler file's oe_pin
# table gives the same two enable pins.
DEDICATED = {44: (43, 1, 44, 2), 68: (67, 1, 68, 2), 84: (83, 1, 84, 2)}


def _owner(netlist, pin_name):
    """The cell that drives an output pin: the pin's own, or the register it repeats."""
    eq = netlist.outputs[pin_name]
    if len(eq.terms) == 1 and len(eq.terms[0]) == 1 and not eq.invert and pin_name not in netlist.enables:
        (signal, value), = eq.terms[0].items()
        if value == 1 and signal.endswith(".q") and signal[:-2] in netlist.registers:
            return signal[:-2]
    return pin_name


def pin_constraints(netlist, fixed, pin_mc):
    """From pin assignments {port: pin}: ({cell: macrocell} for assigned outputs, the inputs
    assigned away from the dedicated clock and clear pins). Raises DoesNotFit for a pin the
    package lacks or an output on a pin with no macrocell."""
    pins = len(pin_mc) - 1
    gclk, gclr = DEDICATED[pins][:2] if pins in DEDICATED else (None, None)
    pinned, not_global = {}, []
    for port, pin in fixed.items():
        if not 1 <= pin <= pins:
            raise DoesNotFit("%s is assigned to pin %d; the package has %d" % (port, pin, pins))
        if port in netlist.outputs:
            if not pin_mc[pin]:
                raise DoesNotFit("output %s is assigned to pin %d, which has no macrocell" % (port, pin))
            pinned[_owner(netlist, port)] = pin_mc[pin]
        elif port in netlist.design.inputs and pin not in (gclk, gclr):
            not_global.append(port)
    return pinned, not_global


def assign_pins(result: Fit, netlist, pin_mc, fixed=None):
    """Give every cell a macrocell number and every port a pin.

    `pin_mc` is the device file's table: pin_mc[pin] = macrocell on that pin, 0 if none.
    A cell's lenders take the macrocell numbers just below it in the same block (parallel
    expanders chain between neighbours). Outputs come out on their macrocell's pin; the global
    clock and clear go to their dedicated pins; other inputs take the pins of macrocells whose
    own output is not needed on a pin, then the spare dedicated inputs.
    Returns ({cell name: macrocell}, {port: pin}). Raises DoesNotFit when pins run out.
    """
    pins = len(pin_mc) - 1
    if pins not in DEDICATED:
        raise DoesNotFit("no dedicated-pin table for a %d-pin package" % pins)
    gclk, gclr, oe1, oe2 = DEDICATED[pins]
    pin_of = {mc: pin for pin, mc in enumerate(pin_mc) if mc}
    fixed = fixed or {}
    pinned, _ = pin_constraints(netlist, fixed, pin_mc)
    placement, port_pin = {}, {}
    for b, block in enumerate(result.blocks):
        free = set(range(b * BLOCK_SIZE + 1, (b + 1) * BLOCK_SIZE + 1))
        for cell in block:                              # assigned cells, lenders just below them
            if cell.name in pinned:
                mc = pinned[cell.name]
                need = set(range(mc - cell.lenders, mc + 1))
                if (mc - 1) // 8 != (mc - 1 - cell.lenders) // 8:
                    raise DoesNotFit("%s on macrocell %d cannot borrow %d expanders across a chain of 8" % (cell.name, mc, cell.lenders))
                if not need <= free:
                    raise DoesNotFit("macrocell %d (for %s) and its lenders are not free" % (mc, cell.name))
                placement[cell.name] = mc
                free -= need
        # cells that drive an output pin go on macrocells with pins, first; buried cells take
        # pinless macrocells when there are any (on the EPM7032 every macrocell has a pin)
        owners = {_owner(netlist, o) for o in netlist.design.outputs if o in netlist.outputs}
        # longest expander chains first (they need runs of free macrocells), then pin owners
        for cell in sorted(block, key=lambda c: (-c.lenders, c.name not in owners)):
            if cell.name in pinned:
                continue
            with_pin = sorted(mc for mc in free if mc in pin_of)
            if cell.name in owners:
                order = with_pin
            else:
                order = sorted(mc for mc in free if mc not in pin_of) + with_pin
            # parallel expanders chain within groups of 8 macrocells (1-8, 9-16, ...)
            spot = next((mc for mc in order if set(range(mc - cell.lenders, mc + 1)) <= free
                         and (mc - 1) // 8 == (mc - 1 - cell.lenders) // 8), None)
            if spot is None:
                e = DoesNotFit("no run of %d free macrocells in block %d for %s" % (cell.macrocells, b + 1, cell.name))
                e.block = b
                raise e
            placement[cell.name] = spot
            free -= set(range(spot - cell.lenders, spot + 1))
    design = netlist.design
    used_pins = set()
    for pin_name in design.outputs:
        eq = netlist.outputs[pin_name]
        owner = pin_name
        if pin_name not in placement:                     # the pin repeats a register
            (signal, _), = eq.terms[0].items()
            owner = signal[:-2]
        mc = placement[owner]
        if mc not in pin_of:
            raise DoesNotFit("macrocell %d (for %s) has no pin on this package" % (mc, pin_name))
        if pin_of[mc] in used_pins:
            raise DoesNotFit("%s and another output share macrocell %d" % (pin_name, mc))
        port_pin[pin_name] = pin_of[mc]
        used_pins.add(pin_of[mc])
    for name, pin in fixed.items():
        if name in design.outputs and port_pin.get(name) != pin:
            raise DoesNotFit("%s could not be put on its assigned pin %d" % (name, pin))
    spare = [oe2, oe1, gclr, gclk]
    for name, pin in ((result.global_clock, gclk), (result.global_clear.lstrip("!") if result.global_clear else None, gclr)):
        if name:
            if fixed.get(name, pin) != pin:
                raise DoesNotFit("%s is the global signal but is assigned to pin %d" % (name, fixed[name]))
            port_pin[name] = pin
            spare.remove(pin)
    for name, pin in fixed.items():                      # assigned inputs take their pins
        if name in design.inputs and name not in port_pin:
            if pin in used_pins or pin in port_pin.values():
                raise DoesNotFit("pin %d is assigned to %s but already in use" % (pin, name))
            mc = pin_mc[pin]
            if mc and mc in placement.values() and mc in [placement.get(_owner(netlist, o)) for o in design.outputs]:
                raise DoesNotFit("pin %d is assigned to input %s but its macrocell drives an output" % (pin, name))
            port_pin[name] = pin
            if pin in spare:
                spare.remove(pin)
    taken = set(port_pin.values())
    free = [pin_of[mc] for mc in sorted(pin_of) if pin_of[mc] not in used_pins and mc not in placement.values()]
    free += [pin_of[mc] for mc in sorted(pin_of) if pin_of[mc] not in used_pins and mc in placement.values()
             and pin_of[mc] not in free]
    # buried macrocells' pins come last: using them as inputs costs nothing but is less tidy
    candidates = [p for p in free + spare if p not in taken]
    for name in design.inputs:
        if name in port_pin:
            continue
        if not candidates:
            raise DoesNotFit("not enough pins: no pin left for input %s" % name)
        port_pin[name] = candidates.pop(0)
    return placement, port_pin
