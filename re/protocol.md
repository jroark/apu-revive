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

## Native line read for the EPM7160E, verified on hardware (2026-11-26)
`apu/max7000.py: line_read_ops(row, first)` reproduces the module's port operations for a line
read op for op (rows 0, 1, 5, 0x69 in simulation; unit test holds the digests). Structure:
1. delay (first read after a voltage set-up: port 1 = C0, 749 no-ops, port 1 = 00) or, on later
   reads, a line release (`14=0C 07=00 03=00 10=00`);
2. row select: 110 clocks on port 3 bit 0 to flush a shift register, one marker bit
   (`11=04 15=02`, one clock), then `row` more clocks;
3. sixteen identical 30-write precharge blocks on ports 06/07/0C/0D/10/11/15;
4. row 0: `03=08 10=20 03=88`; other rows: `07=04 03=80`; then two 74-no-op delays around `14=0D`;
5. 80 clocks on port 3 bit 7, seven port reads (03 06 07 0C 10 12 15) after each; every 16 clocks
   an interlude (`14=0C`, pulse port 0C bit 4, `14=0D`, each separated by a 74-no-op delay).
Delays are `01=C0`, N no-op commands, `01=00`; N comes from the measured stream rate (16.5 µs per
command here), so 74 ≈ 1.2 ms and 749 ≈ 12 ms.
Cell bit ← port bit, fitted against the known signature row (corrects two entries noted earlier):
bit 9 = 0C.0, 8 = 0C.5, 7 = 07.3, 6 = 06.0, 5 = 06.7, **4 = 15.4, 3 = 12.3**, 2 = 10.4, 1 = 03.1,
0 = 03.5.
Hardware check (`tools/native_line.py`; the module still selects the device and sets voltages,
the read and decode are native), B9513 in the socket: ID `ALTERA93`, parameter bytes D7 80, and
all 89 eighty-cell data rows (7120 cells) equal the stored image in bits 0–8. Not yet native:
the 16-cell rows, device select / voltage set-up, and any write.
Mixing native reads with module reads in one session upsets the module (its shadow of the port
state goes stale); its next ID byte reads wrong. Not a native-code problem.

## Fully native silicon-ID read (2026-11-26)
`tools/capture_setup.py` records the module's traffic on the real unit per call (delays kept) and
`tools/setup_decode.py` condenses it into DAC commands, ADC reads, delays and port writes.
Findings:
- Device select (`pgd_setup 6`) sends nothing.
- Supply on (mode 0): `W5 00=F7`; adapter ID twice; DACs A0=45, B3=target, B0=B1=B2=0; `09=7E 09=00`;
  B3=0; `00=10`; ramp B3 0…target, 5 no-ops apart; opcode-5 writes 03=80, 14=1C, others 0;
  then the trim; `W5 00=F7`, `00=90`.
- Trim (code 0x10006b80): start at mV·255/10000, raise one code at a time until ADC channel 4
  reads ≥ min(mV·255/5000, 255). So **ADC channel 4 is the socket supply, 5 V full scale**. On this
  unit 127 reads 254 and 128 reads 255, every time.
- Read set-up (mode 1): rails (B0 = 154 for 12 V), trim, clear data ports, `0A=01`, delay, `0A=81`,
  `W5 14=0C`, then the device's opcode-5 table (03=D9 04=FF 05=FF 06=7A 07=F5 0B=FF 0C=9C 0D=F7
  0E=FF 0F=FF 10=AF 11=7C 12=F5 13=FF 14=EF 15=E3), 120 pulses of port 0C bit 4, trim again.
- Shutdown and the end of initialisation share an idle sequence: rails to 0, every data port
  (3–7, 0B–3F; not 8, 9, 0A) written 0 and opcode-5 FF. Initialisation also runs a walking-ones
  self-test of the data ports.
`apu/programmer.py` implements these. `python -m apu id` on B9513: `ALTERA93`, parameter bytes
D7 80 FF FF, supply code 128, ADC shows VPD ≈ 12.0 V during the read and rails at 0 afterwards.
No Altera code runs; the firmware and FPGA image loaded at start are still Altera's data.
The native trim refuses to go more than 4 codes above nominal.

## Fully native read-back of the EPM7160E (2026-11-26)
- Sixteen-cell rows use the same line sequence stopped after 16 clocks; the cell index is 15 − s.
- Rows 128 and up are selected through a second chain: marker `11=10 15=00`, then row − 128 clocks.
- The examine (margin) set-up is the ID set-up with the margin DAC set (B2 = mV·255/10000); nothing
  else differs on the wire.
- The row layout comes from the device file (`programmer.data_rows`): rows 1–88 and 128 are 80
  cells, rows 89–105 and 129–192 are 16 cells.
`python -m apu read out.json` on B9513 (2.0 V and 1.0 V margin): 8416 data cells, identical in
all ten bits to the hosted read-backs of the same chip (b9513_programmed_vm1000/2500), identical
between the two margins, and the JEDEC file made from it has the same fuse data and fuse checksum as the hosted one; only the header comment naming the part differs, and with it the whole-file checksum on the last line.

## Line write decoded in simulation (2026-11-26) — not yet run on a chip
`tools/sim_capture.py` runs module calls with no device, delays kept. For the EPM7160E:
- Row select as for a read (110 flush clocks, marker, row clocks).
- 80 load units, one per cell: the ten cell bits are put on lines 0D.5, 0D.7, 10.1, 15.5, 11.6,
  11.5, 06.6, 07.6, 0D.0 (bits 0–8) and 0C.3 (bit 9), then port 0C bit 4 is pulsed. The module
  changes one line per write from a shadow of each port.
- Order: an 80-cell line is shifted last cell first (unit u carries cell 79 − u). A 16-cell line
  still takes 80 units: cell c rides in unit 5·(15 − c) + 4 and the rest are all ones.
- The programming pulse starts with `14=0D`, port 0D bit 4 raised, `14=09`, and the call returns.
  It is ended by the next call's first writes: `14=0D`, 0D bit 4 dropped, `14=0C`, `0C=18 0C=08`.
  So the pulse length is whatever time passes between the two calls.
`max7000.LineWriter` reproduces four consecutive writes (rows 5, 90, 0, 128) op for op.
Open before any native programming on hardware: the rail correction that turns 12750 mV into
DAC 164 (the plain formula gives 162; 9000 mV VPG gives 118 against 114), how the pulse time is
kept, verify, and the erase sequence.

## Programming-rail code rule (2026-11-26)
From 0x10018ae5 / 0x10018b21, confirmed by a simulated sweep of eleven VPD and three VPG values:
above 5000 mV the request is raised before conversion — VPD by (mV − 5000)/50, VPG by
(mV − 5000)/12 — then code = mV·255/20000. `board.vpd_code` / `vpg_code`; the earlier "+1" for
12 V in the native read set-up is replaced by this rule (same result, 154).

