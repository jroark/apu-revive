# Altera APU (PL-APU) USB protocol — recovered notes

Source: static disassembly of `pgdmpuz.drv` (MAX+PLUS II 10.2, sha256 `df4a6e79…`),
mainly its `ezusbclient.c` code at `0x100354e0–0x10036265` and init routine `0x100071e0`.
Hardware-confirmed items are listed under "Confirmed on the unit". Items marked **(inferred)** are not read
directly from the code.

## Device
- USB `0799:7651`, Windows device name `\\.\APU_USB` (`-NNN` suffix if `PROGRAMMER_USB_CHANNEL` is set).
- `Apu_usb.sys` is used through the stock Cypress `ezusb.sys` IOCTL set:
  `0x222014` vendor request, `0x222051` bulk write, `0x22204E` bulk read, `0x22206D` Anchor download.
- The 8051 firmware (39 records, 0x0000–0x03E3) is embedded in `pgdmpuz.drv` at VA `0x10038010`
  as 22-byte records `{u8 len; u8 pad; u16 addr; u8 type; u8 data[16]; u8 pad}`.
  It contains no USB descriptors, so the VID/PID come from the 24LC64 EEPROM and the endpoints
  are the EZ-USB defaults **(inferred)**.
- External supply is 9 V (error strings: "9 volt cable is not connected"). Polarity/current unknown.

## Start-up sequence
1. Vendor request `0xA0`, wValue `0x7F92`, data `01` (hold 8051 in reset).
2. Vendor request `0xA0`, wValue = record address, data = record bytes, for each record.
3. Vendor request `0xA0`, wValue `0x7F92`, data `00` (run).
4. 9 V presence check (`fcn.10006b60`): `ctl(0x94) & 0x40` must be set.
5. Configure the FLEX 10K100E from `pgdmpuz.rbf` (below).

## Pipes
Windows pipe index → use. Endpoint addresses assume EZ-USB default descriptor order **(inferred)**.

| index | endpoint | use |
|---|---|---|
| 2 | EP2 OUT | port command stream; also raw FPGA bitstream |
| 1 | EP2 IN | replies to port reads (one byte each) |
| 4 | EP4 OUT | control: 2 bytes `00 <cmd>` |
| 3 | EP4 IN | control reply: 1 status byte |
| 5 | (EP6 IN) | allowed by an assert; no caller found yet |

## FPGA configuration
- `ctl(0x41)`; require `status & 0x28 == 0`.
- Data = 11 × `FF` + rbf file (166965 + 11 = 166976 = 2609 × 64). Within each 64-byte block,
  byte `i` is swapped with byte `(i >> 3) | ((i << 3) & 0x3F)`.
- For each chunk of up to `0xFF00` bytes: `ctl(0x51)`, require `status & 0x28 == 0x08`, then bulk-write the chunk to pipe 2.
- `ctl(0x94)`; success when `status & 0x28 == 0x28`.
- Bits `0x08`/`0x20` are presumably nSTATUS / CONF_DONE **(inferred)**.

## Port command stream
The upper layers (`pgd_writeport` / `pgd_readport` / `pgd_build_command`, the same model as the
ISA-card MPU) queue 32-bit words `op<<24 | addr<<16 | data`, encoded to bytes as:

| op | wire bytes | meaning |
|---|---|---|
| 3 | `0x80+addr` | read port; one reply byte on pipe 1 |
| 4 | `addr`, `data` | write port |
| 5 | `0x40+addr`, `data` | write, variant (semantics not yet known) |
| 8 | `0xC0+addr`, `data` | write, variant (semantics not yet known) |
| 2, 6, 7 | `40 FF` | no-op |
| 1 | — | rejected ("CMD 01 not supported") |

Framing (from the 8051 firmware, `re/fw8051.asm`):
- A full 64-byte OUT packet is clocked straight into the FPGA.
- A short OUT packet is a read request: all but its last two bytes are clocked into the FPGA,
  and the last two are a little-endian count of reply bytes, which the firmware then collects
  from the FPGA and returns on EP2 IN (64-byte packets, then a remainder).
- So write-only streams are padded with `40 FF` pairs to a packet boundary, and streams with
  reads end `40 FF <count lo> <count hi>` in a short packet. The Windows client's separate
  "delayed read" bookkeeping is not implemented.

## Control pipe = EZ-USB port C
The control command byte is written to port C (bit 0 is not a pin: it selects the EP2 OUT
handler, 1 = FPGA bitstream, 0 = port commands) and the reply is the port C pin state.
Outputs are PC2, PC4, PC7 (`OEC = 0x94`); PC0/PC1 are the UART.

| bit | dir | meaning |
|---|---|---|
| 0x10 | out | low in `0x41`, high in `0x51`/`0x94`: nCONFIG **(inferred)** |
| 0x04, 0x80 | out | high only in `0x94`; purpose unknown |
| 0x08 | in | nSTATUS **(inferred)** |
| 0x20 | in | CONF_DONE **(inferred)** |
| 0x40 | in | external 9 V present |

## Not yet recovered
- What the FPGA does with each wire opcode (the firmware only streams bytes to it).
- Port map of the FPGA (DACs, pin drivers, adapter ID, LED, START button) — `pgdzmain.c`.
- Device algorithms: `pgd_*` exports here, driven by `pgm.dll` and the per-family data files.

## Confirmed on the unit (2026-11-25, USB only, no 9 V)
- Enumerates as `0799:7651`, bcdDevice 0.01, with the EZ-USB default descriptors: interface 0
  alt 1 has 0x81 (int), 0x82/0x02, 0x84/0x04, 0x86/0x06 (bulk, 64) plus iso endpoints. The pipe
  table above is therefore correct; alt setting 1 must be selected.
- Firmware download via `0xA0` works from macOS/libusb; the device does not re-enumerate.
- `ctl(0x94)` returns `0x96` with no 9 V attached (bit `0x40` clear, as the 9 V check expects).

## Emulator harness (`tools/harness.py`)
Runs the unmodified `pgdmpuz.drv` under Unicorn with its Windows imports stubbed. With the
simulated device (`FakeApu`) it reproduces the start-up sequence above byte for byte
(firmware load, `ctl` 0x94/0x41/0x51×3/0x94, bitstream chunks of 65280/65280/36416), which
independently confirms the static analysis. `UsbApu` bridges the same calls to the real unit.

`pgd_setup(cmd, p1, p2)` is a 37-way dispatcher. Known so far:
- `12, 'D', out_struct` — select the APU/USB back end (must come first).
- `1, port_base, rbf_path` — open device, load firmware, configure FPGA, then initialise ports
  (skipped when `port_base` is 0). `tools/trace_init.py` uses `0x280`.
- `8, n, 0` — set LED state `n` (0–2).

`re/traces/init_sim.txt` is the simulated port-level trace of that initialisation: 59 OUT
transfers. Port `0x01` is a bit-banged serial bus to the TLC5620 DACs (bit 1 clock, bit 0 data,
`0x88`/`0x84` load strobes **(inferred from the waveform)**); ports `0x03`/`0x04` are read back
repeatedly. Read values come from the simulation (all zero), so branches that depend on real
hardware state are not represented.

## Confirmed on the unit (2026-11-25, USB + 9 V 2.5 A centre-positive)
- `ctl(0x94)` returns `0xDF` before configuration: bit `0x40` (9 V) set, `0x08` set, `0x20` clear.
- FPGA configuration with the sequence above succeeds in about 0.8 s: `0x41`→`0x43`,
  `0x51`→`0x5B` before each of the three chunks, final `0x94`→`0xFF`.
- Port reads work with the short-packet framing: ports `0x03`/`0x04` read `0x00`, port `0x01`
  reads `0x03` immediately after configuration (no port writes issued yet).
- macOS drops back to alt setting 0 when the process exits, and SET_INTERFACE does not reset the
  EZ-USB's data toggles. A new session must select alt 1 and send CLEAR_FEATURE(ENDPOINT_HALT)
  to each endpoint, otherwise OUT packets are silently discarded. `Apu.open()` does this.

## Altera's init run on the real unit (2026-11-25, empty PLMJ7128-84 adapter)
- `tools/harness.py` with the `UsbApu` back end: `pgd_setup(1, 0x280, rbf)` returns 1 and sets the
  port base, i.e. the unmodified Windows module accepts the hardware through the Mac bridge.
  `p1` is the legacy ISA base; valid values are 0x200–0x2F0 in steps of 0x10, default 0x280.
- Full transfer log: `re/traces/init_real.txt` (40 OUT transfers). It includes a 512-byte
  read-back of a walking-ones pattern (`01 00 02 00 04 00 …`), evidently a self-test.
- LED (`pgd_setup(8, n, 0)`): `W5 port 0 = 0xF7` then `W port 0 = 0x80` (n=1), `0x40` (n=2),
  `0x00` (n=0). So port 0 bits 7/6 are the LED, and opcode 5 looks like a mask or direction
  write **(inferred)**.
- `pgd_setup(3, 0, 0)` is the shutdown path (370 port operations).

## How `pgm.dll` drives the module (static, from `pgm_read_in_silicon_id` at 0x1003a3fd)
- `pgm.dll` calls the module through function pointers at `0x1006fc90`–`0x1006fcd4`. Identified
  by argument shape: `0x1006fcc8` = `pgd_setup` (221 call sites, command numbers 3–37),
  `0x1006fc9c` = `pgd_program_setup` (6 args, voltages), `0x1006fcac` = the 2-argument ID read
  (taken to be `pgd_read_silicon_id`). The other slots are not mapped; an earlier guess that
  the table is alphabetical was wrong.
