"""Compile MAX+PLUS II projects in the guest over tools/vmlink and fetch the results.

    python tools/vmlink/compile.py vendor/vm_link probes/r01_b1_to_b2.tdf [more .tdf ...]

Each design's .tdf and .acf go to C:\\max2work\\probes, are re-stamped with the guest's clock
(the compiler refuses sources newer than its outputs), compiled with `maxplus2 -c`, and the
.pof, .rpt, .fit and .pin come back into <link dir>/inbox.
"""
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from q import queue  # noqa: E402

GUEST = "C:\\max2work\\probes"


def compile_design(root, tdf):
    root, tdf = pathlib.Path(root), pathlib.Path(tdf)
    name = tdf.stem
    for ext in (".tdf", ".acf"):
        src = tdf.with_suffix(ext)
        if src.exists():
            (root / "outbox" / src.name).write_bytes(src.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
            print(queue(root, "PUT %s %s\\%s" % (src.name, GUEST, src.name)).strip())
    out = queue(root, 'RUN (cd /d %s & del %s.pof %s.fit %s.rpt %s.cnf 2>nul & copy /b %s.tdf+,, %s.tdf >nul & '
                      'copy /b %s.acf+,, %s.acf >nul & C:\\maxplus2\\maxplus2 -c %s)'
                % ((GUEST,) + (name,) * 9), 1800)
    all_lines = out.splitlines()
    lines = [l + ("  " + all_lines[i + 1].strip() if l.startswith("Error") and i + 1 < len(all_lines) else "")
             for i, l in enumerate(all_lines) if l.startswith(("Error", "Info", "Project compilation"))]
    print("\n".join(lines))
    for ext in ("pof", "rpt", "fit", "pin"):
        print(queue(root, "SEND %s\\%s.%s" % (GUEST, name, ext)).strip())
    return "compilation was successful" in out


if __name__ == "__main__":
    ok = [compile_design(sys.argv[1], t) for t in sys.argv[2:]]
    sys.exit(0 if all(ok) else 1)
