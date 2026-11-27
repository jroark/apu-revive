"""JTAG programming files (SVF) from a device template (apu/data/<tag>_isp.json.gz).

    svf_template.write("7128s", fuses, "program" | "verify", jtag_enabled=True) -> SVF text

The template (tools/s7k_svf_template.py) is quartus_cpf's program / verify sequence for the
device with every data bit traced to the fuse it carries, found by converting index-coded POFs;
see apu.svf7064s for the algorithm it encodes (the EPM7064S writer is hand-modelled and checked
the same way). A few bits do not come from the fuses: Quartus sets them for every design it
compiles (JTAG left enabled); they are written 1 unless jtag_enabled is False, because clearing
them could turn the JTAG pins into I/O and lock out in-system programming.
"""
import functools
import gzip
import json
import pathlib

DATA = pathlib.Path(__file__).parent / "data"


@functools.lru_cache(maxsize=None)
def template(tag):
    return json.loads(gzip.decompress((DATA / (tag + "_isp.json.gz")).read_bytes()))


def _value(spec, fuses, n):
    if isinstance(spec, str):
        return int(spec, 16)
    v, b = 0, 0
    for start, step, count in spec:
        if start < 0:
            if start == -2:
                v |= ((1 << count) - 1) << b
        else:
            for k in range(count):
                if fuses[start + step * k]:
                    v |= 1 << (b + k)
        b += count
    return v


def write(tag, fuses, operation="program", jtag_enabled=True):
    t = template(tag)
    if len(fuses) != t["fuses"]:
        raise ValueError("this device has %d fuses" % t["fuses"])
    forced = {}
    if jtag_enabled and t.get("jtag_enable"):
        for i, field, bit in t["jtag_enable"].get(operation, []):
            forced.setdefault((i, field), []).append(bit)
    lines = ["!apu-revive %s %s" % (tag, operation)]
    for i, st in enumerate(t[operation]):
        if isinstance(st, str):
            lines.append(st + ";")
            continue
        n = st["n"]
        digits = (n + 3) // 4
        parts = ["SDR %d" % n]
        for field in ("TDI", "TDO", "MASK"):
            if field in st:
                v = _value(st[field], fuses, n)
                for bit in forced.get((i, field), ()):
                    v |= 1 << bit
                parts.append("%s (%0*X)" % (field, digits, v))
        lines.append(" ".join(parts) + ";")
    return "\n".join(lines) + "\n"
