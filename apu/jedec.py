"""JEDEC fuse files, and conversion between fuse lists and device images.

A fuse is 1 exactly when (cell & mask) is non-zero, where cell is the device cell the fuse map
(the .pcf's JEDEC runs) assigns it — the rule MAX+PLUS II's own POF-to-JEDEC converter applies
(p2j.dll 0x10002c24), with no inversion.
"""
import re

STX, ETX = "\x02", "\x03"


def fuses_from_image(pcf, rows):
    """Fuse list (0/1 ints, JEDEC order) from an image {line: [cells]}."""
    return [1 if rows[row][col] & mask else 0 for _, row, col, mask in pcf.fuses()]


def image_from_fuses(pcf, fuses, rows=None, blank=0x3FF):
    """Image {line: [cells]} from a fuse list. Cells start from `rows` if given (so bits no
    fuse covers keep their values), otherwise all-ones cells sized by the device's regions."""
    if rows is None:
        rows = {}
        for r in pcf.regions:
            if r.kind in (0x100, 0x10):
                continue
            for line in range(r.row, r.row + r.n):
                cells = rows.setdefault(line, [])
                cells.extend([blank] * (r.col + r.count - len(cells)))
    else:
        rows = {k: list(v) for k, v in rows.items()}
    expected = sum(1 for _ in pcf.fuses())
    if len(fuses) != expected:
        raise ValueError("fuse count %d does not match the device's %d" % (len(fuses), expected))
    for (_, row, col, mask), bit in zip(pcf.fuses(), fuses):
        rows[row][col] = rows[row][col] | mask if bit else rows[row][col] & ~mask
    return rows


def fuse_checksum(fuses) -> int:
    total = 0
    for i in range(0, len(fuses), 8):
        total += sum(b << k for k, b in enumerate(fuses[i:i + 8]))
    return total & 0xFFFF


def write(fuses, title="", pins=None, device=None, security=None) -> str:
    """JEDEC text. Fields follow what MAX+PLUS II writes (QF, QP, ND for the device, G for the
    security bit); fuse data goes out as 64-fuse L lines."""
    body = ["%s*" % title, "QF%d*" % len(fuses)]
    if pins:
        body.append("QP%d*" % pins)
    if device:
        body.append("ND %s *" % device)
    if security is not None:
        body.append("G%d*" % (1 if security else 0))
    body.append("F0*")
    for a in range(0, len(fuses), 64):
        body.append("L%06d %s*" % (a, "".join(map(str, fuses[a:a + 64]))))
    body.append("C%04X*" % fuse_checksum(fuses))
    text = STX + "\r\n".join(body) + "\r\n" + ETX
    return text + "%04X" % (sum(text.encode("latin1")) & 0xFFFF)


def parse(text: str):
    """Return {'fuses': [...], 'device', 'pins', 'security', 'checksum_ok', 'file_checksum_ok'}."""
    start, end = text.find(STX), text.find(ETX)
    if start < 0 or end < 0:
        raise ValueError("not a JEDEC file (no STX/ETX)")
    body = text[start + 1:end]
    out = {"device": None, "pins": None, "security": None, "checksum_ok": None}
    tail = text[end + 1:end + 5]
    out["file_checksum_ok"] = (re.fullmatch(r"[0-9A-Fa-f]{4}", tail) is not None
                               and int(tail, 16) in (0, sum(text[start:end + 1].encode("latin1")) & 0xFFFF))
    fuses, default, stored = None, 0, None
    for field in body.split("*")[1:]:              # the first field is the free-text header
        f = field.strip()
        if not f:
            continue
        key = f[0]
        if key == "Q" and f[1:2] == "F":
            fuses = [None] * int(f[2:].strip())
        elif key == "Q" and f[1:2] == "P":
            out["pins"] = int(f[2:].strip())
        elif key == "N" and f[1:2] == "D":
            out["device"] = f[2:].strip()
        elif key == "G":
            out["security"] = int(f[1:].strip() or 0)
        elif key == "F":
            default = int(f[1:].strip())
        elif key == "L":
            if fuses is None:
                raise ValueError("L field before QF")
            m = re.match(r"L\s*(\d+)\s*([01\s]*)$", f, re.S)
            if not m:
                raise ValueError("bad L field")
            addr = int(m.group(1))
            for i, ch in enumerate(re.sub(r"\s", "", m.group(2))):
                fuses[addr + i] = int(ch)
        elif key == "C":
            stored = int(f[1:].strip(), 16)
    if fuses is None:
        raise ValueError("no QF field")
    out["fuses"] = [default if b is None else b for b in fuses]
    if stored is not None:
        out["checksum_ok"] = stored == fuse_checksum(out["fuses"])
    return out
