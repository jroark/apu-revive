"""A MAX+PLUS II project's device choice and assignments applied to fitting.

The device comes from the project's .acf CHIP block. DEVICE = AUTO means the compiler picks:
it tries the DEFAULT_DEVICES list (written largest first), and takes the smallest that fits;
the tutorial's chiptrip gets EPM7032LC44, the last entry, from the original compiler. Pin
assignments come from the same CHIP block (or from a .fit file, which has the same form).
Only MAX 7000 parts whose assembler file is the classic (m...) or E (e...) kind and whose
package's dedicated pins are known can be fitted.
"""
import pathlib

from . import acf, daf, fit, parts


class Fitted:
    def __init__(self, part, result, placement, pins):
        self.part, self.result, self.placement, self.pins = part, result, placement, pins


JTAG_PINS = {44: (7, 13, 32, 38), 84: (14, 23, 62, 71)}       # MAX 7000S: TDI, TMS, TCK, TDO (JTAG left enabled)


def fittable(part):
    return part is not None and part.daf[:1] in ("m", "e", "s")


def fit_part(net, part, sevenk_dir, fixed=None, caps=None):
    """Fit and assign pins on one part. Raises fit.DoesNotFit. `caps`: starting per-block
    macrocell limits (lowered further when expander chains find no room)."""
    if not fittable(part):
        raise fit.DoesNotFit("%s is not a MAX 7000 part this fitter handles" % (part.name if part else "?"))
    from . import synth
    synth.hoist_controls(net)              # multi-term clocks / clears / presets get a node
    d = daf.parse((pathlib.Path(sevenk_dir) / (part.daf + ".daf")).read_bytes())
    pin_mc = list(d.tables["pin_mc"])
    if part.daf[:1] == "s":                # the JTAG pins' macrocells keep no pin
        for p in JTAG_PINS.get(len(pin_mc) - 1, ()):
            pin_mc[p] = 0
    if len(pin_mc) - 1 not in fit.DEDICATED:
        raise fit.DoesNotFit("%s: dedicated pins of a %d-pin package are not known" % (part.name, len(pin_mc) - 1))
    fixed = dict(fixed or {})
    # single-input output enables go on the OE pins (the only enables the generators handle)
    oe_pins = [p for p in fit.DEDICATED.get(len(pin_mc) - 1, ())[2:] if p not in fixed.values()]
    for name, eq in net.enables.items():
        if len(eq.terms) == 1 and len(eq.terms[0]) == 1:
            (signal, _), = eq.terms[0].items()
            if signal in net.design.inputs and signal not in fixed and oe_pins:
                fixed[signal] = oe_pins.pop(0)
    pinned, not_global = fit.pin_constraints(net, fixed, pin_mc)
    n = daf.summary(d)["macrocells"]
    with_pin = {mc for mc in pin_mc if mc}
    pin_slots = [sum(1 for mc in range(b * fit.BLOCK_SIZE + 1, (b + 1) * fit.BLOCK_SIZE + 1) if mc in with_pin)
                 for b in range(n // fit.BLOCK_SIZE)]
    slots = None if all(k == fit.BLOCK_SIZE for k in pin_slots) else pin_slots
    caps = list(caps) if caps else [fit.BLOCK_SIZE] * (n // fit.BLOCK_SIZE)
    for _ in range(4 * fit.BLOCK_SIZE):
        result = fit.fit(net, part.name, n, pinned=pinned, not_global=not_global, pin_slots=slots, block_caps=caps)
        try:
            placement, pins = fit.assign_pins(result, net, pin_mc, fixed)
            break
        except fit.DoesNotFit as e:
            # a block whose expander chains find no runs of free macrocells: fill it less
            b = getattr(e, "block", None)
            if b is None or caps[b] <= 1:
                raise
            caps[b] -= 1
    return Fitted(part, result, placement, pins)


def fit_project(net, sections, chip_name, vendor_root, device=None, fixed=None):
    """Fit using the project's assignments. `device` / `fixed` override the file. Returns a
    Fitted; raises fit.DoesNotFit with the reasons when nothing fits."""
    root = pathlib.Path(vendor_root)
    catalogue = parts.load(root / "partinfo.crf")
    sevenk = root.parent / "7k"
    device = device or acf.device(sections, chip_name) or "AUTO"
    fixed = fixed if fixed is not None else acf.pins(sections, chip_name)
    if device.upper() != "AUTO":
        return fit_part(net, parts.lookup(catalogue, device), sevenk, fixed)
    reasons = []
    for name in reversed(acf.auto_devices(sections)):          # smallest first
        part = parts.lookup(catalogue, name)
        try:
            return fit_part(net, part, sevenk, fixed)
        except fit.DoesNotFit as e:
            reasons.append("%s: %s" % (name, e))
    raise fit.DoesNotFit("no device in the project's list fits:\n  " + "\n  ".join(reasons or ["(empty list)"]))