## Erase and pulse timing decoded in simulation (2026-11-26) — not run on a chip natively
- Erase start (after a set-up): 1 ms delay, `0D=10`, `0D=12`, `0C=00` (`0C=80` for the
  manufacturer's erase that also clears the signature row), `01=C0 01=00`, `14=08`. The erase time
  argument never reaches the wire. Erase end, sent at the start of the module's next call:
  `14=0C`, short delay, `07=04` (only with flag 0x10000 in the pulse argument), `0D=10`, `0C=00`,
  `0D=00`, `07=00`. `max7000.erase_start_ops` matches the module's traffic exactly.
- Delay routine 0x10006d40(x), x = calibration × milliseconds: up to 1 ms it emits `01=C0`,
  ceil(1000·x/cal)·0.75 − 1 no-ops, `01=00` (so 750 commands per ms; the earlier "16.5 µs per
  command" reading of the calibration was wrong — it is host loop iterations per ms). Above 1 ms
  it spins a host loop, which is how programming and erase pulse times are kept; nothing shows
  on the wire. In the emulator that loop runs at emulator speed, calibrated against real time.
- Row write end (0x1000cff5): strobe on, host wait of calibration × Tprogram, strobe off. In the
  captures the "off" writes only leave with the next call, so on real hardware the pulse lasted
  from one call's flush to the next. Native code sends on, sleeps, sends off.
- pgm.dll's row programmer (0x10013c08) calls set_column_address and
  program_bytes_on_column(…, 0, 0) per row with no wait of its own.
`programmer.erase_epm7160e` / `program_epm7160e` assemble these; unit-tested for structure only.
I could not hook the delay routine in the emulator to confirm wait positions directly.

## Device model files: first look (2026-11-26)
`vendor/.../7k/` holds, besides the programmer's `.pcf` files, the compiler's device data:
27 `.maf` (architecture), 82 `.daf`, 94 `.ddf` and 137 `.dmf` (per speed grade) files. No sample
`.pof` files ship with the package; the tutorial has AHDL (`.tdf`) and schematic (`.gdf`) designs.
`.maf`: "MAF\0" header whose u32 at 0x0C is the number of logic array blocks (2 for the EPM7032 up
to 16 for the EPM7256), then named block-type records (ROW, COLUMN, MCELL, GH, PIA, HIO; later
families add PIA_INPUT, POE) and large undecoded tables. HIO's count is macrocells + 4.
`apu/maf.py` reads this much. My first reading of the record fields as per-device counts was
wrong (they are identical in every file) and was dropped.
These files are the input a native fitter and assembler would need; decoding them means
reversing the compiler DLLs that read them, which has not started.

## Which MAX+PLUS II modules hold the remaining knowledge (2026-11-26)
By the file extensions each module names (BASELINE directory, 105 executables and DLLs):
| module | references | role (inferred from name and references) |
|---|---|---|
| `asm.dll` | `.daf`, `.pcf` | assembler: fitted design → fuse data. The fuse-level model (stage 3) lives here and in the `.daf` files |
| `p2j.dll`, `f2p.dll` | `.pof` | programming-file conversion (POF ↔ JEDEC). The quickest route to the POF format (stage 2) |
| `vpr.dll` | `.maf`, `.acf` | reads the architecture files; partitioner/router |
| `fit.dll` | `.acf` | fitter (1.8 MB) |
| `mls.dll`, `cmp.dll`, `mdl.dll` | `.acf`, `.cnf`, `.tdf` | logic synthesis, compiler driver, design database |
| `sim.dll` | — | simulator |
Roles are inferences, not traced. Nothing here has been reversed yet.

## POF format lead (2026-11-26)
`p2j.dll` (32 KB, export `p2j_convert_pof_to_jedec`) carries debug strings that outline the POF:
a sequence of packets (length, tag) with tags Creator_Id, Device_Name, Comment_Text, Turbo_Bit,
Security_Bit and Address_and_Data. One Address_and_Data form has start_col, start_row,
col_count, row_count, mask, flags — the same row/column/mask addressing the programmer uses —
and another has start_address and representation_width. The module opens the device's `.daf`
and `.ddf` through `GE.dll`. Next step for stage 2: reverse this module for the packet layout and
the row/column → JEDEC mapping, then write a native POF reader/writer and check it against the
`.pcf` fuse map already decoded.

## POF container decoded (2026-11-26)
From `p2j.dll` and `GE.dll` (static reading; nothing hosted):
- Header: 6-byte magic `POF\0\0\0`, u16 version = 1 (GE.dll keeps a table of file types at
  0x100367f8: 6-byte extension, then current/max/min version; `pof` is entry 14 with 1/1/1),
  u32 packet count.
- Packet: u16 tag, u32 length, payload. The converter dispatches on tag − 1 through an 18-entry
  table: 1 Creator_Id, 2 Device_Name, 3 Comment_Text, 4 Turbo_Bit, 5 Security_Bit, 6 logical
  address and data (four u16: start_address, representation_width, address_count, flags),
  7 electrical address and data (six u16: start_col, start_row, col_count, row_count, mask,
  flags), 8 terminator (u16 CRC), 9 Symbol_Table, 10 Test_Vectors, 17 logical with a u32 start
  address and u32 count, 18 one more u16 (name not recovered).
- CRC: `ge_crc_init` 0xFFFF; `ge_crc_update` reflected, polynomial 0x8408; `ge_crc_get_value`
  inverts — CRC-16/X.25. Every read in the converter goes through one wrapper that updates it,
  so it covers the file from offset 0 through the terminator packet's tag and length.
`apu/pof.py` reads and writes this (round-trip and CRC check-value tests). Untested against a
real POF: none is available. Still to do: how electrical data bytes map to cells, then
POF ↔ read-back image ↔ JEDEC conversion.

## POF electrical data ↔ device regions (2026-11-26)
`p2j.dll` 0x100028f1 pairs each electrical packet with one 20-byte region record from the device
file by comparing all six header fields: start_col = region row (line), start_row = region
column (cell), col_count = lines, row_count = cells per line, mask, flags = region kind. Altera's
"column" is the line address — the same naming as the module's `set_column_address(row)`.
0x10002760 then copies the payload byte for byte, line by line, a fixed number of bytes per
cell (a device-wide bit width / 8). So a POF's data blocks are the `.pcf` regions verbatim.
`apu/pof.py: image_to_pof / pof_to_image`; `python -m apu topof image.json out.pof`, `python -m
apu pof file.pof`. B9513's native read-back makes a 17 kB POF with seven electrical packets and
converts back with every cell equal except two on lines 129–133 where the device's mask (0x2FF)
excludes bit 8. Assumptions still open: two bytes per cell, low byte first; which region kinds a
real POF carries (data kinds 0x200 and 0x20 are written; the ID row is left out).

## JEDEC polarity settled; native POF ↔ JEDEC (2026-11-26)
`p2j.dll` 0x10002bbb–0x10002c5c: for 16-bit cells it reads low byte then high byte, ANDs with the
fuse's mask and sets the JEDEC bit when the result is non-zero. So cells are little-endian
16-bit and **fuse = 1 iff (cell & mask) ≠ 0, no inversion** — what `dump_to_jed.py` assumed. This
answers the long-open polarity question to the extent that POF cell values are what the
programmer writes and reads back (pgm.dll passes POF data to the row write unchanged, 0 bits
programmed). Its JEDEC header (0x100022f6): STX, comment, `QF n *`, optional `QP`/`QV`, `ND
<device> *`, `NV MAX+plus II Version … *`, `G1*`/`G0*` from the security bit, then `L0` and the
fuse data.
`apu/jedec.py` (write, parse, fuses ↔ image) and `python -m apu pof2jed / jed2pof`. On B9513's
image: POF → JEDEC gives the same 74653 fuses as the earlier image → JEDEC tool, and
JEDEC → POF → JEDEC is stable. A POF rebuilt from JEDEC differs from the original in 948 of 8416
cells, all in bits no fuse covers (the fuse map uses 74653 of the 84160 cell bits); those bits
come back as 1.

## EPM7064 (module type 0x0D) native read path — simulation only (2026-11-26)
Each device group has its own line protocol. For the EPM7064:
- Row address in parallel, not clocked: bits 0–2 → port 15 bits 3, 2, 1; bits 3, 4 → port 11 bits
  6, 5; bits 5, 6 → port 11 bits 2, 1 for rows below 128. Rows 128+ set port 11 bit 0, keep bits
  3, 4 on 11.6/11.5 and carry bits 5, 6 on port 15 bits 7, 5 in a second write of port 15. The
  module leaves those two lines as they were for later lower rows (shadowed state).
- Sixteen precharge units on ports 07/0B/10, then `0D=02` (or `06=50` for the signature row,
  row 124), then 80 or 16 clocks on port 3 bit 7 with four reads each (03, 10, 07, 0C); every 16
  clocks an interlude on ports 0C/0B with 4-no-op delays.
- Four-bit cells: bit 0 = 03.5, 1 = 10.3, 2 = 07.4, 3 = 0C.0 (found by feeding single port bits to
  the module in simulation). Cell order as for the EPM7160E.
- Set-up: identical to the EPM7160E's except the opcode-5 table (03=DF 04=FF 05=FF 06=FB 07=EF
  0B=FF 0C=F6 0D=DF 0E=FF 0F=FF 10=F7 11=FF 12=FD 13=FF 14=EF 15=FF) and the tail (`10=00 0B=00
  0B=00 07=00`, 120 pulses of port 0B bit 4). Release before shutdown: `0C=00 0D=00 06=00`.
`max7000.line_read_ops_7064` matches all 20 captured module reads; `programmer.read_setup_ops`
matches the module's set-up for both devices (the EPM7160E against the real-unit capture).
`programmer.PROFILES` now carries the per-device differences; `python -m apu id|read --part
EPM7064 --allow-unverified` exists but has not been run on a chip. After the refactor the
EPM7160E was re-read on the unit: same ID, and all cells equal to the earlier native read.
The EPM7032 (type 0x0A) differs again (one read port, serial addressing) and is not done.

## EPM7032 (module type 0x0A, PLMJ7000-44) native read path — simulation only (2026-11-26)
- 44-pin adapter layout: only ports 03–07 and 0B–0D are used. Supply-on clears them and writes
  opcode-5 03=83, 06=80, others 0. Read set-up: `03=02`, others 0, the same opcode-5 release,
  `0A=01`, delay, `0A=81`, delay, drive table (05=FF 06=71 07=A5 0B=FF 0C=0F), `06=00 0C=00`,
  120 pulses of port 06 bit 4.
- Row address in parallel: bits 0, 1 → port 0C bits 1, 0; bits 2–6 → port 0B bits 7 down to 3.
- Line read: on a repeat, `05=00`, port 0C rewritten with the previous row's bits, `06=00`; then
  address; 16 latch units on ports 06/0C preceded by 4 pulses each (80-cell rows) or by 0, 8, 0,
  8 … pulses (16-cell rows); `0C` bit 2 raised (or `06=01` for the signature row, row 124); a
  ten-write clock on ports 02/03/09 (toggling port 3's opcode-5 register); `05=20`; one read of
  port 07 per cell; an interlude every 16 cells.
- Two-bit cells: bit 0 = 07.3, bit 1 = 07.4. Cell order as the other devices.
`max7000.line_read_ops_7032` matches all 16 captured module reads and `read_setup_ops` matches
the module's set-up. `python -m apu id|read --part EPM7032 --allow-unverified` exists; not run on
a chip. The signature decode for this group is from the module's code only.

## Chip parameter codes, stream rate, native program command (2026-11-26)
- pgm.dll's parameter routine for the EPM7160E group (0x1003cc7d → 0x1003cdc9): signature bytes
  8 and 9 give three indices: Vpp = 11250 + 250·(byte9 >> 5 & 7) mV; a programming-time index
  byte8 & 0x1F into a 32-entry table (20, 40, 60 … 600 at index 23 … 2000); an erase-time index
  byte8 >> 5 & 7 into (10, 20, 30, 40, 60, 100, 200, 500). A flag in the device record selects a
  second pair of tables and a different bit split. For B9513 (D7 80): Vpp 12250 — matching the
  hosted run — time index 23 → 600, erase index 6 → 200. The hosted run that programmed B9513
  logged Tprogram 60, read when its signature row was only partly restored, so the two do not
  contradict, but **the unit of these time values is unresolved** (the routine's debug print
  divides by ten). Native code takes Vpp from the chip only as a suggestion and never takes times.
- Stream rate, measured natively: 60000 no-ops take 181 ms, about 3 µs each (USB-limited). The
  module's delay routine assumes 750 commands per millisecond, so its "1 ms" delay of 749 no-ops
  really lasts about 2.3 ms on this set-up.
- `python -m apu program FILE --vpp-mv N --tprog-ms N` (POF, JEDEC or image): prints the plan
  and stops unless `--yes-really-program`; then programs line by line and verifies by native
  read-back inside the device's masks. Dry runs on B9513's image: from the POF 167 of 170 lines
  and 21770 bits; from the JEDEC 123 lines and 20381 bits (bits no fuse covers are left alone).
  Not run on a chip.

## Device assembler files (.daf): first structure notes (2026-11-26)
The `.daf` files are the assembler's fuse-level description (they are what `asm.dll` and `p2j.dll`
open through `GE.dll: ge_open_daf`). From the loader (GE.dll 0x10017f76) and m7032044.daf:
- 8-byte header `DAF\0\0\0` + version (0x0E), then a 0x8C-byte fixed block read in one piece. Its
  first u32 is the device's fuse count (0x3950 = 14672 for the EPM7032, the same number the
  `.pcf` declares).
