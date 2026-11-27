"""Compile AHDL probes with Quartus II 13.0 SP1 (installed in vendor/quartus-13.0sp1) inside a
32-bit Debian container, and turn the POF into SVF and Jam JTAG programming files.

    python tools/quartus_compile.py probes/u01_x.tdf [more.tdf ...]

Each design's .acf (MAX+PLUS II assignments: DEVICE and |pin : INPUT_PIN = n) becomes a
Quartus .qsf. Results go to vendor/quartus-13.0sp1/out/<design>/ (POF, SVF, JAM, reports).
"""
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
Q = ROOT / "vendor/quartus-13.0sp1"
IMAGE = "i386/debian:buster"


def _quartus_name(node, groups):
    """MAX+PLUS II calls bit 3 of group din[] "din3"; Quartus calls it "din[3]"."""
    m = re.fullmatch(r"(\w*?[A-Za-z_])(\d+)", node)
    return "%s[%s]" % (m.group(1), m.group(2)) if m and m.group(1).lower() in groups else node


def qsf_from_acf(name, acf_text, tdf_text=""):
    device = re.search(r"DEVICE\s*=\s*([\w-]+)", acf_text)
    lines = ['set_global_assignment -name FAMILY "MAX7000S"',
             "set_global_assignment -name DEVICE %s" % (device.group(1) if device else "EPM7064SLC44-10"),
             "set_global_assignment -name TOP_LEVEL_ENTITY %s" % name,
             "set_global_assignment -name AHDL_FILE %s.tdf" % name,
             "set_global_assignment -name RESERVE_ALL_UNUSED_PINS \"AS INPUT TRI-STATED\"",
             # keep LCELL primitives as their own macrocells (probes place nodes with them)
             "set_global_assignment -name IGNORE_LCELL_BUFFERS OFF"]
    groups = {g.lower() for g in re.findall(r"(\w+)\s*\[\s*\d+\s*\.\.", tdf_text)}
    for node, pin in re.findall(r"\|(\w+)\s*:\s*(?:INPUT|OUTPUT|BIDIR)_PIN\s*=\s*(\d+)", acf_text):
        lines.append("set_location_assignment PIN_%s -to %s" % (pin, _quartus_name(node, groups)))
    for node, lc in re.findall(r"\|(\w+)\s*:\s*LOCATION\s*=\s*LC(\d+)", acf_text):   # buried node -> macrocell
        lines.append("set_location_assignment LC%s -to %s" % (lc, _quartus_name(node, groups)))
    return "\n".join(lines) + "\n"


def compile_design(tdf):
    tdf = pathlib.Path(tdf)
    name = tdf.stem
    out = Q / "out" / name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copy(tdf, out / tdf.name)
    acf = tdf.with_suffix(".acf")
    (out / (name + ".qpf")).write_text("PROJECT_REVISION = \"%s\"\n" % name)
    (out / (name + ".qsf")).write_text(qsf_from_acf(name, acf.read_text() if acf.exists() else "", tdf.read_text()))
    bin_ = "/opt/altera/13.0sp1/quartus/bin"
    script = " && ".join([
        "cd /work",
        "%s/quartus_map %s" % (bin_, name),
        "%s/quartus_fit %s" % (bin_, name),
        "%s/quartus_asm %s" % (bin_, name),
        "%s/quartus_cpf -c -q 1MHz -g 5.0 -n p %s.pof %s.svf" % (bin_, name, name),
        "%s/quartus_cpf -c %s.pof %s.jam" % (bin_, name, name),
    ])
    r = subprocess.run(["docker", "run", "--rm", "--platform", "linux/386",
                        "-v", "%s:/opt/altera:ro" % (Q / "install"), "-v", "%s:/work" % out,
                        # the launcher refuses CPUs whose /proc/cpuinfo lacks "sse"; under emulation the
                        # host's ARM cpuinfo shows through, so describe the emulated x86 CPU instead
                        "-v", "%s:/proc/cpuinfo:ro" % (Q / "cpuinfo"),
                        IMAGE, "bash", "-c", script], capture_output=True, text=True)
    (out / "build.log").write_text(r.stdout + r.stderr)
    errors = [l for l in (r.stdout + r.stderr).splitlines() if "Error" in l][:5]
    print("%-20s %s %s" % (name, "ok" if r.returncode == 0 else "FAILED (%d)" % r.returncode, " | ".join(errors)))
    return r.returncode == 0


if __name__ == "__main__":
    sys.exit(0 if all([compile_design(t) for t in sys.argv[1:]]) else 1)
