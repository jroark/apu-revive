import argparse
import pathlib
import sys

DEFAULT_VENDOR = pathlib.Path(__file__).resolve().parent.parent / "vendor/maxplus2-10.2/MAX+plus_II_BASELINE"


def cmd_info(_args):
    import usb.core
    from .device import PID, VID
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is None:
        sys.exit("APU (0799:7651) not found on USB")
    print(dev)


def cmd_init(args):
    from .device import Apu
    vendor = pathlib.Path(args.vendor)
    apu = Apu.open()
    apu.load_firmware((vendor / "pgdmpuz.drv").read_bytes())
    print("firmware loaded")
    apu.configure_fpga((vendor / "pgdmpuz.rbf").read_bytes())
    print("FPGA configured")


def cmd_adapter(args):
    from . import board
    from .device import Apu
    vendor = pathlib.Path(args.vendor)
    apu = Apu.open()
    apu.load_firmware((vendor / "pgdmpuz.drv").read_bytes())
    apu.configure_fpga((vendor / "pgdmpuz.rbf").read_bytes())
    board.startup(apu)
    aid = board.adapter_id(apu)
    print("adapter ID %d (%#x): %s" % (aid, aid, board.KNOWN_ADAPTERS.get(aid, "unknown")))


DEVICE_FILES = {"EPM7160E": "e7160084.pcf", "EPM7160EQC160": "e7160120.pcf", "EPM7192EQC160": "e7192160.pcf", "EPM7064": "m7064084.pcf", "EPM7032": "m7032044.pcf"}


def _open_for(args):
    """Bring the unit up and return (apu, profile, device file) for --part."""
    from . import board, programmer
    from .device import Apu
    from .pcf import Pcf
    profile = programmer.PROFILES.get(args.part.upper())
    if profile is None:
        sys.exit("no native support for %r (have: %s)" % (args.part, ", ".join(programmer.PROFILES)))
    if not profile.verified and not args.allow_unverified:
        sys.exit("%s is checked against the vendor module in simulation only and has not been run on a "
                 "chip; pass --allow-unverified to try it" % profile.name)
    vendor = pathlib.Path(args.vendor)
    args.pcf = str(args.pcf or vendor.parent / "7k" / DEVICE_FILES[profile.name])
    pcf = Pcf.load(args.pcf)
    apu = Apu.open()
    apu.load_firmware((vendor / "pgdmpuz.drv").read_bytes())
    apu.configure_fpga((vendor / "pgdmpuz.rbf").read_bytes())
    programmer.initialise(apu)
    if not apu.has_9v():
        sys.exit("no 9 V supply")
    aid = board.adapter_id(apu)
    if aid not in profile.adapters:
        sys.exit("adapter ID %d is not one this device is supported on (%s)" % (aid, profile.adapters))
    return apu, profile, pcf


def cmd_id(args):
    from . import programmer
    apu, profile, pcf = _open_for(args)
    sig = programmer.silicon_id(apu, profile, pcf)
    print("silicon ID %r, following signature bytes %s" % (sig[:8].decode("latin1"), sig[8:].hex(" ")))


def cmd_read(args):
    import json
    from . import programmer
    apu, profile, pcf = _open_for(args)
    sig, rows = programmer.read_back(apu, profile, pcf, vmarg_mv=args.vmarg)
    id_row = programmer.signature_row(pcf)
    out = {"part": profile.name, "pcf": args.pcf,
           "method": "native", "bits_per_call": 16, "vmarg_mv": args.vmarg,
           "silicon_id": sig[:8].decode("latin1"), "signature_row": id_row,
           "rows": {"%#04x" % r: {"col": 0, "data": cells} for r, cells in rows.items()}}
    pathlib.Path(args.out).write_text(json.dumps(out))
    print("silicon ID %r, %d rows, %d cells -> %s" % (out["silicon_id"], len(rows), sum(map(len, rows.values())), args.out))


