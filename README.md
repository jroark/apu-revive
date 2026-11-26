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
| 1 | Native programmer: transport, adapter ID, voltages, ID read, read-back, erase, program, verify | EPM7160E on the PLMJ7000-84: adapter ID, silicon ID and full read-back native and verified on hardware; erase and row write native but checked in simulation only. EPM7064 (84-pin) and EPM7032 (44-pin): ID and read-back native, checked in simulation only. Everything else hosted |
| 2 | Native file handling: `.pcf` device data, JEDEC, POF | `.pcf` parsed; JEDEC read and written; POF read and written; POF ↔ JEDEC ↔ read-back image conversion. POF layout comes from the vendor converter's code and is not yet tested against a real POF |
| 3 | Fuse-level model of the MAX 7000 (what each fuse does) | in progress: every `.daf` and `.maf` device file parses; product-term array, option fuses and term steering located for all MAX 7000 devices; signal routing decoded and confirmed on a real EPM7032, so `python -m apu fuses x.jed` prints its product terms with signal names. Not decoded: true/complement column order, most option fuses, routing on larger devices (their upper rows may not read back completely) |
| 4 | Logic compiler and fitter producing programming files | started: designs reduce to minimised sum-of-products equations (`python -m apu synth x.tdf`), and a resource fit packs them into macrocells and logic blocks under the product-term, expander and block-input limits (`python -m apu fit x.tdf --device EPM7032`). No signal routing, pin assignment or fuse generation, so no programming file |
| 5 | Design entry, simulation, GUI | started: AHDL (`.tdf`) and schematic (`.gdf`) designs are read — schematics are converted to AHDL — including the vendor's library macrofunctions from your own copy (220 of its 306 schematics); a functional simulator handles combinational logic, D/T flip-flops, state machines, hierarchy, tri-state and bidirectional pins. 33 of the 40 tutorial files simulate. No buses, rotated symbols, latches or JK flip-flops in schematics yet; a first application window (`apu gui`: files, source, compile, simulate with waveforms, read-only programmer). No timing simulation or waveform editing |

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

What runs natively (no Altera code executing; the unit's firmware and FPGA image are still loaded
from your copy of MAX+PLUS II):

| Capability | State |
|---|---|
| Firmware download, FPGA configuration, port I/O, adapter ID, rail DACs and ADC | works on hardware |
| EPM7160E on PLMJ7000-84 and EPM7160EQC160 on PLMQ7128/7160-160: silicon ID, full read-back with margin check | works on hardware (both read back identically to the vendor module); rows 128 and up may not read back completely (see below) |
| EPM7160E: bulk erase, programming from POF / JEDEC / image with verify | written; matches the vendor module in simulation; never run on a chip |
| EPM7064 (84-pin) and EPM7032 (44-pin): silicon ID and read-back | written; match the vendor module in simulation; never run on a chip |
| JEDEC and POF files, conversion between them and read-back images | works; POF layout is from the vendor converter's code, untested against a real POF |
| Fuse listing of an EPM7032 JEDEC file with signal names | works on one real chip's contents; register and option-fuse meanings are hypotheses |
| AHDL and schematic designs: parse, simulate, reduce to equations, fit to a device with pin assignment | 33 of the 40 tutorial files simulate, 28 also synthesise and fit; `chiptrip` matches the original MAX+PLUS II simulator's table in all 80 values (`apu sim`); produces no programming file |

Through the emulator-hosted vendor module (`tools/`): silicon ID and read-back on EPM7064-84,
EPM7160E-84 and EPM7032-44; bulk erase, signature-row restore and full programming on one
EPM7160E.

Known problems and open questions:

- On the EPM7064 and EPM7160E the rows numbered 128 and up, which hold the signal-routing fuses,
  read back with very few programmed bits, and the routing they imply is inconsistent with the
  model confirmed on the EPM7032. The read-back images of those chips, and a chip programmed
  from one, may be incomplete in those rows. Settling it needs a write-and-read-back test.
- The unit of the chips' own programming-time codes is unresolved, so the native program
  command takes voltage and pulse time from you, never from the chip.
- Simulation has been compared with the original MAX+PLUS II on one design (the tutorial's
  `chiptrip`, using its shipped `.tbl`). The fuse model has not: no `.pof` from the original
  software is available. One small design compiled by it, with its `.pof`, would confirm or
  refute most of the fuse model.
- EPM7096, 68-pin parts and MAX 5000 are not working or untested.

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -e .                    # the `apu` command; needs libusb (brew install libusb)
.venv/bin/pip install -r requirements.txt     # also the emulator-based tools in tools/
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
.venv/bin/python -m apu id                                 # EPM7160E silicon ID, fully native
.venv/bin/python -m apu read out.json                      # EPM7160E full read-back, fully native
.venv/bin/python -m apu program x.pof --vpp-mv 12250 --tprog-ms 60   # dry run; see below
.venv/bin/python -m apu topof out.json out.pof              # read-back image -> POF
.venv/bin/python -m apu pof2jed out.pof out.jed             # POF -> JEDEC (and jed2pof)
.venv/bin/python -m apu pof out.pof                         # describe a POF
.venv/bin/python -m apu sim design.gdf design.tbl          # simulate against a MAX+PLUS II .tbl
.venv/bin/python -m apu gui path/to/project                # the application, in your browser
.venv/bin/python tools/read_siid.py EPM7064LC84-15 --real  # silicon ID
.venv/bin/python tools/read_bulk.py EPM7064LC84-15 out.json --real
.venv/bin/python tools/read_bulk.py EPM7064LC84-15 lo.json --real --volts vmarg=1000
.venv/bin/python tools/dump_to_jed.py out.json out.jed
.venv/bin/python tools/erase_dryrun.py EPM7160ELC84-15     # shows the erase, sends none
```

`apu program` prints its plan and sends nothing unless `--yes-really-program` is given; its
sequences match the vendor module in simulation but have not yet been run on a chip.

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
