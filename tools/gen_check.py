"""Generate EPM7032 fuses for designs and check them with the fuse simulator.

    python tools/gen_check.py [design.tdf|.gdf ...]   (default: probes/ and the tutorial)
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from apu import ahdl_eval, gdf, generate, parts, project, synth  # noqa: E402
from fusesim_check import cosim, epm7032, VENDOR  # noqa: E402

LIB = ahdl_eval.vendor_library(VENDOR / "MAX+plus_II_BASELINE")


def run(path, steps=300, verbose=True):
    path = pathlib.Path(path)
    text = gdf.to_ahdl(path.read_bytes(), path.stem) if path.suffix.lower() == ".gdf" else path.read_text(encoding="latin1")
    resolver = ahdl_eval.directory_resolver(path.parent, *LIB)
    try:
        net = synth.synthesise(text, resolver)
        part = parts.lookup(parts.load(VENDOR / "MAX+plus_II_BASELINE/partinfo.crf"), "EPM7032LC44")
        fitted = project.fit_part(net, part, VENDOR / "7k")
        fm = epm7032()
        fuses = generate.generate(net, fitted.result, fitted.placement, fitted.pins, fm)
    except Exception as e:
        if verbose:
            print("%-22s not generated: %s: %s" % (path.name, type(e).__name__, str(e)[:90]))
        return None
    return cosim(path.name, text, resolver, fm, fuses, fitted.pins, steps, verbose=verbose)


if __name__ == "__main__":
    files = sys.argv[1:] or sorted(list((ROOT / "probes").glob("*.tdf")) +
                                   [p for p in (VENDOR / "MAX+plus_II_Tutorial").rglob("*") if p.suffix.lower() in (".tdf", ".gdf")])
    for f in files:
        run(f)