- `pgd_program_setup(mode, x, v1, v2, v3, v4)`: voltages are in millivolts; the module rejects
  `v1`, `v4` above 10000 and `v2`, `v3` above 20000.
- `pgd_setup` commands seen: `12, 'D', &struct` init; `6, family, variant` select device
  (from the device record's `+0x44c` and `+0x478`); `8, n, 0` LED; `13, mV, 0` with 2500/3300/5000;
  `19, 0, 0`; `7, …`.
- Silicon-ID read:
  1. `pgd_program_setup(1, 0, w[0x0a], w[0x0c], w[0x0e], w[0x10])` — four 16-bit values from
     the device record at `[0x1006f588]`; the DLL's strings name them `vcc_siid`, `vpd_siid`,
     `vpg_siid`, `vmarg_siid` (order not yet confirmed).
  2. For each ID region (0x1c-byte records: `+0` selector, `+4` start, `+8` count, `+0x10` out,
     `+0x14` mask): `value = pgd_read_silicon_id(selector, start + i) & mask[i]`.
  3. The ID is ASCII, one character per read; the code special-cases `"ALTERA91"`.
- So the device layer is table-driven and the per-call semantics are simple; the tables come
  from the per-family data files, not yet parsed.

## Device data files (`.pcf`) — first observations
- Header: magic `PCF\0`; `u16` at 0x0c and 0x0e look like the family and variant passed to
  `pgd_setup(6, …)` **(inferred: the EPM7128 file gives (0x0b, 2), a pair the module's
  device-selection code special-cases)**. Not yet confirmed that 0x0e is the `+0x478` field.
- Values: m7064084 (0x0b, 4), m7096084 (0xb3, 2), m7128084 (0x0b, 2), e7128084 and e7160084
  (0x0a, 3), m5192 (0x78, 4).
- In the emulator `pgd_setup(6, …)` accepts all of these except the EPM5192 pair, and issues no
  port I/O. EPM7128E and EPM7160E map to the same internal type (0x134).
- Altera's init produces an identical transfer trace with the PLMJ7128-84 and PLMJ7000-84
  adapters fitted (empty), so initialisation does not identify the adapter.

## `.pcf` layout (partly decoded)
- `u16 @0x0c` = device family code (argument to `pgd_setup(6, family, variant)`).
- `u16 @0x0e` = number of variants (not the variant index, as first guessed). The variant index
  (`+0x478` in pgm.dll's device record) is chosen at run time by which silicon-ID alternative matches.
- `u32[5] @0x1c` = section offsets. Section 1 holds the expected silicon IDs as UTF-16 text;
  section 2 holds one 0xEC-byte voltage record per variant (confirmed: section length = variants × 0xEC
  for m7096084, m7128084, e7160084, and pgm.dll steps through them with `add 0xec`).
- Voltage record: 16-bit millivolt fields. The silicon-ID set is at `+0x0a,+0x0c,+0x0e,+0x10`
  and is passed as `pgd_program_setup(1, 0, w0a, w0c, w0e, w10)`.
- `pgd_program_setup` limits, measured in the emulator: arguments 3 and 4 ≤ 10000, 5 and 6 ≤ 20000;
  arguments 1 and 2 are not range-checked. (An earlier note here had the limits on the wrong arguments.)

| File | Family | Variants | Expected IDs | Silicon-ID voltages (w0a, w0c, w0e, w10) |
|---|---|---|---|---|
| m7128084 | 0x0b | 2 | ALTERA92 | 0, 5000, 12000, 0 |
| e7160084 | 0x0a | 3 | ALTERA93 (+ a third entry starting `MPM71…`) | 0, 5000, 12000, 0 |
| m7096084 | 0xb3 | 2 | ALTERA91 | 0, 5000, 9000, 12000 |
| m7064084 | 0x0b | 4 | ALTERA93, ALTERA94 (+ `MPM70…`) | not yet read |

Which rail each of the four fields drives (VCC / VMRG / VPD / VPG) is not yet established.
The silicon-ID region records (selector, start, count) are not decoded.

## Simulation with recorded responses
`harness.boot_sim()` initialises the emulated module against `ReplayApu`, which answers port
reads with the bytes recorded from the real unit in `re/traces/init_real.txt`. With that the
emulated init succeeds, so later calls can be traced offline. `pgd_program_setup` with
non-zero voltages emits 13–27 thousand port operations (DAC ramps).

## Adapters
PLMJ7128-084 (© 1992, 6XX-04752) and PLMJ7000-084 (© 1993, 6XX-04985) are both passive boards:
resistor packs, three small diodes, a few capacitors and an LED, no ID device. That agrees with the
identical init traces. Whether their routing and resistor values are the same has not been checked.

## CORRECTION to the `.pcf` layout (supersedes the two `.pcf` sections above)
The five section offsets at 0x1c are relative to the end of an 8-byte file header, so every
section starts 8 bytes later than first assumed. With that shift everything lines up:
- Section 0: 20-byte region records `{u16 row, u16 col, u16 n, u16 count, u32 mask, u32 kind, u32 -1}`.
  `kind` 0x10 marks silicon-ID regions (8 characters, mask 0xFF, plus one value with mask 0x1F).
- Section 1: expected IDs as UTF-16 text, e.g. `ALTERA93`, `ALTERA93`, `MPM7160E`.
- Section 2: one 0xEC-byte voltage record per variant. `+4` = nominal VCC (mV);
  `+0x0a..+0x10` = silicon-ID voltages.

| File | Nominal VCC | Silicon-ID voltages | ID region (row, col, count) | Expected IDs |
|---|---|---|---|---|
| e7160084 | 5000 | 5000, 0, 12000, 0 | (0, 0, 8) + (0, 9, 1) | ALTERA93, ALTERA93, MPM7160E |
| m7128084 | 5000 | 5000, 0, 12000, 0 | (0x7c, 0, 8) + (0x7c, 9, 1) | ALTERA92 ×2 |
| m7064084 | 5000 | 5000, 0, 12000, 0 | (0x7c, 0, 8) + (0x7c, 9, 1) | ALTERA93, ALTERA94 ×2, MPM7064A |
| m7096084 | 5000 | 5000, 5000, 9000, 12000 | (0x303, 0, 9) | ALTERA91 ×2 |

The earlier table listing silicon-ID voltages as "0, 5000, 12000, 0" was wrong (shifted by one field).

## pgm.dll's sequence before a silicon-ID read (function around 0x1003d540)
1. Optionally `pgd_setup(31, 0, dev+0x464)` (only when a flag at 0x1006ffe8 is 1).
2. `fcn.1003e820(1)`: `dev[0x44c] = ge_mode_generic_part_type(13, dev)` (GE.dll, not analysed), then
   `pgd_setup(6, dev[0x44c], dev[0x478] | nominal_vcc << 16)`. Nominal VCC is record `+4`,
   overridable by `PGM_NOMINAL_VCC`, clamped to 2000–8000.
3. `pgd_program_setup(0, 0, nominal_vcc, 0, 0, 0)`.
4. `pgm_read_in_silicon_id` (program_setup mode 1 with the four ID voltages, then the reads).
In the module, family 0x0a with low word 0 or 1 selects type 0x0a; 2 selects 0x113; 3 selects 0x134.
Types 0x113/0x134 use a different, serial read (ports 3 and 8) from type 0x0a (ports 2–9, read on 7).

## Live attempts on an EPM7160ELC84-15 in the PLMJ7000-84 (2026-11-25)
`tools/read_siid.py <pcf> [--variant N] --real`. None returned a valid ID.
- With VCC wrongly 0 V (before the correction): all zeros, identical to an empty socket.
- Corrected sequence, variant 0 (type 0x0a): `FF` ×8 and `1F`, stable over four runs.
- Variant 1 (type 0x0a): `FF FF 00 00 FF FF 00 00`, `1F` (one run).
- Variant 2 (type 0x113): all zeros (one run).
- Making `GetTickCount` real-time (delay calibration `b988` became 17000) did not change variant 0.
Open: the part-type code actually passed for this device, other calls pgm.dll makes earlier in an
operation, and whether emulated timing distorts the module's in-stream delays.

## Part ID (resolves "which type code")
`pgd_setup(6, part_id, variant | nominal_vcc << 16)`: `part_id` is column 6 of `partinfo.crf`
(GE.dll's `ge_mode_generic_part_type` returns field `+0x48` of its 0x90-byte part record), and
column 2 names the `.pcf`. The `u16` at 0x0c in the `.pcf` header is NOT this value; using it
(0x0a) had selected the EPM7032.

| Part | part ID | module type by variant 0/1/2/3 |
|---|---|---|
| EPM5192 | 9 | 0x9 |
| EPM7032 | 10 | 0xa, 0xa, 0x113, 0x134 |
| EPM7064 | 13 | 0xd, 0xd, 0x116, 0x138 |
| EPM7096 | 15 | 0xf |
| EPM7128 | 18 | 0x12 |
| EPM7128E | 56 | 0x38, 0x38, 0x38, 0x119 |
| EPM7160E | 59 | 0x3b |
| EPM7128S | 72 | 0x48, 0x48, 0x119, 0x13d |

`tools/read_siid.py PART_NAME [--variant N] [--real]` now looks both up from `partinfo.crf`.

Live result with part ID 59 on the EPM7160ELC84-15: `5F FF 5F FF 1F FF 1F FF`, then `1F`;
identical over three runs. Not the expected `ALTERA93` (41 4C 54 45 52 41 39 33). The low five
bits are always 1. Forcing the delay calibration (`0x1003b988`) from the measured ~9000–17000
to 1000 made no difference to the data. Whether the socket latch was closed for these runs is
unconfirmed.

## First successful device read (2026-11-25)
`tools/read_siid.py EPM7064LC84-15 --real` on an EPM7064LC84-15 (date code B9513) in the
PLMJ7000-84 returned `41 4C 54 45 52 41 39 33` = `ALTERA93`, the first ID listed in
`m7064084.pcf`; the extra value at column 9 read 0. Trace: `re/traces/siid_7064_real.txt`.
The raw samples vary across the 80 clocks, unlike the EPM7160E trace where they were constant.
This validates the whole chain: firmware load, FPGA configuration, port protocol, emulated
module over USB, part-ID lookup, `.pcf` parsing and the pgm.dll call sequence.
The EPM7160E failure is therefore specific to that chip or to that device type.

A second EPM7160ELC84-15 (never exposed to the faulty early sequences) returned `ALTERA93` on
the first attempt with the same command (`re/traces/siid_7160e_chip2_real.txt`), so the E-type
read path (module type 0x3b) works and the first EPM7160E is itself non-responsive. Whether it
was already dead or was damaged by the two early reads that applied 12 V with VCC at 0 V is unknown.

EPM7096LC84-15 (part ID 15, module type 0xf): one attempt returned `08` for all nine ID
positions (expected `ALTERA91`). This type uses a different read (one sample each of ports
0x0c and 0x0d per position, raw `08 38` every time). Its region record has row field `0x0303`,
unlike the simple row values of the working parts, so the address arguments are probably
being passed wrongly for this older family. Not investigated further. Trace: `re/traces/siid_7096_real.txt`.

## Full read-back of an EPM7064LC84-15 (2026-11-25)
`tools/read_back.py EPM7064LC84-15 out.json --real`: 136 rows, 7865 cells in 2167 s, using
`pgd_read_silicon_id(row, col)` for every region in the `.pcf` under the silicon-ID voltages.
Results in `re/traces/readback_7064_full.json` (+ `.log`).
- Region records read as: `row` = first row, `n` = number of rows, `col` = first column,
  `count` = columns, `mask` = valid data bits (4-bit cells for the array, 8-bit for the ID row).
- ID row 0x7c read `ALTERA93` again in the same pass.
- The 88 main rows (80 columns) show 49 distinct patterns; some column groups are constant
  across rows and others vary, and there is no column wrap in the main array. The 16-column
  rows (0x58+, 0x70+, 0x80+ …) repeat with period 8.
- pgm.dll's function-pointer table, corrected again from the examine code at 0x1001fd42:
  `0x1006fca0` = `pgd_read_byte()`, `0x1006fcc0` and `0x1006fcc4` = the two one-argument address
  setters (column/row order not confirmed), `0x1006fcc8` = `pgd_setup`, `0x1006fc9c` =
  `pgd_program_setup`, `0x1006fcac` = `pgd_read_silicon_id`.
- `pgm_set_voltages` (0x1003eceb): a VCC of 0 in a voltage set means "use nominal VCC"; when
  margining is off the margin argument is replaced by VCC; mode is 1 (3 with margining, +4 if
  record flag 0x10). Examine uses record words `+0x12..+0x18` (0, 2000, 12000, 0 for the 7064).
- Not verified against a known image; no cell-to-fuse (POF/JEDEC) mapping yet.

## Stability check and JEDEC fuse map (2026-11-25)
- Re-reading 19 rows (553 cells) of the EPM7064 in two fresh sessions gave 0 differences from the
  full dump; the ID row read `ALTERA93` again.
- `.pcf` section 4 is the JEDEC map: `u16 @0x14` records of 16 bytes
  `{u16 row, u16 col, u16 count, u16 ?, u32 mask, u32 flags}` (pgm.dll's `pgm_get_jedec_info`,
  0x1002c785, walks it). A record covers `count` consecutive fuse numbers at `(row + i, col)`,
  bit `mask`. Tested against the cells that exist: this is the only interpretation that is
  one-to-one and in range (29488 distinct cell bits, no duplicates, for the EPM7064).
- The kind-0x100 region record holds the total fuse count as 32 bits (`n | count << 16`); it
  equals the map total for m7064084 (29488), m7096084 (44376), m7128084 (59136),
  e7128084 (59777), s7128084 (62703) and e7160084 (74653).
- Section 3 (between the voltage records and the map; empty for the EPM7064) is not decoded.
  The meaning of map `flags` (0, 2, 4) is unknown.
- `apu/pcf.py` parses all of this natively; `tools/dump_to_jed.py` turns a read-back dump into a
  JEDEC file. `re/traces/epm7064_readback.jed`: 29488 fuses, 21619 set. Fuse polarity relative
  to Altera's own JEDEC/POF output is not verified.

## EPM7160E read-back: only 8 of 10 bits per cell (2026-11-25)
`read_back.py` on the working EPM7160ELC84-15: 171 rows, 8425 cells in 2413 s; ID row `ALTERA93`.
The device's cells are 10 bits wide (region mask 0x3FF; the fuse map uses bits 0x001–0x200,
about 7,460 fuses each), but `pgd_read_silicon_id` returns only the low 8 bits, so roughly
14,900 of 74,653 fuses were not read. No JEDEC file is produced for it (`dump_to_jed.py` now
refuses). The EPM7064 is unaffected: its cells are 4 bits.
`pgd_set_column_address` + `pgd_set_row_address` + `pgd_read_byte` returned 0 for every test
cell on this device type (in either argument order), so that is not the route for type 0x3b;
`pgd_read_bytes_on_column` is the next candidate and its arguments are not yet known.

## Bulk read: `pgd_read_bytes_on_column` (2026-11-25)
From the module (0x1001c970) and pgm.dll's call sites:
`pgd_set_column_address(R)` then `pgd_read_bytes_on_column(start, count, int mask[], int out[])`.
For each i: `out[i] = mask[i] ? read(start + i) & mask[i] : 0`. A zero mask reads nothing.
(Altera's "column" is the coordinate these notes call a row.) `tools/bulk_probe.py`.
- On the EPM7160E it returns 10-bit values at about 0.3 s per 80-cell row; a 194-row scan took
  56 s (`re/traces/bulk_7160e_scan.json`).
- Its data does NOT agree with the cell-by-cell dump made with `pgd_read_silicon_id` on the same
  chip (about half the cells match, mostly blanks; no simple transposition or offset fits).
  Bulk rows show `3FF` blanks and a column pattern repeating every 5 cells that is identical
  across rows. Row 0 via bulk is not `ALTERA93`.
- So for device type 0x3b one of the two methods is mis-addressed for data rows. The
  cell-by-cell method is only proven on the ID row. Unresolved. The EPM7160E dump should be
  treated as unverified beyond its ID row; the EPM7064 dump needs the same cross-check.

## Bulk vs cell-by-cell on the EPM7064: also disagree (2026-11-25) — data read-back UNRESOLVED
- For module types 0x0a/0x0d/0x12/0x3b the bulk branch (0x1001cd35) requires `start == 0`
  (otherwise it reads nothing) and calls one routine (0x1000ca70, or 0x10032ae0 when the cmd-34
  flag is set) that fills `count` cells for the selected line, then applies the masks. pgm.dll
  always passes start 0 and count 0x50 (or 0x10 / 0x78).
- EPM7064 bulk scan (232 lines × 80, `re/traces/bulk_7064_scan.json`): repeatable, but only
  2464/7865 cells equal the cell-by-cell dump; transposition does not fit either; bulk line 0x7c
  is not `ALTERA93` (`0 f f f f 7 f f f f 8 e …`).
- `pgd_read_silicon_id` returns 8 bits for data rows too (e.g. row 0: `ff ff f7 ff ff ff af …`);
  the cell dump kept only the low nibble for 4-bit regions, so half of each returned value was
  discarded without knowing what it was.
- Bulk line 0x70 (a 16-column row) came back as runs of five equal values, suggesting a
  16-cell line is spread over 80 positions, but only 14 of 46 such lines are purely runs of five.
- Raising the margin voltage from 0 to VCC (as pgm.dll does for its read) changed neither method.
- Status: silicon-ID reads are solid on EPM7064 and EPM7160E. Neither data read-back method is
  understood well enough to trust, so `re/traces/epm7064_readback.jed` is NOT a valid image.
  Next: decode the layout produced by 0x1000ca70 and how pgm.dll's read loop (around 0x100176ae)
  turns lines into fuse data.

## Bulk read decoded (2026-11-25)
The line routine 0x1000ca70(out, count), used for module types 0x0a/0x0d/0x12/0x3b:
- clocks the device 80 times (with an extra control pulse on every fifth clock), then takes
  `count` samples in two passes (queue the reads, flush, collect);
- `count == 16`: sample s goes to `out[15 - s]`;
- otherwise: sample s goes to `out[count + s // 16 - 5 * (s % 16 + 1)]`, i.e. for 80 a 16 x 5
  de-interleave. So `count` must be the row's real width (80 or 16); reading a 16-cell row
  with 80 gives the "runs of five" seen earlier.
`tools/read_bulk.py PART OUT.json --real` reads every row with its declared width and mask and
reads the ID rows with `pgd_read_silicon_id`.
- EPM7064: 135 lines (7856 cells) in about 30 s; two runs identical; 80-wide lines identical
  to the earlier 80-wide scan; ID `ALTERA93`. JEDEC: `re/traces/epm7064_bulk.jed`.
- The earlier cell-by-cell dumps (`readback_*_full.json`) are superseded: that method is valid
  only for ID rows. Its period-8 repetition in 16-cell rows was an artifact.
- Still not independently verified against a known image; fuse polarity unverified.

## EPM7160E bulk read (2026-11-25)
- First attempt: two runs differed in 64 cells, all on line 0x01 (the first line read), all in
  bit 0x200, at every position except each fifth. The first line read after power-up is
  unreliable. `read_bulk.py` now reads one line and discards it, and re-reads five lines at
  the end as a check.
- With that: 170 lines (8416 cells, 10-bit) in about 33 s; two runs identical; no re-read
  warnings; ID `ALTERA93`. JEDEC: `re/traces/epm7160e_bulk.jed` (74653 fuses, 54249 set).
- Same caveats as the EPM7064 image: no independent reference, fuse polarity as read, read
  under the silicon-ID voltages rather than the programmer's examine set.

## Voltage/algorithm record field names (2026-11-25)
pgm.dll's parameter-dump routine (0x1000a94c) pairs every 16-bit record field with a name;
the table is in `apu/pcf.py` (`FIELDS`). Notable: `+4 vcc`, `+6 vpd`, `+8 vpg`, the per-operation
voltage sets (siid, examine, low/high verify, blank check, prog1-3 aim/challenge, reprog,
last chance, final verify), pulse parameters, `+0xc6..0xca` security pulse length/count/type,
`+0xd4 turbo_detect`, `+0xd6 margin_mode_type`, `+0xdc device_technology`, bulk program/erase pulses.
EPM7064 and EPM7160E records have `device_technology` 1 and zero pulse counts; the EPM7096
record has `device_technology` 0, prog pulses of 200 and security pulses of 100.

## Security bit
- pgm.dll has no "device is secured" detection or message. Its related messages are
  "Verify failure: device may already have the Security Bit turned on. Security Bit
  programming canceled.", "Device is not erased. Do you wish to program the Security Bit?",
  "Programming Security Bit..." and "Examined device contains programming data that cannot be
  loaded". Setting the bit on EEPROM parts is `pgm_7kae_security_eeprom` (0x100145d2).
- So a secured device is not flagged; it would simply read back as something other than its
  design. What a secured part returns has not been established here.
- Region kind 0x20 (EPM7064 row 0x74, EPM7160E row 0x80, 80 cells) is not yet identified.

## EPM7160E image withdrawn: bit 0x200 is history-dependent
In the `read_bulk.py` dumps only 54 of 8416 cells have bit 0x200 set, while the earlier
`bulk_probe` scan of the same chip (which read line 0 first) shows `3FF` widely. The first
line read returns bit 0x200 set and later reads return it clear, unless line 0 was read first.
So that bit is not stable data as read, and `epm7160e_bulk.jed` was deleted. Bits 0-8 agree
between runs. The EPM7064 (4-bit cells) shows no such effect: 4363 of 7856 cells blank.

## EPM7160E top-bit problem resolved (2026-11-25)
- pgm.dll's generic row reader is 0x1001e48e: `pgm_set_voltages(flag, 0, vcc, vmarg, vpd, vpg)`
  (so vmarg is never 0 for data reads: it is VCC, or a margin level in margin mode), then for
  each row in its row table, in ascending order: `pgd_set_column_address(row)` (or `row & 0x7f`
  in one mode) and `pgd_read_bytes_on_column(start, count, mask, out)` with the row's own
  start/count. `fcn.1001b7da` before it only makes a decision; it issues no module calls.
  Elsewhere pgm.dll brackets programming with `pgd_setup(0x22, 1, 0)` / `(0x25, …)`; cmd 0x22
  makes the module use line routine 0x10032ae0 instead of 0x1000ca70. Not used here.
- On the EPM7160E, bit 0x200 reads 0 on every line unless row 0 (the ID row) has been bulk-read
  first; a `pgd_read_silicon_id` call beforehand also leaves it dead. Reading row 0 first keeps
  it live for the whole pass. `read_bulk.py` now does that (and reads the ID text at the end).
- Result: two runs identical; all 7120 cells of the 80-wide lines identical to the earlier
  194-line scan; 6661 of 8416 cells have bit 0x200 set. `re/traces/epm7160e_bulk.jed`:
  74653 fuses. Changing vmarg (0 / VCC / examine level) did not change bit 0x200, but one cell
  flipped bit 0x100 between vmarg 0 and vmarg = VCC, so marginal cells exist.
- One ID read taken immediately after power-up returned a wrong eighth character once.

## Margin voltage and read validity, EPM7160E (2026-11-25)
`read_bulk.py --volts …` with `pgd_program_setup(3, 0, vcc, vmarg, vpd, vpg)` (mode 3 = margining).
Record values: examine 2000, low verify 1000, high verify 2500 / 1500, blank check and final
verify 4000 / 3000.
| vmarg (mV) | cells differing from the 2000 mV read (of 8416) |
|---|---|
| 1000, 1500, 2000, 2500 | 0 |
| 0 (mode 1, silicon-ID set) | 3 |
| 4000 | 97 |
| 5000 (mode 1, margin = VCC) | 981 |
So the image is identical across the whole low-verify to high-verify window: no marginal
cells on this chip. Higher margins make more bits read 0. Mode 1 with margin = VCC is not a
valid data read here; the earlier reading of `pgm_set_voltages` ("margin replaced by VCC when
not margining") does not apply to how these parts should be read.
`read_bulk.py` now defaults to the examine set. `re/traces/epm7160e_bulk.jed` regenerated from
the 2000 mV read (it differs from the margin-0 version in 3 cells).
The EPM7064 image (`epm7064_bulk.jed`) was read at margin 0 and should be re-read the same way.

## EPM7064 margin check (2026-11-25)
Reads at 1000, 2000 (examine) and 2500 mV margin are identical in all 7856 cells, and equal the
earlier margin-0 image; at 4000 mV 5 cells change. `re/traces/epm7064_bulk.jed` regenerated
from the examine read (content unchanged: 29488 fuses, 22726 set).

## EPM7096 (module type 0xf) — not working yet (2026-11-25)
- Record: vcc 5000, vpd 9000, vpg 12000; silicon-ID set (5000, 5000, 9000, 12000);
  `device_technology` 0; explicit program/security pulse parameters. ID region: column value
  0x303, 9 positions. Two variants, IDs `ALTERA91` + a ninth value of 0 or 1.
- Module path: `pgd_read_silicon_id(a, b)` → `pgd_set_column_address(a)`, `pgd_set_row_address(b)`,
  then a read (0x1000bf40) bracketed by 0x1000bcc0(2)/(7). For type 0xf with `0x1003a208 == 0`
  the column routine is 0x1000b5b0, which itself decodes the composite value: `>= 0x300` is a
  special group, otherwise the low byte is the address and 0x100/0x200 select a plane. So
  passing 0x303 unchanged is correct.
- On the one EPM7096 available, `(0x303, 0..8)` returns `0x109` for every position (the first
  attempt had returned `0x08`); `(i, 0x303)` returns `0x10F, 0x11F, …`. No ID.
- Not established: whether this chip is good; whether the unit's VPG rail works (the EPM7064
  and EPM7160E never use it: their vpg is 0); whether pgm.dll does more for
  `device_technology` 0 parts before reading.
- INCIDENT: a probe using `pgd_set_row_address(0x300…)`-style arguments made the module loop;
  the first time it ran for over 10 minutes with the chip powered before being killed (the unit
  was then re-initialised and shut down). `Harness.call` now has a watchdog (`call_timeout_s`,
  default 120 s) and probes are tried in simulation first.

## pgm.dll's module function table, exact (2026-11-25)
phm.dll stores the entry points at fixed offsets; in pgm.dll the table base is 0x1006fc90:
`+4 program_byte, +8 program_bytes_on_column, +12 program_setup, +16 read_byte,
+20 read_bytes_on_column, +24 read_chgrp, +28 read_silicon_id, +32 bulk_erase, +36 bulk_program,
+40 set_chgrp_dr, +44 set_chgrp_io, +48 set_column_address, +52 set_row_address, +56 setup,
+60 jtag_io, +64 jtag_drscan, +68 jtag_irscan`. This agrees with every slot identified earlier.
`tools/pgm_calls.py START END` lists the named module calls in any pgm.dll address range.

## Bulk erase in pgm.dll (0x10016e38 – 0x100184c7), first pass
Ordered module calls (arguments not yet resolved):
1. `pgd_setup(0x22, 1, 0)`, read silicon ID, `program_setup(0, x, vcc, 0, 0, 0)`, `pgd_setup(0x22, 0, 0)`.
2. `pgm_set_voltages(...)` (several, including the prog1 set `rec+0x38`, `rec+0x3c`).
3. `set_column_address(c)`; `read_bytes_on_column(0, 0x50, mask, out)` — a line is read BEFORE erasing.
4. `bulk_erase(a, 1, b, 0)`.
5. `set_column_address(c)`; `program_bytes_on_column(0, 0x50, data, mask, 0, 0)`; then the same
   line is read back. So a line is saved, the device erased, and the line re-written and checked.
6. Error paths call `pgd_setup(3, 0, 0)` and `pgd_setup(0x14, 4, 0)`.
7. The sequence 3–5 appears twice (two device classes or two lines), followed by
   `set_column_address` ×2, `bulk_program(rec+0xe0, x)`, and further `bulk_erase(a, 0, rec+0xe6, a)`
   / `bulk_erase(a, 0, b, 0)` calls, ending with `pgd_setup(0x22, 0, 0)`.
Which branch applies to which device, which line is preserved, and the argument values are
not yet decoded. Erasing without the save/restore step would lose whatever that line holds.

## Bulk erase decision tree in pgm.dll (0x10016e38), decoded (2026-11-25)
After the common start (limits vcc <= 4750?/vpd <= 12500 on two globals, silicon-ID re-read,
`program_setup(0, …, vcc, 0, 0, 0)`, messages "Bulk erasing EEPROM device via ISP/MEDIT"):
1. part ID 0x0a (EPM7032) and variant 0 → save a line, "Manufacturer's Bulk Erase...",
   re-write and verify the line.
2. else if `record[0] & 0x200` → the same shape: read a line (`set_column_address`,
   `read_bytes_on_column(0, 0x50, …)`), "Manufacturer's Bulk Erase..." `bulk_erase(a, 1, b, 0)`,
   "Reprogramming MS row..." `program_bytes_on_column(0, 0x50, data, mask, 0, 0)`, read back.
3. else if `device_technology == 3` → "FLASH Bulk Program..." (`bulk_program(rec+0xe0, x)`) and/or
   "FLASH Bulk Erase..." (`bulk_erase(len, 0, rec+0xe6 pulses, len)`).
4. else → "Standard Bulk Erase...": `bulk_erase(var_5e0 * [0x1006e48c], 0, var_14h, 0)`.
`var_14h` = `bulk_erase_pulse_length` (default 10 when the record has 0; override
`PGM_BE_DELAY_US`), masked to 16 bits and OR 0x10000 unless `record[0] & 0x80000`.
Record flags (`record[0]`): EPM7064 0x280 → path 2 (MS row saved and rewritten);
EPM7160E 0x80 → path 4 (standard).
The globals 0x1006e480 (6000), 0x1006e484 (13000 / 11250), 0x1006e488 (100 / 500 / 2000 / 10)
and 0x1006e48c (100 / 500) are set in the routine around 0x1003c2ed–0x1003d3xx, which also bulk-reads
lines 0x30 and 0x78 from the chip: the erase voltage and timing appear to be chosen from data
read out of the device. That routine is not decoded.

## Hosting pgm.dll in the emulator (2026-11-25)
`tools/host.py` loads pgm.dll (relocated to 0x20000000) beside pgdmpuz.drv in the same Unicorn
instance, fills pgm.dll's hardware function table with thunks to the module's exports, and
stubs its other imports (GUI calls are no-ops; a few GE.dll/MSVCRT functions are implemented).
- `pgm_load_control_file` (0x10031078) runs unmodified and returns 1. It reads the WHOLE .pcf:
  the file is a sequence of blocks, one per variant, each with its own 48-byte header and
  sections (EPM7064: blocks at 8, 11788, 23526, 38086). `apu/pcf.py` so far parses only the
  first block; the `u16 @0x0e` "variants" count is the number of blocks.
- The loaded data hangs off the device record (0x1006f790): `+0x24` variant count, `+0x438`,
  `+0x47c` variant table, `+0x44c` part type, `+0x478` current variant.
- `pgm_generate_tree` (0x100324ce) copies `dev+0x47c` → 0x1006fc28 and `dev+0x438` → 0x1006fd20
  and calls the variant selector 0x100320bb(variant), which sets the voltage record pointer
  0x1006f588, the row table 0x1006fc2c (count at 0x1006e474) and related pointers.
  `Host.load_device()` does those two copies and calls the selector.
- Result agrees with the native parser: EPM7064 voltage record `640, 0, 5000, 12000, 0, 5000,
  0, 12000 …`, 232 row entries of which 134 are flagged for reading; EPM7160E 193 / 169.

## pgm.dll's own silicon-ID check, hosted (2026-11-25)
`check_silicon_id` (0x1003d4bd) runs unmodified in `tools/host.py` once 0x1006e35c ("hardware
present") is set to 1. Its own debug text and numbered messages are logged.
- It tries the variants from the highest index down to 0. For each: `pgd_setup(6, part,
  variant | vcc << 16)` with THAT variant's nominal VCC, `program_setup(0, 0, vcc, 0, 0, 0)`,
  `program_setup(1, 0, <that variant's ID voltages>)`, then `pgd_read_silicon_id(row, i)` for its
  own ID location. On no match: `pgd_setup(3, 0, 0)` and message 0x5308000f.
- EPM7160E: variant 1 (ID voltages 5000 / 2000 / 10000 / 0), then variant 0 (5000 / 0 / 12000 / 0,
  row 0) — the latter is exactly the hand-derived sequence that reads the working chip.
- EPM7064: variants 3 (VCC 2500), 2 (VCC 3300), 1 (5000, ID at row 0), 0 (5000, row 0x7c).
- EPM7096: variant 1 has ID at row 0x7c with voltages 5000 / 0 / 12000 / 0 (the same style as
  the EPM7064); variant 0 has row 0x303 with 5000 / 5000 / 9000 / 12000. Earlier attempts on
  the EPM7096 used only variant 0's layout.
- Real EPM7096 (the only one available), hosted check: returns 0 after trying both variants
  (`re/traces/7096_hosted_idcheck.txt`). So Altera's own sequence also fails to identify this
  chip. The hosted path has not yet been run against a known-good chip.

## Dry-run guard and erase flow (2026-11-25)
- `Host.blocked` (default: bulk_erase, bulk_program, program_byte, program_bytes_on_column):
  these module calls are logged as BLOCKED, report success to pgm.dll and never reach the module.
- The erase-parameter routine 0x1003c2ed is called from inside `check_silicon_id` (0x1003de9f),
  so the parameters only exist after a successful ID check on a real chip. Calling the
  bulk-erase routine straight after `load_device()` in simulation (parameters all 0) issues
  `program_setup(1, 0, 5000, 5000, 0, 0)` and then stalls; the watchdog stops it.
- Callers of the bulk-erase routine: 0x1001141f (twice, after the ID check), 0x10013a0f (ID
  check at 0x10013a57, erase at 0x10013a71) and 0x1001ec4b.
- `tools/erase_dryrun.py PART` runs the real ID check on a chip, prints the chosen parameters
  and then runs the erase routine with the chip-changing calls blocked.

## UNRESOLVED: EPM7160E reads all ones after the first hosted check (2026-11-25)
With an EPM7160E reported to be the working one in the socket:
1. `erase_dryrun.py`: hosted `check_silicon_id` returned 0 (variant 1 then variant 0), so the
   erase routine was not run. Nothing chip-changing was sent (blocked set intact).
2. The direct `read_siid.py` (which previously read `ALTERA93` from this chip) then returned
   `FF` ×8 and `1F`, three times.
3. A second hosted check with return values logged: every `pgd_read_silicon_id` returned `0xFF`
   for both variants (`re/traces/hosted_id_7160e.txt`).
Not known: whether the chip was reading correctly immediately before step 1 (no direct read
was taken first); whether it is seated / the right chip; whether the variant-1 attempt
(`program_setup(1, 0, 5000, 2000, 10000, 0)`, issued by pgm.dll's own sequence) or anything
else in the hosted path changed the chip's state. The earlier conclusion that the EPM7096 is
bad rested on the hosted check and is therefore NOT established.

Follow-up: reseating the EPM7160E changed nothing, and disabling the watchdog changed nothing.
The EPM7064 then read `ALTERA93` twice with the direct method and its full bulk image matched
the earlier one, so the unit is working. The EPM7160E that had been reading correctly now
returns all ones; it has not been through anything except reads plus the hosted ID check
(including the variant-1 attempt at 5000 / 2000 / 10000 mV). Cause not established.

## Variant-1 vs variant-0 ID attempt on the EPM7160E, compared in simulation (2026-11-25)
`pgd_setup(6, 59, vcc<<16 | 1)` sets module flag 0x1003a208 to 1 (0 for variant 0); the type
stays 0x3b. The supply-on call and both `pgd_read_silicon_id` calls produce identical port
streams for the two variants, and `program_setup(1, …)` writes the same ports the same number
of times: only the DAC values differ (vmarg 2000 vs 0, vpd 10000 vs 12000). So at the module
level the variant-1 attempt is the same read at lower voltages; no additional operation was
found that could explain a chip changing. (Simulation: feedback reads return recorded/zero
values, so data-dependent branches on real hardware are not covered.)
Earlier photo: the first (non-responsive) EPM7160E carries date code B9513; the other is B9550.

## Native parser now reads every variant block (2026-11-25)
`Pcf.load_all(path)` walks the blocks (each starts with its own length); `Pcf.load(path)` is
block 0. Block header (relative to the block start): `+0 u32 length`, `+4 u16`, `+6 u16`
(the value earlier called "variants"), `+0x0c u16` JEDEC run count, `+0x14 u32[5]` section offsets.
Block counts agree with pgm.dll's loader: m7064084 4, e7160084 2, m7096084 2, m7128084 5.
Per-variant ID voltages agree with the hosted ID check (e.g. EPM7160E variant 1:
5000 / 2000 / 10000 / 0; EPM7064 variants 2 and 3 at 3300 and 2500 mV nominal VCC).
Expected IDs by variant: EPM7160E v0 `ALTERA93`, v1 `ALTERA98`/`MPM7160S`; EPM7064 v0
`ALTERA93`/`ALTERA94`, v1 `ALTERA96`, v2 `ALTERA98`, v3 `ALTERA00`; EPM7096 v0 `ALTERA91`
(row 0x303), v1 `ALTERA93` (row 0x7c).
Known gap: for some later blocks (m7064084 v2/v3, m7128084 v4) the fuse-map total does not
equal the declared fuse count, so the map layout or count field differs there.

Chip status: the EPM7160E that had read correctly is confirmed by date code (B9550) to be the
one now returning all ones. Cause not established.

## EPM7160E B9550 is intact; hosted ID check matches on it (2026-11-25)
- With the chip re-inserted, `read_bulk.py` read `ALTERA93` and all 8416 cells equal to its
  earlier image, and `read_siid.py` read `ALTERA93` twice. The earlier all-ones results on this
  chip were therefore a contact/seating problem in that insertion, not damage, and the hosted
  check was not the cause of anything.
- Hosted `check_silicon_id` on it: variant 1 does not match, variant 0 matches (the routine
  proceeds to its "Copyright: %s" step), then it stops with `pgd_setup(3, …)` and message
  0x53080011, returning 0. The chip read correctly again afterwards.
- Cause of the stop (0x1003d7a6): when the record's `margin_mode_type` is 2, the global
  0x1006e4a0 must be 0x0b or 0x0c. It is one of three hardware-description values
  (0x1006e498, 0x1006e49c, 0x1006e4a0) copied from 0x1006fc4c/50/54, which phm.dll fills when the
  hardware is opened; they are 0 in the host. pgm.dll compares 0x1006e49c with 5, 6, 7 and 13,
  and 0x1006e4a0 with 0x0b and 0x0c. The module's own description struct (`pgd_setup(12, 'D', …)`:
  name "APU (USB)", then 0x1f, 0xd0, 0x14 at +0x30) does not contain them, before or after the
  hardware is opened. The correct values for the APU are not yet known.
- Consequence for the EPM7096: the hosted check does read IDs correctly on a good chip, so its
  failure to get any ID from the EPM7096 under both variants stands as evidence about that chip.

## Hardware description and a complete erase DRY RUN on the EPM7160E (2026-11-25)
- The module fills the hardware description only once the hardware is open: `pgd_setup(12,
  'D', info)` then returns `+0x10 = 1`, `+0x14 = 0`, `+0x18 = 7`, `+0x1c = 0xc`, `+0x24 = 0x280`,
  `+0x38 = 0x14`, `+0x3c = 1000`. pgm.dll (0x1003e224) checks `+0x38 == 0x14` and `+0x10 in (1, 2)`
  and copies the three values to 0x1006e498/9c/a0. So for the APU: hardware kind 7, class 0x0c.
  `Host.describe_hardware()` does exactly this.
- With that, hosted `check_silicon_id` on the EPM7160E (B9550) returns 1 (variant 0) and logs:
  "Using MEDIT tables", "Vpp = 12.25v (4)", "Tprogram = 60.0ms (23)", "Terase = 200.0ms (6)",
  then `pgd_setup(0x1b, …)` and `pgd_setup(0x1c, …)`. The chosen parameters:
  0x1006e480 = 5000, 0x1006e484 = 12250, 0x1006e488 = 60, 0x1006e48c = 200.
- Bulk-erase routine with chip-changing calls blocked returns 1 after:
  `pgd_program_setup(3, 60, 4750, 2000, 12250, 0)`; "Standard Bulk Erase...";
  `pgd_bulk_erase(200, 0, 0x1000a, 0)` (BLOCKED). No line is saved or rewritten on this path.
- Direct ID read before and after: `ALTERA93`. Log: `re/traces/erase_dryrun_7160e.txt`.

## Adapters DO have an ID (corrects earlier notes) (2026-11-25)
- Module routine 0x100063a0 reads an adapter ID (0–0x7e) from the unit: it toggles port 0x01
  bit 0x20 and reads port 0x08. 0x1001e1a0 stores it at 0x1003b984 and, through a jump table at
  0x1001f27c indexed by adapter ID, checks that the selected device type (0x1003b9d0) is allowed
  on that adapter. `pgd_setup(24, 0, 0)` returns that verdict (1 = allowed).
  pgm.dll calls it when opening the hardware (0x1003ec47) and reports a mismatch.
- Earlier "the adapter is not identified" conclusion was wrong: identical INIT traces only
  showed that initialisation does not read the ID.
- 0x1006e4a0 is an adapter CLASS, not a hardware class: pgm.dll's table (0x1003e813) maps
  0 → "No programming adapter", 10 → "Vcc-mode", 11 → "Margin-mode", 12 → "Functional test
  adapter", others → unknown. The module reports 12 with either adapter fitted. 0x1006e49c is
  the fixture kind (7 for the APU).
- Real unit with the PLMJ7000-44 fitted: adapter ID 9. Verdicts: EPM7032-44 (type 0x0a) allowed,
  EPM7064-44 (0x0b) allowed, EPM7064-84 (0x0d) and EPM7160E-84 (0x3b) not allowed. So the unit
  detects the 44-pin adapter correctly and it matches both 44-pin parts.
- The PLMJ7000-84's ID has not been read yet.
- pgm.dll also has a socket/continuity check (0x100237e8 → `pgd_setup(14, count, buf)`; messages
  "Device is not in socket", "Device is backwards in socket", "Device pin continuity check
  failed at pin %s"). Its input is built by 0x10023e07, which depends on the compiler's
  database DLLs; not hosted.

## 44-pin parts on the PLMJ7000-44: no ID read yet
EPM7064LC44-12 and EPM7032LC44-15 both return zeros (occasionally unstable garbage) to the
direct ID read and to the hosted ID check under every variant, before and after reseating and a
power cycle. The module sends different pin-driver port sequences for these types than for the
84-pin parts (compared in simulation), i.e. the per-package pin mapping is in the module.

## EPM7032LC44-12 reads on the PLMJ7000-44 (2026-11-25)
Hosted ID check returns 1 (variant 0): ID `ALTERA92` (41 4C 54 45 52 41 39 32), followed by
`26 96 26 32`; pgm.dll logs "Using MEDIT tables", "Vpp = 12.75v (6)", "Tprogram = 40.0ms (18)",
"Terase = 200.0ms (6)". Adapter ID 9, adapter/device check passes. So the PLMJ7000-44 and the
unit work with 44-pin parts; the EPM7064LC44-12 and EPM7032LC44-15 tried earlier are
non-responsive chips. (The .pcf lists `ALTERA91` first for this part; this chip returns `ALTERA92`
and pgm.dll accepts it as variant 0.)

## BUG FOUND: forced delay calibration (2026-11-25)
The tools overwrote the module's delay calibration (0x1003b988, ns per stream command) with
1000, on the assumption that the value it measures under emulation (9000–17000) was inflated by
emulator overhead. That assumption was untested. On a known-good EPM7032LC44-12:
| value used | ID read |
|---|---|
| measured (16500) | `ALTERA92` |
| 30000 | `ALTERA92` |
| 1000 (the old forced value) | `ALTERA92` |
| 200 | garbage (`01 44 10 C9 02 01 00 10`) |
A smaller value means more padding commands per delay, i.e. LONGER delays; at 200 a good chip
misreads. So the forced 1000 stretched every delay ~16x relative to the module's own
calibration and sat between "works" and "fails". Every read so far (and every "non-responsive"
verdict) was taken that way. The tools now default to the measured value
(`APU_NS_PER_CMD=0`; a positive value still forces). All chips previously judged
non-responsive need re-testing. Timed operations (erase/program pulses) would also have been
mis-timed with the forced value.

## Re-tests with the measured calibration (2026-11-25)
- EPM7064LC44-12: hosted ID check, all four variants: zeros. Direct read at calibration values
  measured / 5000 / 40000 / 100000: zeros.
- EPM7032LC44-15: hosted ID check, all four variants: zeros.
So the calibration bug does not explain these two. Not yet re-tested with the fix: EPM7160E
B9513, EPM7096. Still unverified in the implementation: emulator-speed gaps between timed
steps, USB packet pacing, and pgm.dll's pre-read hardware-open steps.
- EPM7160E B9513 with the measured calibration: hosted ID check, both variants: `5F FF 5F FF 1F
  FF 1F FF FF`; direct read at measured / 5000 / 40000: the same bytes. Unchanged by the fix.
- The PLMJ7000-84 reports adapter ID 55 (0x37); the adapter/device check passes for the EPM7160E.

## "Continue anyway" branch traced and dry-run (2026-11-25)
In `check_silicon_id`, after every variant fails (0x1003df44…): `pgd_setup(3, 0, 0)`; if the
interactive flag 0x1006e3ec is 0 → error message 0x5308000f and return 0. If it is set →
prompt 0x51080090 (through 0x1000500d). On "yes": select variant 0's voltage record
(`dev+0x47c` → `+0x18`), `program_setup(0, 0, vcc, 0, 0, 0)`, `program_setup(1, 0, <ID voltages>)`,
return 1. The erase-parameter routine 0x1003c2ed is NOT run on this branch (it is only called
after a matched ID), so 0x1006e480/84/88/8c keep their previous values.
`Host.prompt_answer` answers pgm.dll's prompts.
Dry run on the non-identifying EPM7160E B9513 (chip-changing calls blocked): the check returns 1
via the prompt; parameters all 0; the erase routine then issues
`program_setup(3, 0, 5000, 2000, 0, 0)` (no programming voltage) and
`pgd_bulk_erase(0, 0, 0x1000a, 0)` (zero pulse value). I.e. pgm.dll's erase is a no-op for a
device it could not read parameters from. Log: `re/traces/continue_anyway_dryrun_b9513.txt`.
Implication: pgm.dll can only erase these parts after reading the ID and the Vpp/Tprogram/Terase
values out of the chip, so a secured part must still return them if Altera's own tool is to
reuse it. That argues against "secured" as the reason these four chips return no ID, but does
not prove it.

## FIRST REAL ERASE: EPM7160E B9513 (2026-11-25)
`tools/force_erase.py EPM7160ELC84-15 5000 12250 60 200 --yes-really-erase` (parameters copied
from the working sibling B9550; pgm.dll's own flow would have used zeros). Sequence sent:
ID check with "continue anyway" → `program_setup(3, 60, 4750, 2000, 12250, 0)` →
"Standard Bulk Erase..." → `pgd_bulk_erase(200, 0, 0x1000a, 0)` → 1 → shutdown. Measured delay
calibration (16000) in use. Log: `re/traces/force_erase_b9513.txt`.
Result (`b9513_before_erase.json` / `b9513_after_erase.json`), counting cells whose low 9 bits
are not all ones:
- earlier today 194; immediately before the erase 194, at the same 194 positions (10 per line);
- after the erase 2 (both at column 1 of rows 0x81/0x83, where the region mask 0x2FF excludes
  the bit concerned), i.e. effectively none.
So the erase cleared every programmed cell: the chip is alive and responds to erase, and the
erase path works on real hardware. The ID row still reads `5F FF 5F FF 1F FF 1F FF`, i.e. no
`ALTERA93`. The top bit (0x200) remains history-dependent on this chip and lines still change
on re-read in that bit.
Interpretation (not proven): this chip's ID/parameter ("MS") row is itself blank, so it cannot
be identified; it was already almost entirely erased before today. For comparison the working
B9550 has 3472 non-blank cells.

## Where the ID and parameters live; B9513's row 0 (2026-11-25)
- The parameter routine 0x1003c2ed reads the chip with `pgd_read_silicon_id(row, k)` for k = 0, 1,
  8, 9, 10, 11 (plus, for some part types, bulk reads of lines 0x30 and 0x78). So the ID row
  carries the ID characters and, in further positions, the Vpp / Tprogram / Terase codes
  (EPM7032 log: `ALTERA92` then `26 96 26 32`). This is the "MS row" pgm.dll saves and rewrites
  on the manufacturer's-erase paths. For the EPM7160E it is row 0.
- Bulk read of row 0 (80 cells, mask 0x3FF, margin mode), after the standard erase:
  B9513: `000` at position 0, `1E3` at 5, `000` at 10, every other cell blank; three reads identical.
  B9550 (from the early scan, old settings): data at every fifth position 0…70
  (`3F0 3F7 3E8 3FE 3E8 3F2 3F4 3F9 3F0 3E7 3E8 3E8 3E1 3E3 3E8 …`), the rest blank.
  So the standard bulk erase does not clear row 0, and B9513's row 0 holds only three data cells
  where a good chip has about fifteen: its signature row is largely blank or corrupt, which is
  why it cannot be identified. (The byte-level relation between these cells and the ID
  characters is not decoded; the ID read assembles its bytes differently from the bulk read.)
- To restore it: a manufacturer's erase (`bulk_erase(a, 1, b, 0)`, which per pgm.dll's flow is
  followed by rewriting the MS row) and then `program_bytes_on_column(0, 0x50, data, mask, 0, 0)`
  on row 0 with a good chip's row-0 cells. Arguments not yet traced. A clean row-0 capture from
  B9550 with the current settings is still needed.

## Row write and manufacturer's erase, traced (2026-11-25)
Module:
- `pgd_bulk_erase(a1, a2, a3, a4)`: implemented only for types 0x0a–0x1a, 0x37–0x40, 0x41–0x50,
  0x10b–0x112 (the EPM7160E, 0x3b, is included). Flags = (a2 != 0 ? 1 : 0) | (a3 >> 16 ? 2 : 0);
  0x1003a214 = a3 & 0xffff; then 0x1000d060(a1, flags). So a1 = Terase value, a2 = the
  "manufacturer's" flag, a3 = pulse length (10) with 0x10000 as a second flag.
- `pgd_program_bytes_on_column(start, count, mask[], data[], x, y)`: start must be 0; for these
  types `data[i] |= ~mask[i]` and then the line routine 0x1000cee0(data, count) programs the
  selected line. Bits that are 1 are left alone.
pgm.dll's manufacturer's-erase path (0x10017a50…), used when `record[0] & 0x200`:
1. MS row = the row of the kind-0x10 (silicon-ID) region. `set_column_address(row)`;
   `read_bytes_on_column(0, 0x50, mask, saved)`.
2. It recomputes some values from the saved cells (positions 25/30/35 and 65/70/75 feature), and
   proceeds only if a decoded value equals 0x41 ('A'): it will not erase a chip whose MS row
   does not read as valid.
3. "Manufacturer's Bulk Erase...": `bulk_erase(Terase, 1, pulse | 0x10000, 0)`.
4. `pgm_set_voltages(0, Tprogram, vcc, 0, vpp, 0)`; "Reprogramming MS row...":
   `set_column_address(row)`; `program_bytes_on_column(0, 0x50, mask, data, 0, 0)`.
5. Read the row back and compare.
The EPM7160E record lacks the 0x200 flag, so pgm.dll never does this on that part; whether
erase flag 1 clears row 0 on it is unknown.

## Clean capture of B9550's row 0 (`re/traces/b9550_row0.json`)
Margin mode at 1000 / 2000 / 2500 mV, three reads each, all identical. Data in 16 cells at
positions 0, 5, …, 75 (low bytes F0 F7 E8 FE E8 F2 F4 F9 F0 E7 E8 E8 E1 E3 E8 EE), every other
cell blank. (Bits 8–9 read 1/0 here; bit 9 is the history-dependent one.)
ID-read positions 0–11 on the same chip: `41 4C 54 45 52 41 39 33 D7 80 FF FF`.

## FIRST SUCCESSFUL PROGRAMMING: B9513's signature row restored (2026-11-25)
`tools/ms_row.py` on the EPM7160E B9513, parameters from its sibling (Vpp 12250 mV, Tprogram 60,
Terase 200), measured delay calibration:
1. `read`: row 0 = `000` @0, `1E3` @5, `000` @10 (confirms the chip).
2. `erase`: `program_setup(3, 60, 4750, 2000, 12250, 0)`; `pgd_bulk_erase(200, 1, 0x1000a, 0)` → 1.
   Row 0 afterwards: no data cells. So erase flag 1 (the "manufacturer's" erase) DOES clear the
   signature row on this part, which the standard erase (flag 0) does not.
3. `write re/traces/b9550_row0.json`: same voltage set-up; `set_column_address(0)`;
   `pgd_program_bytes_on_column(0, 80, mask, data, 0, 0)` → 1, data = B9550's low bytes with bits
   8–9 left unprogrammed. Row 0 read back: 16 data cells, low bytes identical to the source.
4. Direct ID read: `ALTERA93`, twice.
5. pgm.dll's own non-interactive ID check: returns 1 (variant 0), logs "Using MEDIT tables",
   Vpp = 12.25 V, Tprogram = 60.0 ms, Terase = 200.0 ms, and sets its erase parameters to
   5000 / 12250 / 60 / 200 from the chip.
6. Full read at 1000 and 2500 mV margin: identical in all 8416 cells; array blank (2 cells with a
   low-9-bit zero, both in the masked-out bit noted earlier).
Logs: `re/traces/b9513_ms_erase.txt`, `b9513_ms_write.txt`, `b9513_restored_vm*.json`.
So: the erase path, the row-programming path and the read path are all confirmed on hardware,
and a chip that could not be identified is now a blank, identifiable EPM7160E (carrying its
sibling's ID and parameter codes). The other non-identifying chips may be in the same state.

## FIRST FULL PROGRAM: B9550's image written into B9513 (2026-11-25)
`tools/program_image.py EPM7160ELC84-15 re/traces/bulk_7160e_examine.json --yes-really-program`
- Pre-check: chip blank (2 masked-bit cells), ID `ALTERA93`.
- pgm.dll's ID check passed and gave Vpp 12250, Tprogram 60; calibration 16500.
  `program_setup(3, 60, 4750, 2000, 12250, 0)`, then for each of the 159 lines with data:
  `set_column_address(row)`; `pgd_program_bytes_on_column(0, width, mask, data, 0, 0)`.
  All calls returned 1; 11 s in total.
- Read back at 1000 and 2500 mV margin: the two reads are identical in all 8416 cells.
- Against the source image: bits 0–8 match in every one of the 8416 cells (no bit that should
  be 0 reads 1, none that should be 1 reads 0); 3472 cells with data in both.
- Bit 9 (0x200) cannot be verified on this chip: it reads 0 in all but 160 cells whether the chip
  is blank or programmed (source image: set in 6661 cells). On B9550 that bit is live when
  row 0 is read first. Whether B9513's bit-9 plane is defective, or whether it depends on
  something in row 0 that was not copied (bits 8–9 of row 0 were left unprogrammed), is unknown.
  The device has about 7,460 fuses on that bit.
Logs: `re/traces/b9513_program.txt`, `b9513_programmed_vm*.json`.

## Clues on the EPM7160E's top cell bit, from the module (2026-11-25)
For module type 0x3b a cell value is assembled (0x1001642d) from single bits of seven port
reads (ports 0x03, 0x06, 0x07, 0x0c, 0x10, 0x12, 0x15; 0x15 is read but unused):
| cell bit | source | | cell bit | source |
|---|---|---|---|---|
| 9 (0x200) | port 0x0c bit 0 | | 4 | port 0x12 bit 4 |
| 8 | port 0x0c bit 5 | | 3 | port 0x10 bit 3 |
| 7 | port 0x07 bit 3 | | 2 | port 0x10 bit 4 |
| 6 | port 0x06 bit 0 | | 1 | port 0x03 bit 1 |
| 5 | port 0x06 bit 7 | | 0 | port 0x03 bit 5 |
So each cell bit is one device pin sampled through the unit; bit 9 is a single line.
The line routine sets up differently depending on the selected line: for line 0 it calls
0x10013220(1), which for type 0x3b raises port 0x03 bit 3 and port 0x10 bit 5; for any other
line it calls 0x10013830(1), which raises port 0x07 bit 2. This is the mechanism behind
"bit 9 is only live after line 0 has been read": the line-0 set-up leaves two extra control
bits asserted. Whether pgm.dll relies on that, and what those controls do on the chip, is not
established.
Observations on B9513 that any explanation must fit:
- 80-cell lines: bit 9 reads 0 in every cell, blank or programmed.
- 16-cell lines 0x59–0x69: bit 9 read 1 in all 272 cells when blank; after writing an image
  whose cells there are all 0, low 9 bits read 0 everywhere but bit 9 still reads 1 on 10 of the
  17 lines. So the pin can read high, and bit 9 was programmed on some lines and not others.
- Programming was a single pass with no verify/retry; pgm.dll's real algorithm has aim,
  challenge, verify and remedial passes (not traced).

## pgm.dll's EEPROM row programmer (0x10013c08), traced (2026-11-25)
Called from `pgm_do_programming_passes` (0x1001235b). For non-ISP hardware it logs
"Programming EEPROM device via MEDIT" and "Using <…>auto-increment programming algorithm", then:
1. `pgm_set_voltages(0, Tprogram, vcc, 0, vpp, 0)` with the values read from the chip
   (0x1006e488, 0x1006e480, 0x1006e484). With `margin_mode_type` 2 and adapter class 0x0c this
   becomes `program_setup(3, Tprogram, vcc, 0, vpp, 0)`: VCC uncapped (5000) and margin 0.
2. `pgd_setup(0x25, (record[0] & 0x100000) ? 0 : 1, 0)`. Module flag 0x1003a218 (default 1) is only
   used by the routines for types 0x113+, so this is a no-op for the parts handled here.
3. For every row-table entry with a data pointer, in ascending row order:
   `set_column_address(row)`; `program_bytes_on_column(start, count, mask, data, 0, 0)` with the
   row's own start/count. One call per row, no verify inside this routine.
4. `pgd_setup(0x25, 1, 0)`.
So the structure matches `tools/program_image.py`; the differences are the set-up values:
that tool used `program_setup(3, Tprogram, 4750, 2000, vpp, 0)` (copied from the ERASE set-up)
where pgm.dll uses VCC 5000 and margin 0 for programming. Verify and any remedial passes are
in the caller and not yet traced.

## Project goal changed: clean native rewrite (2026-11-26)
Goal set by the owner: a fully compatible MAX+PLUS II replacement for macOS, as a clean native
rewrite (no Altera code at run time). The emulator host stays as the reference for checking
native code. First native board-level operation: `apu/board.py`.
- Without any start-up writes, port 8 reads 0 (native adapter ID came back 0; module: 55).
- After replaying the module's first twelve start-up writes (`board.startup_words()`), the
  native adapter-ID read returns 55 with the PLMJ7000-84 fitted, matching the module.
  `python -m apu adapter` does: firmware, FPGA, start-up writes, adapter ID — no vendor code
  executed (the firmware and FPGA image are still Altera's data, loaded from the user's copy).

## Voltage DACs decoded (2026-11-26)
`tools/dac_decode.py` turns the port-1 stream into DAC commands. One TLC5620 command is 25
writes: `C0`, eleven bits (A1 A0 RNG D7..D0) each as `82|b` then `80|b`, the load strobe (`88` =
chip A, `84` = chip B), `00`. `apu/board.py: dac_words()` reproduces three frames from the real
start-up trace exactly (unit test).
Channel roles, from simulation with different requested voltages (final codes):
| request | A0 | B0 | B1 | B2 | B3 |
|---|---|---|---|---|---|
| vpd 12000 | 45 | 154 | 0 | 0 | ramp |
| vpd 10000 | 45 | 128 | 0 | 0 | ramp |
| vmarg 2000 | 45 | 154 | 0 | 51 | ramp |
| vpg 9000 | 45 | 154 | 118 | 0 | ramp |
So B0 = VPD (~20 V full scale), B1 = VPG, B2 = VMARG (~10 V full scale), B3 = VCC, A0 = 45 always.
VCC is closed-loop: on the real unit for 5000 mV the module sets B3 = 127, steps it 0 → 127 one
code at a time, then alternates 127 / 128 while taking ten reads of port 1 per step (values 00–03),
i.e. it trims against feedback. In simulation (reads always 0) it runs to 254. The trim rule and
the remaining ~330 non-DAC port writes of a voltage set-up (pin-driver ports) are not yet decoded.
A separate port-1 pattern based on `90`/`92`/`91`/`93` with reads appears at start-up (probably a
serial read of something); not decoded.

## Reference checking for native code; native LED (2026-11-26)
`tools/golden.py`: `module_ops(call, …)` runs a module call in the emulator against the replay
device and returns its port operations; `ops_of_words()` gives the same form for a native word
list. A native routine is accepted when the two match. First use: `board.led_words(state)`
matches `pgd_setup(8, state, 0)` for states 1, 2 and 0 (the module prefixes one extra `F7`
opcode-5 write). Run natively on the unit (states 1, 2, 1, 2, 0); visual result not yet reported.

## Rail helpers and the on-board ADC, from the module (2026-11-26)
The voltage set-up (0x10018950) is built from small helpers that convert millivolts to a DAC code
and call 0x10007a60(channel, code, 1) (chip B; shadow at 0x1003b3ec + 4·channel → 0x10007910):
| helper | rail | chip-B channel | code |
|---|---|---|---|
| 0x100069b0 | VPD | 0 | mV · 255 / 20000 |
| 0x100069f0 | VPG | 1 | mV · 255 / 20000 |
| 0x10006a20 | VMARG | 2 | mV · 255 / 10000 |
| 0x10006850 | VCC | 3 | mV · 255 / 10000 (and 0x10006940 with the value clamped to 2500–3300) |
The final codes seen in traces are a few counts higher than these formulas for VPD/VPG
(154 vs 153, 118 vs 114), so 0x10007910 applies a correction or trim; not yet decoded.
0x10007ad0(channel < 12) is a serial ADC read on the same port-1 bus: eight `90` writes, then a
4-bit channel address clocked as `90|b`, `92|b`, data bits read back from port 1 (bit 0 data,
bit 1 valid), with a wait on `80` + read until bit 1 is set (up to ten tries). This is the
`90/92/91/93` pattern at start-up and the source of the port-1 reads during a voltage set-up:
the unit measures its own rails and the module trims the DACs against the measurement.
Other pieces of the set-up: 0x10006d10 stores a time value scaled by the delay calibration;
0x100064f0 ("pgd_set_header") and 0x1001ad10 / 0x10018e10 configure pin drivers per device type.

## Native ADC read verified on hardware (2026-11-26)
`board.adc_read(apu, channel)` (transcribed from 0x10007ad0). All twelve channels, idle unit with
the PLMJ7000-84 fitted, 9 V supply:
| | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| native (after only the 12 start-up writes) | 1 | 79 | 218 | 128 | 1 | 169 | 0 | 0 | 4 | 0 | 255 | 128 |
| module (after its full initialisation) | 1 | 184 | 225 | 128 | 0 | 168 | 0 | 0 | 4 | 0 | 255 | 128 |
Ten channels agree to within one count. Channels 1 and 2 differ, as expected if they follow DAC
state that the module's full initialisation sets and the native start-up does not yet.
What each channel measures, and its scale, is not yet established.

## ADC channels identified on hardware (2026-11-26)
Native code only; each DAC stepped alone, never above about 5 V worth of code (chip in socket):
| change | ADC channel that moved |
|---|---|
| B0 (VPD) 0 → 32 → 64 | ch 7: 0 → 23 → 46 |
| B1 (VPG) 0 → 64 | ch 6: 0 → 46 |
| B2 (VMARG) 0 → 64 → 127 | ch 3: 0 → 65 → 127 |
| B3 (VCC) 0 → 64 → 127 | ch 3: 0 → 65 → 128 |
| A0 45 → 90 | ch 5: 169 → 147 (inverse) |
So ch 7 = VPD, ch 6 = VPG (about 0.72 ADC counts per DAC count, ~109 mV per count), ch 3 = the
VCC/VMARG node (1:1 with either DAC, 39 mV per count; the two are evidently combined before the
measurement point), ch 5 = something set inversely by DAC A0. Fixed readings: ch 1 = 185,
ch 2 = 225, ch 8 = 4, ch 10 = 255, ch 11 = 128, the rest 0; what they measure is unknown.
`board.RAILS`, `set_rail()` (open loop) and `read_rail()` encode this. Rails were returned to 0.

## The silicon-ID read is a line read plus a bit-plane decode (2026-11-26)
For module types 0x12–0x18 and 0x38–0x50 (0x10003658): `pgd_read_silicon_id(row, k)` =
`set_column_address(row)`, the ordinary 80-cell line routine 0x1000ca70, then 0x10003900(cells, k).
For the EPM7160E's group (0x10003a1c, cell width W = 10): first cell = k // 2W + (40 if k odd),
bit = (k >> 1) % W, and the byte is that bit of eight cells spaced five apart, MSB first.
Checked against B9550's captured row 0 (data cells F0 F7 E8 FE E8 F2 F4 F9 F0 E7 E8 E8 E1 E3 E8 EE):
bytes 0–7 decode to `ALTERA93` and bytes 8, 9 to D7, 80 — exactly what the module's ID read
returned on that chip. `apu/max7000.py` implements it (unit test with those values).
So the ID and parameter codes are bit planes of the signature row; this also explains why
copying the row's low bytes to B9513 restored its ID.
(An older group, types 0x0a–0x15 with flag 0x1003a208 = 0, uses bit k // 10 of cells 8k … 8k+7.)