- Then a sequence of element descriptors held in memory as 0x30-byte records: nine u32 fields
  read one by one, followed by optional arrays whose presence is tested through the record's
  fields at +0x24, +0x28 and +0x2C (one array is of 16-byte entries). The loader runs this
  pattern twice, then reads arrays of 16-byte and 32-byte entries and further counted blocks.
- The body is dense with fuse numbers (for example runs of 0x3700, 0x370C, 0x3718 … stepping by
  12), so the files do map logical resources to fuse addresses.
Nothing here is decoded to meaning yet. That needs the element semantics from `asm.dll`
(164 KB), which is the next large piece of reversing for stage 3.

## Device assembler files parsed (2026-11-26)
`apu/daf.py` follows GE.dll's loader exactly: 0x8C-byte header; h[0x28]+1 then h[0x2C]+1 element
records (nine u32 fields; f8+1 u32; f5+1 four-u32 entries; an (f6+1) × (f7+1) u32 matrix); then
21 tables sized by header fields. **All 160 `.daf` files in the package parse with zero bytes
left over**, so the container is right.
What the EPM7032's file shows (m7032044.daf, 14672 fuses):
- header: h[0x30] = 44 pins, h[0x34] = 32 macrocells, h[0x38] = 2 LABs, h[0x10] = 88;
- table t34e: one entry per macrocell, 0, 440, 880 … — 440 fuses per macrocell, i.e. five
  product terms of 88 inputs (36 routed signals in true and complement plus 16 shared-expander
  inputs would make 88); 32 × 440 = 14080;
- element records carry per-macrocell fuse numbers in their matrices: ids 24, 25, 26, 27, 28, 1,
  29, 30, 31 (two fuses), 6, 32 occupy twelve consecutive fuses per macrocell from 14080, and id
  34 two per macrocell from 14608;
- 14464–14607 (144 fuses) are not in these matrices.
On the larger devices h[0x10] is 11 and t34e steps by 55, so that pair means something else
there; and the EPM7160E has further element ids (81, 82, 83, 84, 67).
Not known: what each element id controls, the order of inputs within a product term, and the
entries of the other tables. That is asm.dll's side.

## EPM7032 fuse map: structure confirmed on a real chip (2026-11-26)
- A macrocell's array starts at (block − 1) · h[0x14] + t34e[macrocell], with the block number in
  table t34c and h[0x14] = 7040 the size of one logic array block (16 macrocells × 440). My first
  version used t34e alone and so read block 1 twice; a test caught it.
- `apu/fusemap.py` + `python -m apu fuses x.jed`: per macrocell, five product terms as lists of
  connected columns (fuse 0 = connected) and the option elements' values.
- The EPM7032LC44-12's own contents fit the model: of 160 product terms 74 have no connections,
  26 have every column connected, and 60 carry one to nine literals. Columns 72–87 (taken to be
  the shared expanders, from table t54a) are each used about once; among columns 0–71 a few
  recur across many terms (7, 39, 71, 67, 19 …), as an address decoder's inputs would.
- The 144 fuses 14464–14607 are outside the array and the per-macrocell elements; 36 routed
  signals × 2 blocks × 2 bits would make 144, so they are probably the signal routing selects.
Still unknown: the signal on each column, true/complement order within a pair, the meaning of
each option element, and the 144 routing fuses.

