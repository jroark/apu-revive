# apu-revive

Use an Altera Programming Unit (PL-APU, the USB successor to the MPU) from macOS, without
Windows, to identify, read, erase and repair MAX 7000 devices.

This is a reverse-engineering project and a work in progress. It has been exercised on one
APU with a handful of used chips. Read the status section before trusting it with a part
you care about.

## Goal

A clean, native macOS replacement for MAX+PLUS II for these parts: no Altera code at run time.
That is a long road, and today most device-level work still runs Altera's own code in an
emulator. The emulator is being kept as a reference that every native piece is checked against.

| Stage | Scope | State |
|---|---|---|
| 1 | Native programmer: transport, adapter ID, voltages, ID read, read-back, erase, program, verify | transport and adapter ID native; the rest hosted |
| 2 | Native file handling: `.pcf` device data, JEDEC, POF | `.pcf` parsed, JEDEC written; POF not started |
| 3 | Fuse-level model of the MAX 7000 (what each fuse does) | not started |
| 4 | Logic compiler and fitter producing programming files | not started |
| 5 | Design entry, simulation, GUI | not started |

## How it works

The APU contains a Cypress EZ-USB microcontroller and a FLEX 10K FPGA. Both are loaded by the
host on every power-up, and all device knowledge lives in Altera's MAX+PLUS II software. This
project therefore has two layers:

- **Native code** (`apu/`): USB transport (firmware download, FPGA configuration, port
  read/write framing) and a parser for MAX+PLUS II's programmer control files (`.pcf`).
- **Hosted Altera code** (`tools/harness.py`, `tools/host.py`): the unmodified hardware module
  `pgdmpuz.drv`, and parts of `pgm.dll`, run inside a user-mode x86 emulator (Unicorn). Their
  Windows imports are stubbed and their USB calls are bridged to libusb, so the vendor's own
  algorithms drive the chip.

No Altera software is included. You need your own copy of MAX+PLUS II 10.2 Baseline; the
tools read the firmware, FPGA image and device files from it at run time.

## Status

Confirmed on hardware:

| Capability | State |
|---|---|
| Firmware download, FPGA configuration, port I/O | works |
| Adapter ID and adapter/device check | works |
| Silicon-ID read (direct, and via `pgm.dll`'s own check) | EPM7064-84, EPM7160E-84, EPM7032-44 |
| Full read-back, with margin check | same parts; images are not verified against a reference |
| Read-back to JEDEC | fuse map decoded from the `.pcf`; polarity unverified |
| Bulk erase | EPM7160E (standard path) |
| Signature-row erase and rewrite | EPM7160E, one chip revived |
| Programming a full design | not implemented |
| EPM7096, 68-pin parts, MAX 5000 | not working / untested |

Known limitations: everything device-level depends on the emulated vendor code; the top bit of
EPM7160E cells reads unreliably in some sequences; erase and programming have been run on a
single chip.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # needs libusb installed (brew install libusb)
```

Unpack the MAX+PLUS II 10.2 Baseline installer (it is an InstallShield archive; `unshield`
extracts it) so that these exist:

```
vendor/maxplus2-10.2/MAX+plus_II_BASELINE/pgdmpuz.drv   pgdmpuz.rbf   pgm.dll   partinfo.crf
vendor/maxplus2-10.2/7k/*.pcf
```

The APU needs both USB and its external 9 V supply (centre positive on the unit this was
developed with; check yours).

## Usage

Run from the repository root. Reading is non-destructive.

```sh
.venv/bin/python -m apu info                               # USB descriptors
.venv/bin/python -m apu adapter                            # adapter ID, fully native
.venv/bin/python tools/read_siid.py EPM7064LC84-15 --real  # silicon ID
.venv/bin/python tools/read_bulk.py EPM7064LC84-15 out.json --real
.venv/bin/python tools/read_bulk.py EPM7064LC84-15 lo.json --real --volts vmarg=1000
.venv/bin/python tools/dump_to_jed.py out.json out.jed
.venv/bin/python tools/erase_dryrun.py EPM7160ELC84-15     # shows the erase, sends none
```

Part names are looked up in `partinfo.crf`. Without `--real` the tools run against a
simulated device. Tools that change a chip (`force_erase.py`, `ms_row.py`) require an explicit
confirmation flag and are experiments, not a supported workflow.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -t .
```

## Notes

`re/protocol.md` is the running lab notebook: the USB protocol, the module and programmer
internals as recovered, every hardware result, and the mistakes made along the way. Later
entries correct earlier ones.

## Legal

This project is not affiliated with Altera or Intel. It contains no vendor code or data. No
licence has been chosen yet, so all rights are reserved by default.
