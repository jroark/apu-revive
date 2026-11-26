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
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