## Assembler-file field names, and the layout generalised (2026-11-26)
`asm.dll` contains a routine that writes an ASCII dump of the loaded `.daf` ("ascidaf.dmp"); its
format strings name every field. `apu/daf.py` now uses those names:
- header: total_bits, wl_offset, pt_offset, mc_pt_offset, pia_wl_offset, lab_offset, pia_offset,
  lab_pia_wls, pia_wls, pt_wls, arch1_bits, arch2_bits, n_pins, n_mcs, n_labs, (bit_tab size at
  0x3C), n_default_groups, n_jedec_groups, n_vpterms, n_jedec_bits, pterms_per_mc, exp_per_lab,
  exp_per_mc, lab_clocks, lab_clears, initial_arr_val, first_pia_addr, por_wl_1, por_wl_2,
  assemble_mux, assemble_por, jedec_defined, bit_matches_jed, bit_shift, bit_mask. The dump's
  order matches the file except for the count at 0x3C, which I take to be its last name,
  n_data_bits (assumption; the names after 0x3C are the dump's order shifted by one).
- element ("arch_hdr1/2"): arch_type, arch_value, num_bits, offset, index, num_vpterm, addr_rows,
  addr_columns, num_select; then select[], arch_vpterm[] (input_eq_type, vpterm, condition,
  setting) and arch_addr[row][column] (fuse addresses; row = macrocell, pin or block).
- tables: default_group, bit_jed_conv, oe_pin, io_wl, mc_wl, exp_wl, pia_wl, io_pia_wl, mc_pia_wl,
  mc_lab, mc_data_bit, pia_data_bit, pia_addr, pia_wl_polarity, vpterm_array (input_eq_type,
  pterm_offset), mc_pterm, exp_pterm, exp_data_bit, pin_mc, output_mc, bit_tab. "wl" = word
  line, an input column of the array.
With the names the array layout is the same on every device: pt_wls = 88 columns, pterms_per_mc
= 5, a macrocell's terms start at (block − 1)·lab_offset + mc_pterm·mc_pt_offset and are
pia_wl_offset·mc_pt_offset = 88 apart (the EPM7032 stores 88 and 1, larger devices 11 and 8).
`fusemap.FuseMap` now handles all of them, and gives each macrocell's pin from pin_mc.
Real images through it: EPM7064 — 320 terms: 164 empty, 65 fully connected, 91 with 1–15
literals; EPM7160E (B9550) — 800 terms: 271 empty, 133 fully connected, 396 with literals.
Virtual product terms (EPM7032): eleven (input_eq_type, physical term) pairs — (0,0) (1,1) (0,2)
(0,3) (0,4) (1,0) (0,1) (3,2) (2,3) (5,3) (4,4). So each physical term either feeds equation
type 0 (taken to be the macrocell's OR) or a special input: types 1, 3, 2/5 and 4 on terms 0/1,
2, 3 and 4. The option elements 24, 25, 26, 27, 28 and 1 list which virtual terms they depend
on; these are the product-term steering fuses. What equation types 1–5 are (clock, clear,
preset, enable, XOR …) and what elements 29, 30, 31, 6, 32, 34 do is not yet known; the
database module that defines those enumerations has not been looked at.

## Where the option elements get their meaning (2026-11-26)
`asm.dll` 0x10006880 is the assembler's per-element routine: it takes (element index, design
node), reads the element's arch_type, and dispatches through a byte-indexed jump table over
types 1…0xB9 (index bytes at 0x1000e309, targets at 0x1000e195). Each case derives the fuse
value from flag bits in the design node record (for example node+0x28 bits 0x10, 0x40, 0x80,
0x100, 0x200) and calls 0x100123b6(value, element, row) to set the element's fuses. The routine
is about 30 kB. asm.dll imports nothing descriptive from the database module (only generic
record iteration), so the flag bits have no names here; naming them needs either the database
module's record definitions or designs with known settings.
Decoding this switch for the dozen MAX 7000 element types (24–32, 1, 6, 34, and the E-family's
81–84, 67) is the remaining work for a fuse-level disassembler. Not started.

## Lead: the design database also has a named dump (2026-11-26)
`db.dll` carries format strings for a text dump of its records, with field names: for placed
nodes `resource`, `pin`, `mc`, `lab`, `row`, `column`, `rdf_bits`, `use_count`, `userpin` …; for
equations `eq_type`, `src1`, `src2`, `output`, `pterms`; and device-level `turbo_bit`,
`security_bit`, `invert_input`, `slow slew`, `expander`. Matching that dump routine's field
accesses to the offsets asm.dll reads (node+0x14, +0x18, +0x28 …) should name the flag bits the
option elements are computed from, and `eq_type` is the likely source of the assembler file's
`input_eq_type` values. Not yet done.

## Design-database placed-node record: named offsets (2026-11-26)
From `db.dll`'s record dump (the routine referencing "mc %3d " at 0x10003380), each format string
paired with the field it loads:
| offset | name | offset | name |
|---|---|---|---|
| +0x0C | clique | +0x48 | lab (userlab) |
| +0x10 | resource | +0x4C | row (userrow) |
| +0x14 | pin | +0x50 | column (usercolumn) |
| +0x18 | mc | +0x54 | pin_next |
| +0x28 | rdf_bits | +0xE8 / +0xEC | Assign / Assign_index |
| +0x38 | temp_id | +0xF8 | bit field: periph out/fbk/inp, oe_source, use_oe, unused_input, invert_input, routing cell, mpp_created_pin |
| +0x3C | index | +0xFC | bit field: auto_pin_name_suffix, increase_delay, pci_io, pullup_resistor, bus_hold, input_reference, io_standard, low_cap, power_up_high, legally_placed, packed_local, dup_reg_id, rdf_exp, open_drain, gtl_open_drain |
| +0x40 | part | +0x104 / +0x108 | lut_mask / backup_lut_mask |
| +0x44 | userpin | +0x13C / +0x140 | borrower / lender (parallel expanders) |
| +0x16C / +0x170 | use_count / max_2_fanout | | plus arrays input_eqs[], pterm_types[], lut_inputs[] |
So in asm.dll's element routine, node+0x14 is the pin, node+0x18 the macrocell, and node+0x28 is
`rdf_bits` — the flags the steering and option fuses are computed from (bits 0x10, 0x40, 0x80,
0x100, 0x200 seen so far). The individual rdf bits are not named by the dump (it prints the
word in hex); db.dll does hold the primitive names (DFF, DFFE, TFFE, JKFFE, SRFFE, LATCH, MCELL,
EXP, SOFT, TRI, INPUT, OUTPUT, BIDIR, CARRY, CASCADE), which is where to look next for what
sets each bit.

## Product-term steering decoded from the device file itself (2026-11-26)
- asm.dll's helper 0x100061e0(node, k) returns the node's `pterm_types[k]` (array at node+0xA8;
  the database dump names it) — the role assigned to physical product term k.
- The case for element 27 sets its fuse to 1 unless pterm_types[3] is 2 or 5, when it sets 0.
  The device file says the same thing in element 27's arch_vpterm entries: (0, vp4, 1),
  (2, vp9, 0), (5, vp10, 0) — (equation type, virtual term, fuse value). So for one-fuse
  elements the third field is the fuse value used when the term serves that equation type,
  and **the steering can be read from the `.daf` without the code**. Element 1 picks between
  types 5 (fuse 1) and 2 (fuse 0) on term 3.
- The cases for elements 24, 25, 26, 27, 28 call the helper with k = 0, 1, 2, 3, 4: they are the
  five terms' steering elements. Type 0 is the macrocell's own sum; types 1 (terms 0 and 1),
  3 (term 2), 2 and 5 (term 3) and 4 (term 4) are the special inputs, not yet named.
- Other cases, by what they test: element 29 — rdf_bits 0x800000; element 30 — no borrower
  (parallel expander) and a helper; element 31 (two fuses) — rdf_bits 0x20, 0x300, 0x400;
  element 6 — rdf_bits 0x40 / 0x300; element 32 — bit 0 of the node's +0xF8 field (the dump's
  "periph out"); elements 34 and 83 — pin nodes (rdf_bits 0x80), the output-enable select;
  81 — rdf_bits 0x100; 82 — rdf_bits 0x80000000. rdf_bits 0x10 marks a macrocell node.
`fusemap.FuseMap.term_roles()` applies the one-fuse tables; the listing now tags each term. On
the real EPM7032: term 3 is the sum in 12 macrocells (always empty there) and type 2 in 20
(one or two literals in 18 of them) — what a clock or clear term would look like.

## Steering for all five terms (2026-11-26)
Elements 24, 25, 26 and 28 are one-fuse select elements: asm.dll's cases call the select setter
with an index, and the fuse value is the element's select[index]. Their arch_vpterm entries give
(…, virtual term, select index), so the mapping again comes straight from the `.daf`:
| element (term) | fuse 0 | fuse 1 |
|---|---|---|
| 24 (term 0) | type 1 | sum, or type 1 |
| 25 (term 1) | sum | sum, or type 1 |
| 26 (term 2) | type 3 | sum, or type 3 |
| 27 (term 3) | type 2 or 5 (element 1: 0 → 2, 1 → 5) | sum |
| 28 (term 4) | type 4 | sum, or type 4 |
A 1 is ambiguous for 24, 25, 26, 28 from these tables alone; the assembler's cases choose among
several select indices with the same fuse value using other state (register flags, borrowed
terms). In asm.dll the per-term role (`pterm_types[k]`) is a separate enumeration: 2 and 5 mean
a special input, 3 the sum, and 0/1 unused, judging by the cases for elements 26 and 27.
On the real EPM7032 (32 macrocells): term 4 is steered to type 4 in every macrocell (fully
connected in 14, empty in 12, literals in 6); term 2 is type 3 with no connections in 12 and
otherwise sum-or-type-3; term 3 as before. `FuseMap.term_roles()` implements the table.
Note the first field of an arch_vpterm entry does not always equal the virtual term's own
equation type (element 25's entries), so that field's meaning is still open.

## Architecture files parsed; signal routing located (2026-11-26)
`apu/maf.py` now follows GE.dll's loader (ge_open_maf): six header u32, ROW/COLUMN records,
block-type records (MCELL, GH, PIA, HIO, …), a u32 table, then routing entries of five i32
(type, index, f2, n_targets, n_fuses) + targets + (fuse address, flags) pairs. **All 27 MAX 7000
`.maf` files parse with nothing left over.**
EPM7032: 208 entries = 32 macrocells, 68 GH lines, 72 PIA inputs (36 per block), 36 HIO. A
macrocell or HIO entry targets the GH line it drives; a GH line targets the PIA inputs it can
reach and gives, for each, the address of that input's select field and the value that selects
it. Flags: bits 0–9 position in the line's list, bits 10–15 field width in fuses (2, 3, 4 on the
EPM7032, EPM7064, EPM7160E), bits 20–27 select value, bits 30–31 extra flags on some entries.
The select fields are exactly the fuses the assembler file leaves unexplained: 14464–14607 on
the EPM7032 (72 fields × 2), 28928–29359 on the EPM7064 (144 × 3). Each field chooses among 3–4
sources (EPM7032), 7–8 (EPM7064) or 14–15 (EPM7160E).
Real images: every field of the EPM7032 and EPM7064 chips holds either a value that has a source
or, on the larger device, all ones (no source listed for that value: an unused input). On the
EPM7160E the count of unmatched fields depends slightly on the assumed bit order (311–320 of
366, the minimum when the third fuse is the low bit), so the order of fuses within a field is
**not established** — on the two smaller chips every order decodes equally well.
`maf.routing()` returns {field address: (width, {value: ("mc"|"hio", n)})}.
Still needed to name a product term's inputs: the field bit order, which array column pair each
PIA input feeds (taken to be 2m and 2m+1 so far, unverified), which column of a pair is the
complement, and which pin each HIO entry is.

