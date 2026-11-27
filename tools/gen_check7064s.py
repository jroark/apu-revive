"""Generate EPM7064S fuses for designs and check them with the fuse simulator.

    python tools/gen_check7064s.py [design.tdf ...]   (default: probes/)
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from apu import ahdl_eval, daf, epm7064s, fusemap, gdf, generate7064s, maf, parts, project, synth  # noqa: E402
from fusesim_check import cosim, VENDOR  # noqa: E402

LIB = ahdl_eval.vendor_library(VENDOR / "MAX+plus_II_BASELINE")
K = VENDOR / "7k"


def base_map():
    return fusemap.FuseMap(daf.parse((K / "s7064044.daf").read_bytes()), maf.parse((K / "m7064s.maf").read_bytes()))


def build(path, fixed=None, device="EPM7064S"):
    """device "EPM7064S" (hand-built tables) or a table-described one, e.g. "EPM7128S"."""
    path = pathlib.Path(path)
    text = gdf.to_ahdl(path.read_bytes(), path.stem) if path.suffix.lower() == ".gdf" else path.read_text(encoding="latin1")
    resolver = ahdl_eval.directory_resolver(path.parent, *LIB)
    net = synth.synthesise(text, resolver)
    if device == "EPM7064S":
        part = parts.lookup(parts.load(VENDOR / "MAX+plus_II_BASELINE/partinfo.crf"), "EPM7064SLC44")
        base = base_map()
        fuses, fitted = generate7064s.compile_netlist(net, part, K, fixed or {}, base)
        return text, resolver, base, fuses, fitted
    from apu import s7k
    from s7k_probes import DEVICES
    tables = s7k.Tables.load(device[3:].lower())
    part = parts.lookup(parts.load(VENDOR / "MAX+plus_II_BASELINE/partinfo.crf"), DEVICES[device][0].split("-")[0])
    base = fusemap.FuseMap(daf.parse((K / (tables.daf + ".daf")).read_bytes()), maf.parse((K / (tables.maf + ".maf")).read_bytes()))
    fuses, fitted = generate7064s.compile_netlist(net, part, K, fixed or {}, base, tables)
    return text, resolver, (base, tables), fuses, fitted


def run(path, steps=300, verbose=True, device="EPM7064S"):
    try:
        text, resolver, base, fuses, fitted = build(path, device=device)
    except Exception as e:
        if verbose:
            print("%-22s not generated: %s: %s" % (pathlib.Path(path).name, type(e).__name__, str(e)[:90]))
        return None
    if device == "EPM7064S":
        fm = epm7064s.FuseMap(base)
    else:
        from apu import s7k
        fm = s7k.FuseMap(*base)
    return cosim(pathlib.Path(path).name, text, resolver, fm, fuses, fitted.pins, steps, verbose=verbose)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--device=")]
    dev = next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--device=")), "EPM7064S")
    for f in args or sorted((ROOT / "probes").glob("*.tdf")):
        run(f, device=dev)