def cmd_pof(args):
    from . import pof
    p = pof.Pof.from_bytes(pathlib.Path(args.file).read_bytes())
    print("creator %r, device %r, comment %r" % (p.creator, p.device, p.comment))
    print("security bit %s, turbo bit %s, CRC %s" % (p.flag(pof.TAG_SECURITY), p.flag(pof.TAG_TURBO),
                                                     "ok" if p.crc_ok else "BAD or missing"))
    for tag, payload in p.packets:
        print("  packet %-28s %d bytes" % (pof.TAG_NAMES.get(tag, "tag %d" % tag), len(payload)))
    for e in p.electrical():
        print("  electrical: lines %d..%d, cells %d..%d, mask %#x, kind %#x" % (
            e.start_col, e.start_col + e.col_count - 1, e.start_row, e.start_row + e.row_count - 1, e.mask, e.flags))


def cmd_topof(args):
    import json
    from . import pof
    from .pcf import Pcf
    dump = json.loads(pathlib.Path(args.image).read_text())
    rows = {int(k, 16): v["data"] for k, v in dump["rows"].items()}
    p = pof.image_to_pof(Pcf.load(args.pcf or dump["pcf"]).regions, rows, args.device or dump.get("part", ""))
    pathlib.Path(args.out).write_bytes(p.to_bytes())
    print("wrote %s: %d electrical packets" % (args.out, len(p.electrical())))


def _load_image(path, pcf):
    """Image {row: [cells]} from a .json read-back, a .pof or a JEDEC file."""
    import json
    from . import jedec, pof
    raw = pathlib.Path(path).read_bytes()
    if raw[:4] == b"POF\0":
        p = pof.Pof.from_bytes(raw)
        if p.crc_ok is False:
            sys.exit("POF checksum is wrong")
        return pof.pof_to_image(p)
    if raw[:1] == b"{":
        return {int(k, 16): v["data"] for k, v in json.loads(raw)["rows"].items()}
    j = jedec.parse(raw.decode("latin1"))
    if j["checksum_ok"] is False:
        sys.exit("JEDEC fuse checksum is wrong")
    return jedec.image_from_fuses(pcf, j["fuses"])