## Routing select encoding: the simple reading fails on larger devices (2026-11-26)
Looking at the bit patterns actually present in the select fields of real chips:
| chip | field width | sources per field in the file | patterns present |
|---|---|---|---|
| EPM7032 | 2 | 3–4 | 00 ×34, 11 ×18, 01 ×16, 10 ×4 |
| EPM7064 | 3 | 7–8 | 111 ×73, 011 ×38, 101 ×27, 110 ×6 |
| EPM7160E | 4 | 14–15 | 1111 ×311, 1110 ×19, 1011 ×18, 1101 ×10, 0111 ×8 |
On the two larger chips a used field always has exactly one programmed fuse, so three or four
choices, not the eight or fifteen the file's select values would give if the field held the
value in binary. So "field = value" is wrong there (it still decodes without contradiction only
because the single-zero patterns happen to be valid values). The select value must combine with
something else; on the E family there is a further entry type (4, "PIA_INPUT", 360 entries) with
its own fuse per input. asm.dll does not read the `.maf` at all and its `.daf` PIA tables are
empty for these parts, so the routing fuses are placed by other code — the fitter/router side
(`fit.dll`, `vpr.dll`) — which has not been looked at.
I withdrew the "value selects the source" wording from `maf.routing()`; what stands is the file
structure, the field addresses and widths, and the source lists per field.

## Routing select values: more evidence, still unresolved (2026-11-26)
- In the architecture files the select values are ordered by source kind: on the EPM7064 values
  0–2 are macrocell feedbacks, 3 mixed, 4–7 I/O entries (7 present in only 48 of 144 fields); on
  the EPM7160E 0–7 are macrocell feedbacks, 8 mostly, 9–14 I/O entries.
- Reading a field as a binary number (first fuse = low bit), the real EPM7064 uses only values 7
  (all ones, 73 fields), 6 (38), 5 (27) and 3 (6): never a macrocell-feedback value and never 4.
  Decoded sources: 67 I/O entries and 4 macrocells that carry no logic. The EPM7160E uses 7, 11,
  13, 14 only.
- So two readings remain, and I cannot choose between them from data: (a) binary, with these two
  designs happening to take block inputs almost only from pins and from a narrow set of
  values — unlikely given that no two-zero code ever occurs in 126 used fields; (b) the fuses
  are not a binary select at all. Neither is established.
The router's code is the way to settle it (GE.dll exports `ge_get_routing_struct_for_maf_index`,
`ge_get_maf_direct_source`, `ge_set_maf_routing_destinations` for it).

## Native AHDL front end started (2026-11-26)
Independent of the reversing work: `apu/ahdl.py` is a lexer and recursive-descent parser for
AHDL written from the language as the tutorial designs use it (comments, TITLE / INCLUDE /
CONSTANT / FUNCTION prototypes, SUBDESIGN ports with groups, VARIABLE sections including state
machines and instances, Boolean equations, IF / CASE / TABLE / DEFAULTS, calls with empty
arguments, `inst.(a, b)` port groups, names that start with or consist of digits). All 38
`.tdf` files in the tutorial parse. `apu/ahdl_eval.py` evaluates purely combinational designs
(equations ORed per node, DEFAULTS, IF, CASE, TABLE with don't-cares, groups, constants,
== and !=) by iterating to a fixed point, and refuses anything with registers, machines,
instances, BIDIR ports or arithmetic. 13 of the tutorial designs construct under it.
`python -m apu ahdl x.tdf` prints the structure and, for small combinational designs, a truth
table. Nothing here produces fuses: logic synthesis, fitting and assembly are not started.

## AHDL: registers and state machines simulated (2026-11-26)
`apu/ahdl_eval.py` now handles DFF / DFFE / TFF / TFFE (clock, enable, asynchronous clear and
preset), state machines (clock, reset, enable; next state holds when not assigned; optional
state values and bit names), one-bit-to-group broadcast, + and − on groups and ordered
comparisons. `Simulator.set()` applies input changes and clocks every register whose clock rose,
using the input value from before the edge; `clock()` pulses a clock. Functional only — no
delays, no setup/hold. Checked with my own designs (a loadable counter with enable and clear,
a toggle, a three-state machine); not compared with the vendor simulator.

## Two-level synthesis for small flat designs (2026-11-26)
`apu/synth.py`: every output and register input is tabulated with the evaluator over all values
of the design's inputs and register outputs (at most 14 signals), signals a function does not
depend on are dropped, and the table is minimised (Quine–McCluskey primes, greedy cover). The
polarity with fewer terms is kept and flagged as inverted, since a MAX 7000 macrocell can invert
its sum. Registers get D (enable and T-type folded in), clock, clear and preset equations; a
state machine's reset becomes clear or preset per bit according to the reset state's code.
Tests replay the equations against the simulator (all input combinations for combinational
designs; 300 random clocked steps for a counter). `python -m apu synth x.tdf` prints the
equations. This is device-independent: no product-term limit, expander use, pin assignment or
fuses — the fitter and assembler stages, which also still need the routing encoding and the
option-fuse meanings from the reversing side.

## A wrong synthesis result caught, and its two causes (2026-11-26)
The tutorial's `speed_ch` first synthesised to equations that ignored its input. Causes, both
mine: AHDL names are case-insensitive (`Accel_in` declared, `accel_in` used) and my evaluator
treated the two as different; and it silently read any unknown signal as 0. Fixed: the lexer
lower-cases names, and an unknown signal is now an error. Regression test added. `speed_ch` now
gives next-state equations that depend on `accel_in`, as its source says. 19 of the 38 tutorial
designs synthesise; the rest need hierarchy or library macrofunctions (8), exceed the
exhaustive method's 14-signal limit (6), or use BIDIR / machine ports / one unhandled
construct (5).

## Synthesis limit now per function (2026-11-26)
`Design.dependencies()` derives, from the statements' structure, which inputs and register
outputs each driven signal can depend on (bit-by-bit for bitwise group expressions; every
enclosing IF / CASE / TABLE condition included; resolved through intermediate nodes).
`synth.synthesise` tabulates each function over its own support only, with all other signals
held at 0, so the 14-signal limit applies to one function instead of the whole design. A
24-input design whose outputs each depend on three inputs now synthesises. 21 of the 38 tutorial
designs synthesise; four still have single functions wider than the limit (16–19 signals), which
needs a symbolic method instead of tabulation.

## AHDL: inline primitives (2026-11-26)
The evaluator now rewrites inline `DFF(d, clk, clrn, prn)`, `DFFE`, `TFF`, `TFFE` calls into
anonymous registers, drops the transparent buffer primitives (LCELL, MCELL, SOFT, GLOBAL, WIRE),
turns `EXP(a)` into `!a`, and evaluates groups that mix constants and signals such as
`(0, !at_zero)`. The tutorial's switch debouncer (an 8-bit down-counter preset to 255) now
simulates and emits its single pulse on the 254th clock after the key is pressed, which is what
its source describes. Tutorial coverage: 27 of 38 simulate, 23 synthesise. The remaining eleven
all need hierarchy (instances of other designs or of library macrofunctions such as `4count`,
`8count`, `74151`), tri-state/BIDIR, or machine ports.

## AHDL: hierarchy flattened (2026-11-26)
`ahdl_eval.flatten()` expands instances of other AHDL designs — both `u : other;` variables with
`u.port` references and inline calls `other(a, b)` (argument order from the FUNCTION prototype,
else the callee's port order) — into one flat design, prefixing the instance's names and turning
its ports into nodes; unconnected inputs take their declared default. Designs are found through
a resolver (`directory_resolver` looks for `<name>.tdf` beside the file). Checked on my own
two-level example (two half adders and a registered bit). Tutorial coverage is unchanged at 27
simulating / 23 synthesising: what remains needs the vendor's library macrofunctions (`4count`,
`8count`, `74151`, which ship as compiled library files, not AHDL), tri-state and BIDIR pins,
or state machines passed through ports.
Also noted: a signal named `x` clashes with AHDL's don't-care `X`; my parser treats a bare `x`
as the don't-care, which is wrong for a design that really names a signal `x`.

## AHDL: tri-state, BIDIR pins, and the `x` clash (2026-11-26)
- `pin = TRI(in, oe)` (as a whole right-hand side) and BIDIR ports are modelled: the pin shows
  the buffer's input when the enable is high, otherwise whatever the outside drives; a BIDIR pin
  nothing drives is an input. `Simulator.set(pin=…)` sets the outside value. Synthesis gives such
  a pin two equations, data and `.oe`, and feedback from the pin appears as `pin@ext`.
