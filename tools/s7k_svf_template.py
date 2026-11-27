"""Build a device's SVF template from quartus_cpf conversions of index-coded POFs.

    python tools/s7k_svf_template.py TAG DIR FUSES [REAL_NAME]

DIR holds bJJp / bJJn .p.svf and .v.svf (fuse f = bit J of f, and its complement). Every data bit
of the program and verify files is traced to a fuse or found constant; the result,
apu/data/<TAG>_isp.json.gz, lets apu.svf_template write the same files for any fuse list.
REAL_NAME: a Quartus-compiled design (vendor/quartus-13.0sp1/out/<name>) whose own SVF shows
which constant bits Quartus sets for its designs (the JTAG-enable bit) and checks the result.
"""
import gzip
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def statements(text):
    """[(kind, n, {field: int}, raw text)] with comments dropped."""
    out = []
    for stmt in re.sub(r"!.*", "", text).split(";"):
        st = " ".join(stmt.split())
        if not st:
            continue
        w = st.split()
        if w[0] in ("SIR", "SDR"):
            f = {k: int(v.replace(" ", ""), 16) for k, v in re.findall(r"(TDI|TDO|MASK)\s*\(\s*([0-9A-Fa-f\s]+)\)", st)}
            out.append((w[0], int(w[1]), f, st))
        else:
            out.append((w[0], 0, {}, st))
    return out


def runs(bits):
    """[fuse or -1 / -2 (constant 0 / 1)] -> [[start, step, count]]."""
    out = []
    for b in bits:
        if out:
            s, step, n = out[-1]
            if b < 0 and s == b and step == 0:
                out[-1][2] += 1
                continue
            if b >= 0 and s >= 0 and (n == 1 or b == s + step * n) and (n > 1 or True):
                if n == 1:
                    out[-1][1] = b - s
                    out[-1][2] = 2
                    continue
                if b == s + step * n:
                    out[-1][2] += 1
                    continue
        out.append([b, 0, 1])
    return out


def build(directory, nbits, kind):
    d = pathlib.Path(directory)
    files = {(j, inv): statements((d / ("b%02d%s.%s.svf" % (j, "n" if inv else "p", kind))).read_text())
             for j in range(nbits) for inv in (0, 1)}
    ref = files[(0, 0)]
    assert all(len(f) == len(ref) for f in files.values())
    tpl = []
    for i, (k, n, f, raw) in enumerate(ref):
        if k != "SDR":
            assert all(files[key][i][3] == raw for key in files), raw
            tpl.append(raw)
            continue
        entry = {"n": n}
        for field in ("TDI", "TDO", "MASK"):
            if field not in f:
                continue
            vals = {key: files[key][i][2][field] for key in files}
            if len(set(vals.values())) == 1:
                entry[field] = "%x" % f[field]
                continue
            bits = []
            for b in range(n):
                p = [vals[(j, 0)] >> b & 1 for j in range(nbits)]
                q = [vals[(j, 1)] >> b & 1 for j in range(nbits)]
                if p == q:
                    bits.append(-1 - p[0])
                else:
                    assert all(x != y for x, y in zip(p, q))
                    bits.append(sum(x << j for j, x in enumerate(p)))
            entry[field] = runs(bits)
        tpl.append(entry)
    return tpl


def main():
    tag, directory, fuses = sys.argv[1], sys.argv[2], int(sys.argv[3])
    nbits = (fuses - 1).bit_length()
    data = {"fuses": fuses, "program": build(directory, nbits, "p"), "verify": build(directory, nbits, "v"), "jtag_enable": []}
    path = ROOT / "apu/data" / (tag + "_isp.json.gz")
    path.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0))
    print("wrote", path, path.stat().st_size, "bytes;", len(data["program"]), "program /", len(data["verify"]), "verify statements")
    if len(sys.argv) > 4:
        from apu import pof, svf_template
        name = sys.argv[4]
        out = ROOT / "vendor/quartus-13.0sp1/out" / name
        fz = pof.logical_fuses(pof.Pof.from_bytes((out / (name + ".pof")).read_bytes()))
        ours = statements(svf_template.write(tag, fz, "program", jtag_enabled=False))
        theirs = statements((out / (name + ".svf")).read_text())
        diffs = []
        for i, (a, b) in enumerate(zip(ours, theirs)):
            for field in ("TDI", "TDO"):
                x = a[2].get(field, 0) ^ b[2].get(field, 0)
                diffs += [(i, field, bit) for bit in range(a[1]) if x >> bit & 1]
        print("bits Quartus sets differently for its own design:", diffs[:12], len(diffs))
        if diffs and len(diffs) <= 8:
            data["jtag_enable"] = {"program": diffs}
            # the same bit in the verify file: found by the same comparison when a verify.svf exists
            v = out / "verify.svf"
            if v.exists():
                o2 = statements(svf_template.write(tag, fz, "verify", jtag_enabled=False))
                t2 = statements(v.read_text())
                data["jtag_enable"]["verify"] = [(i, field, bit) for i, (a, b) in enumerate(zip(o2, t2)) for field in ("TDI", "TDO")
                                                 for bit in range(a[1]) if (a[2].get(field, 0) ^ b[2].get(field, 0)) >> bit & 1]
            path.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0))
            print("jtag-enable bits recorded:", data["jtag_enable"])


if __name__ == "__main__":
    main()