def cmd_program(args):
    from . import programmer
    from .pcf import Pcf
    profile = programmer.PROFILES.get(args.part.upper())
    if profile is None or profile.name != "EPM7160E":
        sys.exit("native programming exists only for the EPM7160E")
    vendor = pathlib.Path(args.vendor)
    args.pcf = str(args.pcf or vendor.parent / "7k" / DEVICE_FILES[profile.name])
    pcf = Pcf.load(args.pcf)
    image = _load_image(args.file, pcf)
    plan = programmer.plan_program(pcf, image)
    zeros = sum(bin(~c & 0x3FF).count("1") for cells in plan.values() for c in cells)
    print("%s: %d of %d lines to write, %d bits to program, %d ms per line at Vpp %d mV (about %d s)" % (
        args.file, len(plan), len(programmer.cell_masks(pcf)), zeros, args.tprog_ms, args.vpp_mv,
        len(plan) * (args.tprog_ms + 30) // 1000 + 1))
    if not args.yes_really_program:
        print("dry run: nothing sent to the unit. Pass --yes-really-program to write the chip. "
              "Programming only clears bits; erase first if the chip is not blank.")
        return
    args.allow_unverified = True
    apu, profile, pcf = _open_for(args)
    try:
        programmer.supply_on(apu, profile=profile)
        programmer.program_epm7160e(apu, plan, tprog_ms=args.tprog_ms, vpp_mv=args.vpp_mv)
    finally:
        programmer.shutdown(apu, profile)
    sig, got = programmer.read_back(apu, profile, pcf)
    bad = programmer.verify(pcf, image, got)
    print("verify: %d cells differ%s" % (len(bad), "" if not bad else "; first: %s" % (bad[:5],)))
    sys.exit(1 if bad else 0)


def cmd_fuses(args):
    from . import daf, fusemap, jedec
    j = jedec.parse(pathlib.Path(args.jed).read_bytes().decode("latin1"))
    from . import maf
    arch = maf.parse(pathlib.Path(args.maf).read_bytes()) if args.maf and pathlib.Path(args.maf).exists() else None
    fmap = fusemap.FuseMap(daf.parse(pathlib.Path(args.daf).read_bytes()), arch)
    print(fmap.report(j["fuses"]))


def cmd_ahdl(args):
    import itertools
    from . import ahdl, ahdl_eval
    text = pathlib.Path(args.file).read_text(encoding="latin1")
    try:
        sub = ahdl.parse(text)[5]
    except ahdl.AhdlError as e:
        sys.exit("%s: %s" % (args.file, e))
    print("SUBDESIGN %s: %d port declarations, %d variable declarations, %d statements" % (
        sub[1], len(sub[2]), len(sub[3]), len(sub[4])))
    try:
        resolver = ahdl_eval.directory_resolver(pathlib.Path(args.file).parent, *ahdl_eval.vendor_library(DEFAULT_VENDOR))
        design = ahdl_eval.Design(text, resolver)
        if design.flops:
            raise ahdl_eval.Unsupported("registers")
        if len(design.inputs) > 8:
            print("combinational; %d inputs is too many to tabulate" % len(design.inputs))
            return
        print(" ".join(design.inputs), "|", " ".join(design.outputs))
        for values in itertools.product((0, 1), repeat=len(design.inputs)):
            out = design.evaluate(**dict(zip(design.inputs, values)))
            print(" ".join(str(v).rjust(len(n)) for v, n in zip(values, design.inputs)), "|",
                  " ".join(str(out[n]).rjust(len(n)) for n in design.outputs))
    except ahdl_eval.Unsupported as e:
        try:
            sim = ahdl_eval.Simulator(text, ahdl_eval.directory_resolver(pathlib.Path(args.file).parent, *ahdl_eval.vendor_library(DEFAULT_VENDOR)))
            d = sim.design
            print("sequential: %d register bits, state machines: %s; can be simulated (apu.ahdl_eval.Simulator)" % (
                len(d.flops), ", ".join(d.machines) or "none"))
        except ahdl_eval.Unsupported as e2:
            print("parses, but cannot be evaluated yet: %s" % e2)


def cmd_synth(args):
    from . import ahdl, ahdl_eval, synth
    text = pathlib.Path(args.file).read_text(encoding="latin1")
    try:
        net = synth.synthesise(text, ahdl_eval.directory_resolver(pathlib.Path(args.file).parent, *ahdl_eval.vendor_library(DEFAULT_VENDOR)))
    except (ahdl.AhdlError, ahdl_eval.Unsupported, synth.TooLarge) as e:
        sys.exit("%s: %s" % (args.file, e))
    print(net.report())
    terms = [len(eq.terms) for eq in net.outputs.values()] + [len((r.get("d") or r["t"]).terms) for r in net.registers.values()]
    print("-- %d outputs, %d register bits, largest equation %d product terms" % (
        len(net.outputs), len(net.registers), max(terms or [0])))


FIT_DEVICES = {"EPM7032": "m7032044.daf", "EPM7064": "m7064084.daf", "EPM7096": "m7096084.daf",
               "EPM7128": "m7128084.daf", "EPM7160E": "e7160084.daf", "EPM7064S": "s7064044.daf"}


def cmd_fit(args):
    from . import ahdl, ahdl_eval, daf, fit, synth
    text = pathlib.Path(args.file).read_text(encoding="latin1")
    name = args.device.upper()
    if name not in FIT_DEVICES:
        sys.exit("unknown device %s (have: %s)" % (args.device, ", ".join(FIT_DEVICES)))
    info = daf.summary(daf.parse((DEFAULT_VENDOR.parent / "7k" / FIT_DEVICES[name]).read_bytes()))
    try:
        net = synth.synthesise(text, ahdl_eval.directory_resolver(pathlib.Path(args.file).parent, *ahdl_eval.vendor_library(DEFAULT_VENDOR)))
        result = fit.fit(net, name, info["macrocells"])
    except (ahdl.AhdlError, ahdl_eval.Unsupported, synth.TooLarge, fit.DoesNotFit) as e:
        sys.exit("%s: %s" % (args.file, e))
    print(result.report())
    try:
        device_file = daf.parse((DEFAULT_VENDOR.parent / "7k" / FIT_DEVICES[name]).read_bytes())
        placement, pins = fit.assign_pins(result, net, device_file.tables["pin_mc"])
        print("pins: " + ", ".join("%s=%d" % (n, p) for n, p in sorted(pins.items(), key=lambda kv: kv[1])))
    except fit.DoesNotFit as e:
        print("pin assignment failed: %s" % e)
    print("-- resource fit only: signals are not routed and no programming file is produced")


def cmd_compile(args):
    """Design -> synthesis -> fit -> programming file (EPM7032, EPM7064S 44-pin, EPM7128S 84-pin)."""
    from . import acf, ahdl, ahdl_eval, daf, fit, fusemap, gdf, generate, jedec, maf, parts, pof, project, synth
    path = pathlib.Path(args.file)
    text = gdf.to_ahdl(path.read_bytes(), path.stem) if path.suffix.lower() == ".gdf" else path.read_text(encoding="latin1")
    resolver = ahdl_eval.directory_resolver(path.parent, *ahdl_eval.vendor_library(DEFAULT_VENDOR))
    sections = acf.parse(path.with_suffix(".acf").read_text(encoding="latin1")) if path.with_suffix(".acf").exists() else []
    try:
        net = synth.synthesise(text, resolver)
        device = args.device or acf.device(sections, path.stem)
        if not device or device.upper() == "AUTO":
            device = "EPM7032LC44"                       # the only device programming data is generated for
        part = parts.lookup(parts.load(DEFAULT_VENDOR / "partinfo.crf"), device)
        if part is None or part.daf not in ("m7032044", "s7064044", "s7128084"):
            sys.exit("%s: programming data can only be generated for the EPM7032, EPM7064S (44-pin) and EPM7128S (84-pin) so far" % device)
        k = DEFAULT_VENDOR.parent / "7k"
        if part.daf == "s7064044":
            from . import generate7064s
            fm = fusemap.FuseMap(daf.parse((k / "s7064044.daf").read_bytes()), maf.parse((k / "m7064s.maf").read_bytes()))
            fuses, fitted = generate7064s.compile_netlist(net, part, k, acf.pins(sections, path.stem), fm)
        elif part.daf == "s7128084":
            from . import generate7064s, s7k
            tables = s7k.Tables.load("7128s")
            fm = fusemap.FuseMap(daf.parse((k / "s7128084.daf").read_bytes()), maf.parse((k / "m7128s.maf").read_bytes()))
            fuses, fitted = generate7064s.compile_netlist(net, part, k, acf.pins(sections, path.stem), fm, tables)
        else:
            fitted = project.fit_part(net, part, k, acf.pins(sections, path.stem))
            fm = fusemap.FuseMap(daf.parse((k / (part.daf + ".daf")).read_bytes()), maf.parse((k / (part.maf + ".maf")).read_bytes()))
            fuses = generate.generate(net, fitted.result, fitted.placement, fitted.pins, fm)
    except (ahdl.AhdlError, ahdl_eval.Unsupported, synth.TooLarge, fit.DoesNotFit, generate.CannotGenerate) as e:
        sys.exit("%s: %s" % (args.file, e))
    out = pathlib.Path(args.output or path.with_suffix(".pof"))
    out.write_bytes(pof.fuses_to_logical_pof(fuses, part.name.split("-")[0], "apu-revive").to_bytes())
    print("%s: %s, %d of %d macrocells -> %s" % (path.name, part.name.split("-")[0],
                                                 sum(c.macrocells for c in fitted.result.cells), fm.macrocells, out))
    print("pins: " + ", ".join("%s=%d" % kv for kv in sorted(fitted.pins.items(), key=lambda kv: kv[1])))
    if getattr(args, "svf", None):
        if part.daf == "s7064044":
            from . import svf7064s
            text = svf7064s.write(fuses, "program")
        elif part.daf == "s7128084":
            from . import svf_template
            text = svf_template.write("7128s", fuses, "program")
        else:
            sys.exit("--svf: JTAG programming files are written for the EPM7064S and EPM7128S only")
        pathlib.Path(args.svf).write_text(text)
        print("SVF (erase, program, verify; JTAG stays enabled) -> %s" % args.svf)
    if args.jed:
        pathlib.Path(args.jed).write_bytes(jedec.write(fuses, "Compiled by apu-revive from %s" % path.name,
                                                       device=part.name.split("-")[0]).encode("latin1"))
        print("JEDEC -> %s" % args.jed)


def _load_fuses(path):
    from . import jedec, pof
    data = pathlib.Path(path).read_bytes()
    if path.lower().endswith(".jed"):
        return jedec.parse(data.decode("latin1"))["fuses"]
    return pof.logical_fuses(pof.Pof.from_bytes(data))


def cmd_jtag(args):
    """A MAX 7000S chip on a USB-Blaster: read-only actions run directly; program needs --yes."""
    from . import jedec, jtag, pof
    if args.action in ("read", "verify", "program") and not args.file:
        sys.exit("jtag %s: give a file" % args.action)
    if args.action == "program" and not args.yes:
        sys.exit("jtag program erases and reprograms the chip; run again with --yes to do it")
    device = args.device.upper()
    if device not in jtag.DEVICES:
        sys.exit("jtag: devices are %s" % ", ".join(sorted(jtag.DEVICES)))
    want = jtag.DEVICES[device][0]
    fuses = _load_fuses(args.file) if args.action in ("verify", "program") else None
    try:
        with jtag.Openocd(args.interface, idcode=want) as ocd:
            code = jtag.idcode(ocd)
            print("IDCODE %08X%s" % (code, " (%s)" % device if code == want else " (not an %s)" % device))
            if code != want:
                sys.exit(1)
            if args.action == "read":
                fuses = jtag.read_device(ocd, device)
                out = pathlib.Path(args.file)
                if out.suffix.lower() == ".jed":
                    out.write_bytes(jedec.write(fuses, "Read from an %s by apu-revive" % device, device=device).encode("latin1"))
                else:
                    out.write_bytes(pof.fuses_to_logical_pof(fuses, device, "apu-revive").to_bytes())
                print("read %d fuses (%d programmed) -> %s" % (len(fuses), fuses.count(0), out))
            elif args.action == "verify":
                bad = jtag.verify_device(ocd, device, fuses)
                print("verify: %s" % ("matches" if not bad else "%d reads differ" % len(bad)))
                sys.exit(1 if bad else 0)
            elif args.action == "program":
                print("erasing, programming and verifying ...")
                jtag.program_device(ocd, device, fuses)
                bad = jtag.verify_device(ocd, device, fuses)
                print("programmed; independent read-back %s" % ("matches" if not bad else "differs in %d reads" % len(bad)))
                sys.exit(1 if bad else 0)
    except jtag.JtagError as e:
        sys.exit("jtag: %s" % e)


def cmd_sim(args):
    import re
    from . import ahdl, ahdl_eval, gdf, tbl
    path = pathlib.Path(args.design)
    resolver = ahdl_eval.directory_resolver(path.parent, *ahdl_eval.vendor_library(DEFAULT_VENDOR))
    try:
        text = gdf.to_ahdl(path.read_bytes(), path.stem) if path.suffix.lower() == ".gdf" else \
            path.read_text(encoding="latin1")
        sim = ahdl_eval.Simulator(text, resolver)
    except (ahdl.AhdlError, ahdl_eval.Unsupported, gdf.GdfError) as e:
        sys.exit("%s: %s" % (path, e))
    table = tbl.parse(pathlib.Path(args.tbl).read_text(encoding="latin1"))
    rename = lambda n: re.sub(r"\|[\w~]+:(\d+)\|", r"u\1/", n).lstrip("|")
    checks, mismatches = tbl.compare(table, sim, rename, table.outputs + table.buried)
    for time, name, want, got in mismatches[:40]:
        print("%8.1f ns  %-30s expected %s, got %s" % (time, name, want, got))
    print("%d values compared, %d differ" % (checks, len(mismatches)))
    sys.exit(1 if mismatches else 0)


def cmd_gui(args):
    from . import gui
    server = gui.serve(args.project, args.vendor, args.port, open_browser=not args.no_browser)
    print("apu-revive is running at %s  (Ctrl-C to stop)" % server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def _pcf_for(args, device=None):
    """Device file: --pcf, or looked up from the device name in the vendor part table."""
    from .pcf import Pcf
    if getattr(args, "pcf", None):
        return Pcf.load(args.pcf)
    name = (getattr(args, "device", None) or device or "").strip()
    table = DEFAULT_VENDOR / "partinfo.crf"
    for line in table.read_text(errors="replace").splitlines() if table.exists() else []:
        cols = line.split()
        if len(cols) > 2 and cols[0].upper() == name.upper():
            hits = sorted(DEFAULT_VENDOR.parent.glob("*/%s.pcf" % cols[1].lower()))
            if hits:
                return Pcf.load(hits[0])
    sys.exit("cannot find a device file for %r; pass --pcf" % name)


def cmd_pof2jed(args):
    from . import jedec, pof
    p = pof.Pof.from_bytes(pathlib.Path(args.pof).read_bytes())
    if p.crc_ok is False:
        sys.exit("POF checksum is wrong")
    fuses = pof.logical_fuses(p)                  # what MAX+PLUS II writes for MAX 7000 parts
    if fuses is None:
        pcf = _pcf_for(args, p.device)
        fuses = jedec.fuses_from_image(pcf, pof.pof_to_image(p))
    text = jedec.write(fuses, "Converted from %s" % pathlib.Path(args.pof).name, device=p.device,
                       security=p.flag(pof.TAG_SECURITY))
    pathlib.Path(args.jed).write_bytes(text.encode("latin1"))
    print("%d fuses, %d set -> %s" % (len(fuses), sum(fuses), args.jed))


def cmd_jed2pof(args):
    from . import jedec, pof
    j = jedec.parse(pathlib.Path(args.jed).read_bytes().decode("latin1"))
    if j["checksum_ok"] is False:
        sys.exit("JEDEC fuse checksum is wrong")
    device = args.device or j["device"] or ""
    pcf = _pcf_for(args, device)
    p = pof.image_to_pof(pcf.regions, jedec.image_from_fuses(pcf, j["fuses"]), device)
    if j["security"] is not None:
        p.add_flag(pof.TAG_SECURITY, j["security"])
    pathlib.Path(args.pof).write_bytes(p.to_bytes())
    print("%d fuses -> %s (%d electrical packets)" % (len(j["fuses"]), args.pof, len(p.electrical())))


def main():
    p = argparse.ArgumentParser(prog="apu")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="dump the APU's USB descriptors").set_defaults(fn=cmd_info)
    i = sub.add_parser("init", help="load 8051 firmware and configure the FPGA")
    i.add_argument("--vendor", default=str(DEFAULT_VENDOR), help="dir containing pgdmpuz.drv/.rbf")
    i.set_defaults(fn=cmd_init)
    a = sub.add_parser("adapter", help="read the fitted adapter's ID (native, no Altera code)")
    a.add_argument("--vendor", default=str(DEFAULT_VENDOR), help="dir containing pgdmpuz.drv/.rbf")
    a.set_defaults(fn=cmd_adapter)
    for name, fn, text in (("id", cmd_id, "silicon ID (native, no Altera code)"),
                           ("read", cmd_read, "full read-back to a JSON image (native)")):
        c = sub.add_parser(name, help=text)
        if name == "read":
            c.add_argument("out", help="JSON file to write")
            c.add_argument("--vmarg", type=int, default=2000, help="margin voltage in mV (default 2000)")
        c.add_argument("--part", default="EPM7160E", help="device type: EPM7160E (default), EPM7160EQC160, EPM7192EQC160, EPM7064 or EPM7032")
        c.add_argument("--pcf", help="device file (default: the 84-pin one for the part)")
        c.add_argument("--allow-unverified", action="store_true",
                       help="run a device whose native sequences have only been checked in simulation")
        c.add_argument("--vendor", default=str(DEFAULT_VENDOR), help="dir containing pgdmpuz.drv/.rbf")
        c.set_defaults(fn=fn)
    g = sub.add_parser("program", help="program an EPM7160E from a .pof, JEDEC or image file (native; "
                                       "sequences checked in simulation only)")
    g.add_argument("file")
    g.add_argument("--vpp-mv", type=int, required=True, help="programming voltage in mV (no default)")
    g.add_argument("--tprog-ms", type=int, required=True, help="pulse time per line in ms (no default)")
    g.add_argument("--part", default="EPM7160E")
    g.add_argument("--pcf")
    g.add_argument("--yes-really-program", action="store_true", help="actually write the chip")
    g.add_argument("--vendor", default=str(DEFAULT_VENDOR))
    g.set_defaults(fn=cmd_program)
    u = sub.add_parser("fuses", help="listing of a JEDEC file: product terms, their roles and option fuses "
                                     "(signal names for the EPM7032 only)")
    u.add_argument("jed")
    u.add_argument("--daf", default=str(DEFAULT_VENDOR.parent / "7k/m7032044.daf"), help="device assembler file")
    u.add_argument("--maf", default=str(DEFAULT_VENDOR.parent / "7k/m7032.maf"), help="device architecture file")
    u.set_defaults(fn=cmd_fuses)
    h = sub.add_parser("ahdl", help="parse an AHDL (.tdf) design; print a truth table if it is purely "
                                    "combinational and small")
    h.add_argument("file")
    h.set_defaults(fn=cmd_ahdl)
    y = sub.add_parser("synth", help="reduce a small flat AHDL design to sum-of-products equations")
    y.add_argument("file")
    y.set_defaults(fn=cmd_synth)
    c = sub.add_parser("compile", help="compile a design (.tdf or .gdf) to a programming file (.pof); "
                                       "device and pins from <design>.acf if present (EPM7032, EPM7064S, EPM7128S)")
    c.add_argument("file")
    c.add_argument("-o", "--output", help="POF to write (default: <design>.pof)")
    c.add_argument("--jed", help="also write a JEDEC file")
    c.add_argument("--svf", help="also write a JTAG programming file (SVF; EPM7064S)")
    c.add_argument("--device", help="override the .acf device")
    c.set_defaults(fn=cmd_compile)
    j = sub.add_parser("jtag", help="EPM7064S over JTAG with a USB-Blaster (openocd): id, read, verify, program")
    j.add_argument("action", choices=("id", "read", "verify", "program"))
    j.add_argument("file", nargs="?", help="read: POF to write (or .jed); verify / program: POF or JEDEC to use")
    j.add_argument("--yes", action="store_true", help="program: really erase and program the chip")
    j.add_argument("--device", default="EPM7064S", help="EPM7064S (default) or EPM7128S")
    j.add_argument("--interface", default="interface/altera-usb-blaster.cfg", help="openocd interface config")
    j.set_defaults(fn=cmd_jtag)
    z = sub.add_parser("fit", help="check that an AHDL design fits a device's macrocells and blocks "
                                   "(no routing, no programming file)")
    z.add_argument("file")
    z.add_argument("--device", default="EPM7032")
    z.set_defaults(fn=cmd_fit)
    w = sub.add_parser("sim", help="simulate an AHDL or schematic design against a MAX+PLUS II simulator "
                                   "table (.tbl) and report differences (functional, no delays)")
    w.add_argument("design")
    w.add_argument("tbl")
    w.set_defaults(fn=cmd_sim)
    g2 = sub.add_parser("gui", help="open the application in your browser (local only)")
    g2.add_argument("project", nargs="?", default=".", help="directory holding the design files")
    g2.add_argument("--port", type=int, default=0)
    g2.add_argument("--no-browser", action="store_true")
    g2.add_argument("--vendor", default=str(DEFAULT_VENDOR))
    g2.set_defaults(fn=cmd_gui)
    f = sub.add_parser("pof", help="describe a .pof programming file")
    f.add_argument("file")
    f.set_defaults(fn=cmd_pof)
    t = sub.add_parser("topof", help="write a read-back image (JSON) as a .pof")
    t.add_argument("image")
    t.add_argument("out")
    t.add_argument("--pcf", help="device file (default: the one named in the image)")
    t.add_argument("--device", help="device name to record (default: the image's part)")
    t.set_defaults(fn=cmd_topof)
    for name, fn, a, b, text in (("pof2jed", cmd_pof2jed, "pof", "jed", "convert a .pof to a JEDEC file"),
                                 ("jed2pof", cmd_jed2pof, "jed", "pof", "convert a JEDEC file to a .pof")):
        c = sub.add_parser(name, help=text)
        c.add_argument(a)
        c.add_argument(b)
        c.add_argument("--pcf", help="device file (default: found from the device name)")
        c.add_argument("--device", help="device name (default: the one recorded in the input)")
        c.set_defaults(fn=fn)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