- A bare `X` is now a don't-care only where no signal is called `x`; a design may name a signal
  `x` again. (The parser used to hard-wire the don't-care reading.)
Tutorial coverage: 28 of 38 simulate, 24 synthesise. Left: library macrofunctions (5 designs),
machine ports (4), a BIDIR port on an instantiated design (1).

## Routing fields against what the product terms need (2026-11-26)
Taking block input m to feed array columns 2m and 2m+1, I tabulated each routing field's
pattern against whether any product term in that block uses those columns:
| chip | needed by a term | not needed |
|---|---|---|
| EPM7032 (2 fuses) | 00 ×25, 01 ×5, 11 ×4 | 00 ×9, 01 ×11, 10 ×4, 11 ×14 |
| EPM7064 (3 fuses) | 111 ×42, 101 ×27, 011 ×7, 110 ×4 | 011 ×31, 111 ×31, 110 ×2 |
| EPM7160E (4 fuses) | 1111 ×220, 1110 ×19, 1011 ×18, 1101 ×10, 0111 ×8 | 1111 ×85 |
What this supports: on the EPM7160E every programmed field (55) is one the terms need, and with
the two other column mappings I tried (c mod 36, mirrored) that is not so — evidence for the
2m / 2m+1 column pairing on that device.
What it leaves unexplained: 220 inputs that terms need have a wholly unprogrammed field. Either
an unprogrammed field still delivers a signal (so all-ones is a selection, though the
architecture file lists no source for that value), or the routing rows of that image are
incomplete. On the EPM7064 the picture is worse (pattern 011 sits mostly on inputs no term
needs), so the column pairing or the field model is wrong there. In the device the EPM7160E's
routing fuses are five short rows (177–181) per group, four for a field and one for the E
family's extra per-input fuse.
I cannot resolve the routing encoding from these three images. It needs either the
router's code or a design compiled by the original software with its report file.

## Routing: two more readings tested (2026-11-26)
A binary select has no "unused" state, so an unprogrammed field must select something. On the
EPM7160E image, for the 220 inputs that terms need but whose field is all ones:
- "code = value + 1" (all ones = value 14): 77 of them have no value-14 source. Rejected.
- "code = inverted value" (all ones = value 0): every field has a value-0 source, a macrocell
  feedback; 206 of the 220 would come from macrocells that carry logic and 14 from idle ones.
  Not contradicted, but under it the design would use only values 0, 1, 2, 4, 8 — still the
  one-fuse-at-most pattern, which a free binary choice would not produce.
The E family's extra per-input fuses (one in each field's fifth row, one in a separate block)
are all unprogrammed in this image. Still unresolved; I am leaving the routing question until
there is new information (router code or a reference design).

## Programmed-fuse density by region (2026-11-26)
| chip | array (80-cell rows) | options (16-cell rows) | routing (16-cell rows) |
|---|---|---|---|
| EPM7032 | 17.3% | 46.7% | 61.1% (rows 100–105) |
| EPM7064 | 22.1% | 52.9% | 16.4% (rows 128–226) |
| EPM7160E | 18.9% | 56.6% | 3.0% (rows 129–181) |
The short rows that hold option fuses read with a healthy density on all three chips, so short
rows as such are not being misread. The routing rows of the two larger chips are sparse, and
they are also exactly the rows numbered 128 and up, which use the second row-select chain. I
cannot tell from the images whether those rows are genuinely sparse or are being read
incompletely; both the emulated vendor module and the native code read them the same way. On
the EPM7064 a field's three fuses are three consecutive rows (128–130 for block 1, 160–162,
192–194, 224–226), so "one programmed fuse per field" means one row out of three per input.
A way to test the read path on hardware: program a known pattern into an upper row of a
sacrificial chip and read it back — a write test, so it needs the owner's go-ahead.

## Upper rows read at full length: nothing hidden (2026-11-26, hardware, read-only)
On B9513 (native read path, 2.0 V margin) I read rows 100, 129, 147, 178–181 and 128 both as the
device file describes them (16 cells) and with all 80 clocks. In every row the 16-cell read
equals positions 0, 5, 10 … of the 80-clock read, and no other position holds anything but
erased cells. So the sparse routing rows are not sparse because data sits in positions the
16-cell read skips. Whether programmed bits in those rows fail to read at all is still open;
only a write-and-read-back test can tell.

## Option elements: evidence and hypotheses (2026-11-26)
Values per element on the three chip images (macrocell counts):
| element | EPM7032 (32) | EPM7064 (64) | EPM7160E (160) |
|---|---|---|---|
| 24 / 25 (terms 0, 1) | all 1 / all 1 | 54×1 / 53×1 | all 0 / 135×0 |
| 26 (term 2) | 20×1, 12×0 | 42×1, 22×0 | 123×1, 37×0 |
| 27 (term 3) | 20×0, 12×1 | 41×0, 23×1 | 142×0, 18×1 |
| 28 (term 4) | all 0 | all 0 | 136×0, 24×1 |
| 1 | all 0 | all 0 | 82×1, 78×0 |
| 6 | all 1 | 61×1, 3×0 | 146×0, 14×1 |
| 29 | 31×0 | 32×0, 32×1 | 102×0, 58×1 |
| 30 | 24×0, 8×1 | all 0 | 144×1, 16×0 |
| 31 (2 fuses) | 25×2, 7×3 | 32×2, 20×3, 12×0 | 145×2, 15×3 |
On the EPM7160E the common combinations are: term 3 = type 2 with literals and element 1 = 0
(77 macrocells); term 3 = type 5, empty, element 1 = 1 (51 macrocells).
Hypotheses, in decreasing confidence — none confirmed against a known design:
1. Type 2 on term 3 is the register's product-term clock and type 5 a clock enable used with
   a global clock; element 1 chooses between them (0 = product-term clock, 1 = global clock with
   enable). Fits asm.dll's case for element 1 (it tests two alternative register flags, 0x200
   and 0x100, and lists exactly these two virtual terms), fits the EPM7160E pattern (an empty
   enable term is always true), and matches the term roles of the ATF150x family, which is
   pin-compatible with these parts.
2. By the same analogy type 3 on term 2 would be asynchronous clear and type 4 on term 4
   asynchronous preset; type 1 (terms 0 and 1) the XOR / expander input.
3. Element 6 separates registered from combinational macrocells (asm.dll sets it from flag 0x40
   versus flags 0x300). Which value is which is NOT settled: the EPM7160E image suggests 0 =
   registered (146 macrocells, most with clock terms), but the EPM7032 image has element 6 = 1
   everywhere together with 19 macrocells whose term 3 is a type-2 term with literals, which
   reads oddly if 1 meant "no register".
Elements 29, 30, 31, 32 and the E family's 81–84, 67: only the flags they test are known.

## Resource fitting (2026-11-26)
`apu/fit.py` packs a synthesised design into a MAX 7000 device's macrocells and logic array
blocks: one macrocell per register and per output that is not just a register's output; five
product terms each, less one for each product-term clock, clear, preset or enable; wider sums
borrow whole macrocells through parallel expanders up to 20 terms; the input pin clocking most
registers becomes the global clock and the commonest single-pin clear the global clear; cells
are placed greedily so each block stays within 16 macrocells and 36 input signals.
`python -m apu fit x.tdf --device EPM7032` prints the placement. It is a resource check only:
no routing, pins or fuses, and the product-term roles it assumes are the hypotheses above.

## Pin assignment in the fit (2026-11-26)
`fit.assign_pins()` numbers the placed cells' macrocells (a cell's lenders take the numbers just
below it) and assigns pins from the device file's `pin_mc` table: outputs on their macrocell's
pin, the global clock and clear on the dedicated pins (43 and 1 on the 44-pin package, 83 and 1
on the 84-pin; output enables 44/2 and 84/2, which the device file's `oe_pin` table confirms),
other inputs on the pins of macrocells whose output is not brought out, then spare dedicated
inputs. `python -m apu fit` prints the pin list. User pin assignments, and checking them
against what the router can reach, are not implemented.

## Routing decode CONFIRMED on the EPM7032 (2026-11-26)
Test: on the real EPM7032, take the block inputs that product terms use, decode each field as a
two-fuse binary number, and list the I/O-entry sources selected.
- First fuse = high bit: the I/O entries used are 8, 16 and 18 (numbered from 0) — the pins of
  macrocells 9, 17 and 19, which are **exactly** the three macrocells whose element 34 is 0.
  All feedback sources used are macrocells that carry logic.
- First fuse = low bit: eight I/O entries are used, most of them pins of macrocells whose
  element 34 is 3.
So, for this device, all of these hold together: a routing field is a binary select, first fuse
high, over the architecture file's sources; block input m feeds array columns 2m and 2m+1; HIO
entry i (i < 32) is the pin of macrocell i + 1; and element 34 = 0 means the macrocell's output
driver is off (pin used as an input) while 3 means it drives (2 fuses, probably the two global
enables in between).
`fusemap.FuseMap(daf, maf).block_inputs()` implements it and `python -m apu fuses x.jed` now
prints EPM7032 product terms with signal names (`mcN` feedback, `pinN`, `inK` dedicated). Still
unknown there: which column of each pair is the complement (shown `.a` / `.b`).
Consequence for the larger chips: the same model gives contradictions on the EPM7064 image under
every bit order (pins of driving macrocells used as inputs; 28 needed inputs with no source),
and their routing rows are the sparse rows numbered 128 and up. With the model confirmed on the
EPM7032, the likelier explanation is now that **rows 128 and up are not being read back
completely on the EPM7064 and EPM7160E** (by the emulated vendor module and the native code
alike), which would also make the images of those chips, and the copy programmed into B9513,
incomplete in the routing rows.

## Upper rows in the signature read mode: no change (2026-11-26, hardware, read-only)
On B9513 I re-read rows 178–181 (and row 100 as a control) with the line set-up the module uses
for the signature row (port 3 bit 3 and port 0x10 bit 5 raised) instead of the ordinary one.
Every row returned exactly the same cells in both modes, and again afterwards in the ordinary
mode. So the read mode does not reveal further programmed bits in the upper rows. The question
whether those rows read back completely remains one for a write-and-read-back test.

## Packaging (2026-11-26)
`pyproject.toml` added: `pip install -e .` gives an `apu` command (same as `python -m apu`). The
package itself needs only pyusb; unicorn, pefile and capstone are needed only by the
emulator-based tools in `tools/`.

## Literal polarity and constant terms, from asm.dll (2026-11-26)
- asm.dll keeps the `.daf` header in globals from 0x10023720, which fixes the field order:
  total_bits, wl_offset, pia_wl_offset, pt_offset, mc_pt_offset, lab_offset, pia_offset,
  lab_pia_wls, pia_wls, pt_wls, arch1_bits, arch2_bits, n_pins, n_mcs, n_labs, n_data_bits, …
  (my earlier order had three of these swapped; n_data_bits at 0x3C is confirmed). A term's
  fuses start at (block − 1)·lab_offset + (mc_pterm + term·mc_pt_offset)·pt_offset.
- Product-term routine 0x10004c83(source, term address, word line, invert): source 1 fills the
  term's pt_wls fuses with 1, source −1 fills them with 0; otherwise it flips the word line's
  low bit when the source id is negative or the invert flag is set, and writes a 0 at
  term + word line · wl_offset. The caller's word line for a routed signal is 2 × its block
  input index (0x10004ed8). So: **programmed (0) = literal present; block input m uses column 2m
  for the true signal and 2m + 1 for its complement; a term with nothing programmed is the
  constant the code calls 1 (VCC) and a fully programmed term is its inverse (GND)**. Each
  shared expander has a single word line (table exp_wl).
`python -m apu fuses` now prints EPM7032 terms as real literals, e.g. macrocell 2 of the chip:
`!mc19 & !mc8 & !mc9 & !pin19 & !mc15 & !mc16`, its other four terms VCC.
An observation that fits together (hypothesis, not confirmed): macrocells like that one have one
term of all-inverted literals and VCC elsewhere. If term 0 there is the XOR input (type 1) and
the sum is the constant VCC, the output is the complement of that term — a wide OR of the six
signals done with a single product term, which is the classic De Morgan trick a fitter uses.
That would make type 1 the XOR term.

## The real EPM7032's design, decoded as far as it goes (2026-11-26)
With names and polarity in place the chip's 32 macrocells fall into clear groups (V = constant
true term, G = constant false; elements 24–28 written as five digits):
- **Unused** (13, 22, 26–30): `11010`, element 31 = 3, all five terms V. This is the blank
  pattern: an unused macrocell has term 2 and term 4 steered to their special inputs and
  nothing programmed.
- **One wide term** (2, 3, 5, 10, 11): `11010`, element 31 = 2, term 0 an AND of six to eight
  inverted signals, the rest V. Same steering as an unused cell.
- **`11100` cells** (the other twenty): term 2 in the sum position and term 3 steered away.
  Six of them (4, 18, 21, 23, 24, 25) have term 3 = `pin19` and a single signal on term 1 that
  chains them (32 → 25 → 24 → 18, 31 → 21, 4 → 23): they look like a shift register clocked
  from pin 19, with term 1 as data and term 3 as clock. Five (9, 14, 15, 16, 19) have
  term 2 = `mc12`, term 4 = `!mc20 & !mc12` and term 3 an AND of two shared expanders, as a
  register with product-term clear, preset and clock would.
So the term roles hypothesised earlier (term 3 clock, term 2 clear, term 4 preset) fit this
design too. What does not fit yet: element 6 is 1 in every macrocell, used or not, including
the ones that look registered, so "element 6 = register bypass" cannot be right as I stated
it; and in the chained cells term 0 is V while sitting in a position my table calls "sum or
type 1", which would force a sum to 1 unless term 0 is the XOR input there or is ignored.
Element 6, the XOR/invert control and the exact sum membership are the open points.

## Hypothesis: term 0 is the XOR input, element 31 selects it (2026-11-26)
Reading the decoded EPM7032 design for what would make every used macrocell meaningful:
- If the sum included term 0, the six chained cells (term 0 = VCC) would have a constant sum.
  If instead term 0 feeds the macrocell's XOR and the sum is the remaining sum-steered terms,
  every group works: the one-wide-term cells give the complement of their AND (a wide OR by
  De Morgan, with the sum at VCC from the unprogrammed terms); the chained cells give the
  complement of their single data signal (term 0 = VCC used purely to invert); unused cells
  give VCC xor VCC.
- Element 31 has two fuses and a four-way select in the device file (values 0–3); it is 3 on
  every unused macrocell and 2 on every used one on this chip (and takes 0 on twelve cells of
  the EPM7064). A two-fuse, four-way choice that differs between used and unused cells is what
  the XOR's second-input select would look like (the data sheet's macrocell has the XOR fed by
  a product term, a constant, or the register for T-type operation). asm.dll's case for
  element 31 tests register flags 0x20, 0x300 and 0x400 and picks select index 1–4.
So the working model for the EPM7032 is: XOR input = term 0 (type 1) when element 31 = 2;
sum = the terms steered to it among 1–4; term 2 clear, term 3 clock, term 4 preset when steered
away. Still hypothesis: no known design to check against. Element 6 (1 everywhere here), 29,
30, 32 remain unexplained; element 30 = 1 coincides with the cells that use shared expanders or
product-term clear/preset.

## The vendor library is mostly schematics (2026-11-26)
The user's MAX+PLUS II tree ships its macrofunction library as files, not compiled code:
`max2inc/` holds 307 prototype include files (`FUNCTION 4count (clk, clrn, setn, ldn, cin, dnup,
d, c, b, a) RETURNS (qd, qc, qb, qa, cout);` …) and `max2lib/mf/` holds 24 AHDL files, 306
schematic files (`.gdf`, a binary format starting `GDF\0`) and 289 symbol files; `max2lib/prim`
has 85 primitive symbols and `mega_lpm` the parameterised library.
The front end now reads these at run time from the user's copy: `INCLUDE "name"` loads the
prototypes (so inline calls get their argument order), instances are searched in the design's
directory and then the library directories, and `IF … GENERATE … ELSE GENERATE … END GENERATE`
is handled in VARIABLE sections and in the body, with `FAMILY_FLEX()` and the like false for a
MAX 7000 target and `USED(port)` taken as true. All 24 old-style library `.tdf` files parse.
But they are thin wrappers: `8count.tdf` chooses between `p8count` and `f8count`, and those,
like `4count`, exist only as `.gdf` schematics. So simulating the tutorial designs that use
`4count`, `8count` or `74151` needs a reader for the binary schematic format — the same thing
schematic entry needs. Not started. The parameterised `mega_lpm` AHDL (56 of 159 files parse)
uses more of the language than the parser handles (`?:`, `^`, `FOR … GENERATE` forms).

## Schematic files (.gdf): first look (2026-11-26)
- 8-byte header `GDF\0\0\0` + version 6, then a stream of records introduced by lower-case
  letter tags. Visible in a small tutorial file: `j` + two u16 (a size: the sheet, and later
  each symbol's box); `k` + four u16 + one u16 (a line: x1 y1 x2 y2 and an id); `v` + u16
  length + NUL-terminated text (font strings such as "Arial,10", node names such as
  "ticket[3..0]", symbol names such as "NOR2", "INPUT", "VCC"); `q`, `i`, `l`, `n`, `o`, `p`,
  `r`, `h` carry positions and attributes; `f` … `g` appear to bracket a symbol.
- The module that reads and writes them is `gio.dll` (276 exports). Its export names give the
  object model: a drawing holding symbols, each with graphics, pin stubs and text; lines with a
  node id, a wire id and a pin-stub reference (`gio_line_get_node_id`, `_get_wire_id`,
  `_get_pinstub_rec_id`) — so connectivity is stored, not only geometry. Entry points are
  `gio_read_drawing` (0x100167e0) and `gio_write_drawing`. Its debug-print routines have format
  strings without field names, so the record layouts have to come from the reader's code.
Not started beyond this. A netlist extractor for `.gdf` would unlock the library
macrofunctions (`4count`, `8count`, `74151` exist only as schematics) and schematic entry.

## Schematic files parsed (2026-11-26)
The record layouts came from gio.dll's reader. `ged_u_io_read_label(tag)` consumes a tag and
`ged_u_io_is_label(tag)` only tests it, so a record read after `is_label` starts with its own
tag byte. Layouts:
- header record `e` + four bytes; `f` + `j` w h starts a graphic;
- lines `k` / `l` / `m`: x1 y1 x2 y2 (i16) + u16; `i` marks a named line and is followed by its
  name text; arc `n`: six i16 + four u16; circle `o`: two i16 + two u16; pin stub `p`: two i16;
- text `q`: u8 type, u8 font, i16 x, i16 y, u16 attributes; if font ≥ 4 a font string and two
  i16; else in a graphic or `u` list two i16 when the type is 0x32 or 0x37; then text and
  alternative text; in a `u` list a trailing u16. Strings: `v` + u16 length + bytes + NUL, or
  `s` + u8 length + bytes + NUL (the length excludes the NUL — my first version had that wrong);
- `g` opens the symbol section: per symbol a graphic, then per instance `r` x y orientation,
  `h` and its texts, optionally `u` and more texts; `h` at top level holds sheet texts; `t` ends.
`apu/gdf.py` parses **all 410 `.gdf` files in the vendor tree to the terminator with no bytes
left over** (81,032 lines, 84,750 texts, 16,287 symbol instances, 8,132 pin stubs).
What the records mean, from the tutorial and library files: text type 1 is a symbol's name,
2 and 3 its input and output pin names (each followed by the pin stub's position), 6 a node
name on a wire, 41 an instance's number. `gdf.symbols()` and `gdf.wires()` return these.
The old-style library is built from about forty primitive symbols — INPUT, OUTPUT, AND2…AND8,
OR, NAND, NOR, XOR, XNOR, NOT, the B-prefixed bubble-input gates, DFF, DFFE, TFF, JKFF, LATCH,
TRI, SOFT, LCELL, CARRY, CASCADE, EXP, BIDIR. `4count.gdf` is 60 such symbols and 312 wires.
Not done: the netlist — joining wires, pin stubs and names into nets — and turning it into
logic for the simulator and synthesiser.

## Schematic netlist: first attempt does not work yet (2026-11-26)
`gdf.to_ahdl()` joins wires at shared endpoints, attaches pin stubs, and emits AHDL. On the
library's `4count.gdf`: all 186 pins land exactly on wire ends with the stored coordinates (only
60 with the y axis flipped, so the geometry is right), no wire in that file ends on another
wire's interior, and the netlist has no undriven or doubly driven nets. The DFF symbol's own
labels confirm pin 1 = Q, 2 = D, 3 = clock, 4 = CLRN, 5 = PRN. But the result is wrong: the
converted counter loads a value yet does not count, and its flip-flops' CLRN pins come out
driven by an XOR, which looks like the D logic. Cause not found. Suspects: the meaning of the
line records' style word (low byte values 0–3 plus bits 6 and 7 are spread evenly over all
line ends, so they are not simply junction dots), the `l` versus `k` line types, or how my
simulator treats asynchronous clear and preset together.

## Schematic netlists work; library macrofunctions simulate (2026-11-26)
The converted `4count` was wrong because a symbol's pins are stored in drawing order, not by
number, and my converter mapped primitive inputs by position in the file. Sorting by pin number
fixes it; the DFF mapping is 2 = D, 3 = CLK, 4 = CLRN, 5 = PRN (chosen because CLRN low then
clears the counter; the other order sets it). The converted `4count` counts up and down, gives
carry-out at 15 and 0, loads, holds with carry-in low and clears — the behaviour of the TTL
part it models.
The library search now converts a `.gdf` when no `.tdf` of that name exists. Two front-end gaps
fixed on the way: a bare `X` inside a flattened instance is the don't-care again, and
assignments with empty positions or group arguments spread over single-bit ports
(`(q[3..0], ) = 4count(clk, …)`, `16dmux(q[3..0])`) are split bit by bit.
Results: 220 of the 306 library schematics convert and construct (failures: latches, JK
flip-flops, rotated symbols, buses, a few others). Of the 40 tutorial files (38 AHDL, 2
schematic), 33 simulate and 28 also synthesise and fit an EPM7128. `macro2` — the library's
4-bit counter driving its 4-to-16 decoder, both converted from schematics — walks one output
high per clock through all sixteen and wraps. Not compared with the vendor simulator.
Left: buses, rotated symbols, latches and JK flip-flops in schematics; machine ports; a TRI
variable; BIDIR ports on instances.

## A vendor reference after all: the tutorial's compiled `chiptrip` (2026-11-26)
The tutorial directory holds the original compiler's and simulator's output for `chiptrip`:
`chiptrip.rpt` (MAX+PLUS II 10.0, target **EPM7032LC44-6**), `.fit`, `.snf`, `.acf` and
`chiptrip.tbl`, the simulator's vector table. No POF, but:
- **Simulation reference.** `apu/tbl.py` reads `.tbl` files and replays their inputs into the
  native simulator. `python -m apu sim chiptrip.gdf chiptrip.tbl`: the schematic (converted,
  with its four sub-designs and the library's `8count` → `p8count` schematic) matches the vendor
  simulation in **all 80 settled values**: `at_altera`, `ticket[3..0]`, the buried 8-bit
  counter and both state machines' state names. Altering two expected values makes the check
  report exactly those two, so it does discriminate. Functional only; the vendor table also
  shows delays (e.g. 2 ns clock-to-output), which are not modelled.
- To get there the schematic converter learned user-defined symbols (name text kind 19) and
  buses (a bus pin, port or wire name such as `dir[1..0]` becomes an AHDL group).
- **Fitter reference** (for later): 21 logic cells, 18 shareable expanders, global clock on pin
  43, clear `!reset` taken from an ordinary pin, XOR used with an expander-product second input
  (`DFFE(_EQ013 $ _EQ014, GLOBAL(clock), !reset, VCC, VCC)`), Turbo and Security bits ON,
  pin ↔ logic cell assignments (pin 4 = LC1 … pin 41 = LC17), which agree with the device
  file's `pin_mc` table for all 18 pins the design uses.

## Application window, and T flip-flops in synthesis (2026-11-26)
- `python -m apu gui [project dir]` (`apu/gui.py`, standard library only): a local server on
  127.0.0.1 and a page in the browser with the project's files, a source viewer (schematics are
  shown as the AHDL they convert to), Compile (equations, fit, pins), Simulate (waveform, and
  comparison with a `.tbl` when one is chosen) and a read-only Programmer panel (adapter and
  silicon ID). It never writes to a chip. Tested through its HTTP API (tests/test_gui.py); the
  page's script passes `node --check`; not yet looked at in a browser.
- Synthesis now keeps a register's toggle form (T flip-flop: t = next ⊕ q) when that needs fewer
  product terms, as the original compiler does for counters (TFFE in chiptrip.rpt). The
  tutorial's 8-bit `time_cnt` now fits in 8 macrocells of one product term each — the same as
  the vendor report — instead of 12 with expanders.
- Whole-`chiptrip` synthesis still stops: one register of the library's `p8count` depends on 15
  signals, over the exhaustive method's 14.

## PLMQ7128/7160-160 and the EPM7160EQC160 (2026-11-26)
- Adapter ID 36. The native adapter read returned 0 or 0x7F on this adapter because it skipped
  the module's first step (opcode-5 write F7 to port 0, then ~31 no-ops); fixed in
  `board.adapter_id`.
- Silicon ID through the vendor module: `ALTERA93`; full read-back (170 lines) → 74773 fuses,
  10.8 % programmed, 96 of 160 macrocells with logic.
- Native path: this adapter puts most lines on separate ports, so its line read has the same
  structure as the 84-pin part (110-clock flush, marker, row clocks, sixteen precharge units,
  read loop with an interlude every 16 cells) but different ports and nine port reads per cell.
  Aligning the two at the level of individual line changes gives a consistent one-to-one map
  (e.g. read clock 03.7 → 10.6, row clock 03.0 → 12.7); two adapter quirks (an always-on bit 7
  on port 0x14, port 0x10 bit 7 constant) meant transcribing rather than translating.
  `max7000.line_read_ops_160` matches all twelve captured module reads op for op; cell bits:
  9 = 10.2, 8 = 05.0, 7 = 11.1, 6 = 0F.6, 5 = 0F.3, 4 = 04.4, 3 = 13.6, 2 = 16.0, 1 = 14.2,
  0 = 0D.2 (by single-bit probing in simulation). Set-up, supply release and drive table
  recorded from the real unit and matched op for op.
- On the chip: native `apu id --part EPM7160EQC160` gives `ALTERA93`; native `apu read` returns
  all 170 lines **identical in all ten bits** to the vendor-module image (8416 cells).
