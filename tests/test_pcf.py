import pathlib
import unittest

from apu.pcf import KIND_ID, Pcf

V = pathlib.Path(__file__).resolve().parent.parent / "vendor/maxplus2-10.2/7k"


class SignatureRow(unittest.TestCase):
    # Row 0 of a real EPM7160ELC84-15 as a line read returns it: data in every fifth cell.
    DATA = [0xF0, 0xF7, 0xE8, 0xFE, 0xE8, 0xF2, 0xF4, 0xF9, 0xF0, 0xE7, 0xE8, 0xE8, 0xE1, 0xE3, 0xE8, 0xEE]

    def cells(self):
        row = [0x3FF] * 80
        for i, v in enumerate(self.DATA):
            row[5 * i] = 0x300 | v
        return row

    def test_id_and_parameter_bytes(self):
        from apu import max7000
        cells = self.cells()
        self.assertEqual(max7000.silicon_id(cells), "ALTERA93")
        # the two bytes the vendor module's own ID read returned after the ID on this chip
        self.assertEqual([max7000.signature_byte(cells, k) for k in (8, 9)], [0xD7, 0x80])


class LineRead(unittest.TestCase):
    # sha256 prefixes of the port operations the vendor module issued for the same read
    # (simulation, tools/line_ops.py), delays excluded
    REFERENCE = {0: "dfb5d9b00a661a83", 1: "c8a55bf832ee59a1", 5: "2f698bf92863c6c6", 0x69: "655c7f2eda82fe33"}

    def test_operations_match_the_module(self):
        import hashlib
        from apu import max7000
        for row, digest in self.REFERENCE.items():
            ops = [o for o in max7000.line_read_ops(row) if o[0] != "N"]
            self.assertEqual(hashlib.sha256(repr(ops).encode()).hexdigest()[:16], digest, hex(row))

    def test_repeat_read_releases_lines_first(self):
        from apu import max7000
        first, again = max7000.line_read_ops(5), max7000.line_read_ops(5, first=False)
        self.assertEqual(again[:4], [("W", 0x14, 0x0C), ("W", 0x07, 0), ("W", 0x03, 0), ("W", 0x10, 0)])
        self.assertEqual(again[4:], first[3:])

    def test_sixteen_cell_rows_and_upper_row_chain(self):
        from apu import max7000
        short = max7000.line_read_ops(90, first=False, cells=16)
        full = max7000.line_read_ops(90, first=False)
        self.assertEqual(sum(o[0] == "R" for o in short), 16 * 7)
        self.assertEqual(short, full[:len(short)])                  # a short line is a truncated one
        upper = max7000.line_read_ops(169, first=False, cells=16)
        self.assertIn(("W", 0x11, 0x10), upper)                      # rows 128+ use the second chain
        self.assertNotIn(("W", 0x11, 0x04), upper)
        clocks = lambda ops: sum(o == ("W", 0x03, 0x01) for o in ops)
        self.assertEqual(clocks(upper), 110 + 1 + 41)
        self.assertEqual(max7000.cells_from_reads(bytes.fromhex("20000000000000") + bytes(7 * 15), 16)[15], 1)

    def test_cells_from_reads(self):
        from apu import max7000
        # bytes read on the real chip for the first three clocks of the signature row
        reads = bytes.fromhex("0a810820300800" "08810820200800" "2a810820200000") + bytes(7 * 77)
        cells = max7000.cells_from_reads(reads)
        self.assertEqual([cells[75] & 0xFF, cells[70] & 0xFF, cells[65] & 0xFF], [0xEE, 0xE8, 0xE3])


@unittest.skipUnless((V / "m7032.maf").exists(), "vendor files not extracted")
class Maf(unittest.TestCase):
    def test_every_file_is_consumed_exactly(self):
        from apu import maf
        files = sorted(V.glob("*.maf"))
        self.assertGreater(len(files), 20)
        for path in files:
            self.assertEqual(maf.parse(path.read_bytes()).trailing, 0, path.name)
        self.assertRaises(ValueError, maf.parse, b"nope")

    def test_epm7032_routing(self):
        from apu import maf
        m = maf.parse((V / "m7032.maf").read_bytes())
        self.assertEqual({n: f[1] for n, f in m.blocks}, {"MCELL": 16, "GH": 68, "PIA": 36, "HIO": 36})
        fields = maf.routing(m)
        # 36 inputs for each of two blocks, two fuses each, filling the 144 fuses the assembler
        # file leaves between the macrocell options and the output-enable selects
        self.assertEqual(sorted(fields), list(range(14464, 14608, 2)))
        self.assertTrue(all(width == 2 and 3 <= len(sources) <= 4 for width, sources in fields.values()))
        kinds = {kind for _, sources in fields.values() for kind, _ in sources.values()}
        self.assertEqual(kinds, {"mc", "hio"})
        big = maf.routing(maf.parse((V / "m7064.maf").read_bytes()))
        self.assertEqual((len(big), {w for w, _ in big.values()}), (144, {3}))


class LineRead7064(unittest.TestCase):
    # (row, cells, sha256 prefix) of the vendor module's traffic for eleven consecutive EPM7064
    # bulk reads in simulation (margin set-up), delays included; row 124 is the signature row
    REFERENCE = [(124, 80, '4a451307779329b5'), (90, 16, '742ac5c1a9092b68'), (116, 80, '0db72ee8c72ae9ae'), (128, 16, '0f6aa8644ad7201d'), (129, 16, '00cbd58a19160151'), (135, 16, 'a26c6a31849706b2'), (160, 16, '227ba75caf15eb20'), (192, 16, '134ff2cc16b4e850'), (224, 16, '5c0bfeaf991b0f96'), (231, 16, '4796b831d2d0f9a0'), (5, 80, '85906d0c05328f94')]

    def test_consecutive_reads_match_the_module(self):
        import hashlib
        from apu import max7000
        first, hi = True, 0
        for row, n, digest in self.REFERENCE:
            ops = max7000.line_read_ops_7064(row, first, n, hi, signature=(row == 124))
            first, hi = False, max7000.hi15_after_7064(row, hi)
            self.assertEqual(hashlib.sha256(repr(ops).encode()).hexdigest()[:16], digest, (row, n))

    def test_decode(self):
        from apu import max7000
        reads = bytearray(4 * 80)
        reads[4 * 7:4 * 7 + 4] = bytes([0x20, 0x08, 0x10, 0x01])       # clock 7: all four bits
        cells = max7000.cells_from_reads_7064(bytes(reads))
        self.assertEqual([i for i, v in enumerate(cells) if v], [40])
        self.assertEqual(cells[40], 0xF)
        row = [0xF] * 80
        for j, bit in enumerate(reversed([0, 1, 0, 0, 0, 0, 0, 1])):    # 'A' = 0x41 in cells 0..7
            row[j] = 0xE | bit
        self.assertEqual(max7000.signature_byte_7064(row, 0), 0x41)


class LineRead7032(unittest.TestCase):
    # (row, cells, sha256 prefix) of the vendor module's traffic for nine consecutive EPM7032 bulk
    # reads in simulation, delays included; row 124 is the signature row
    REFERENCE = [(124, 80, 'fc02fef1e869c9f3'), (0, 80, 'cdd0bb087b9cd505'), (1, 80, '2a0fc8ad57f80477'), (2, 80, '1914d182c8c8a549'), (5, 80, 'bfb3f1fc181eefb4'), (113, 16, '992b0f85510438e9'), (64, 80, '5114ef4071d76a40'), (116, 80, '397c50d1b617e0dd'), (1, 80, '2a0fc8ad57f80477')]

    def test_consecutive_reads_match_the_module(self):
        import hashlib
        from apu import max7000
        previous = None
        for row, n, digest in self.REFERENCE:
            ops = max7000.line_read_ops_7032(row, previous is None, n, previous or 0, signature=(row == 124))
            previous = row
            self.assertEqual(hashlib.sha256(repr(ops).encode()).hexdigest()[:16], digest, (row, n))

    def test_address_and_decode(self):
        from apu import max7000
        self.assertEqual(max7000.row_address_7032(1), (2, 0x00))
        self.assertEqual(max7000.row_address_7032(2), (1, 0x00))
        self.assertEqual(max7000.row_address_7032(124), (0, 0xF8))
        reads = bytearray(80)
        reads[7] = 0x18
        cells = max7000.cells_from_reads_7032(bytes(reads))
        self.assertEqual([(i, v) for i, v in enumerate(cells) if v], [(40, 3)])


@unittest.skipUnless((V / "m7032044.daf").exists(), "vendor files not extracted")
class DafFiles(unittest.TestCase):
    def test_every_file_is_consumed_exactly(self):
        from apu import daf
        files = sorted(V.parent.glob("*/*.daf"))
        self.assertGreater(len(files), 100)
        for path in files:
            self.assertEqual(daf.parse(path.read_bytes()).trailing, 0, path.name)

    def test_epm7032_layout(self):
        from apu import daf, pcf
        d = daf.parse((V / "m7032044.daf").read_bytes())
        info = daf.summary(d)
        self.assertEqual((info["fuses"], info["pins"], info["macrocells"], info["labs"]), (14672, 44, 32, 2))
        self.assertEqual((info["pt_wls"], info["pterms_per_mc"], info["lab_offset"]), (88, 5, 7040))
        self.assertEqual((d.named()["mc_pt_offset"], d.named()["pt_offset"], d.named()["wl_offset"]), (88, 1, 1))
        groups = daf.fuse_groups(d)
        self.assertEqual(groups[0][2][1], (14080,))                 # first option fuse of macrocell 1
        self.assertEqual(groups[0][2][2], (14092,))                 # twelve option fuses per macrocell
        self.assertEqual(sum(1 for _ in pcf.Pcf.load(V / "m7032044.pcf").fuses()), info["fuses"])


@unittest.skipUnless((V / "m7032044.daf").exists(), "vendor files not extracted")
class FuseMapTest(unittest.TestCase):
    def test_structure_of_a_synthetic_design(self):
        from apu import daf, fusemap
        d = daf.parse((V / "m7032044.daf").read_bytes())
        fmap = fusemap.FuseMap(d)
        self.assertEqual((fmap.macrocells, fmap.terms, fmap.inputs), (32, 5, 88))
        fuses = [1] * fmap.fuses
        fuses[440 * 2 + 88 * 1 + 7] = 0                  # macrocell 3, term 1, column 7
        fuses[440 * 2 + 88 * 1 + 73] = 0                 # ... and a shared-expander column
        for c in range(88):
            fuses[440 * 2 + 88 * 4 + c] = 0              # term 4 fully connected
        fuses[14080 + 12 * 2] = 0                        # first option fuse of macrocell 3
        self.assertEqual(fmap.product_terms(fuses, 3), [None, [7, 73], None, None, "all"])
        self.assertEqual(fmap.option_bits(fuses, 3)[24], 0)
        self.assertEqual(fmap.option_bits(fuses, 2)[24], 1)
        rest = fmap.other_fuses()
        self.assertEqual((rest[0], rest[-1], len(rest)), (14464, 14607, 144))
        self.assertIn("]: !in3 & exp1", fmap.report(fuses))
        # term 3: element 27 = 1 means the sum; 0 means type 2 or 5, and element 1 picks which
        self.assertEqual(fmap.term_roles(fuses, 1)[3], [0])
        fuses[14080 + 3] = 0
        self.assertEqual(fmap.term_roles(fuses, 1)[3], [5])
        fuses[14080 + 5] = 0
        self.assertEqual(fmap.term_roles(fuses, 1)[3], [2])
        fuses[14080 + 3] = fuses[14080 + 5] = 1
        from apu import maf
        named = fusemap.FuseMap(d, maf.parse((V / "m7032.maf").read_bytes()))
        erased = [1] * named.fuses
        inputs = named.block_inputs(erased, 1)
        self.assertEqual(len(inputs), 36)
        self.assertTrue(all(n == "?" or n.startswith(("mc", "pin", "in")) for n in inputs))
        programmed = list(erased)
        programmed[14464] = programmed[14465] = 0            # field 0 of block 1 set to value 0
        self.assertNotEqual(named.block_inputs(programmed, 1)[0], inputs[0])
        self.assertEqual(named.block_inputs(programmed, 1)[1:], inputs[1:])
        self.assertIsNone(fmap.block_inputs(erased, 1))       # without the architecture file
        big = fusemap.FuseMap(daf.parse((V / "m7064084.daf").read_bytes()),
                              maf.parse((V / "m7064.maf").read_bytes()))
        self.assertIsNone(big.block_inputs([1] * big.fuses, 1))   # 3-fuse fields: not established
        self.assertEqual((big.macrocells, big.terms, big.inputs, big.starts[:3]), (64, 5, 88, [0, 440, 880]))
        self.assertEqual(big.starts[16], 7040)                       # first macrocell of block 2
        self.assertEqual(big.pin_of[16], 4)


class LineRead160(unittest.TestCase):
    # (row, cells, first read?, sha256 prefix) of the vendor module's traffic for twelve line
    # reads of an EPM7160EQC160 in simulation, delays included; row 0 is the signature row
    REFERENCE = [(0, 80, True, '8d3e4cc5e7869173'), (1, 80, False, 'e45ec05e5cc3ad3d'), (2, 80, False, '4f59a02c93d98985'), (5, 80, False, '2b8ab059e0638a7f'), (88, 80, False, '1ddf28d70ac32ace'), (89, 16, False, '65779eb3bc0c66fb'), (90, 16, False, 'a94599f9ee5b3ed4'), (128, 80, False, 'ea55b01b8a36b15e'), (129, 16, False, '6eeab2ed70532e52'), (169, 16, False, 'e1bc1c4340aa09c3'), (192, 16, False, '59e0ed6f286395d9'), (1, 80, False, 'e45ec05e5cc3ad3d')]

    def test_reads_match_the_module(self):
        import hashlib
        from apu import max7000
        for row, n, first, digest in self.REFERENCE:
            ops = max7000.line_read_ops_160(row, first, n, signature=(row == 0))
            self.assertEqual(hashlib.sha256(repr(ops).encode()).hexdigest()[:16], digest, (row, n))

    def test_decode(self):
        from apu import max7000
        reads = bytearray(9 * 80)
        reads[9 * 7:9 * 8] = bytes([0x10, 0x01, 0x04, 0x48, 0x04, 0x02, 0x40, 0x04, 0x01])   # all ten bits
        cells = max7000.cells_from_reads_160(bytes(reads))
        self.assertEqual([(i, v) for i, v in enumerate(cells) if v], [(40, 0x3FF)])


class LineWrite(unittest.TestCase):
    # (row, cells, sha256 prefix) of the vendor module's traffic for four consecutive line writes
    # in simulation, each cell i holding 0x300 | (i + 1); delays included
    REFERENCE = [(5, 80, '95c6c9b18f38d407'), (90, 16, '017a56a89a6f2c90'), (0, 80, 'f3cb0b1ce5cc0de1'), (128, 80, '8c6e17b2802a811f')]

    def test_consecutive_writes_match_the_module(self):
        import hashlib
        from apu import max7000
        writer = max7000.LineWriter()
        for row, n, digest in self.REFERENCE:
            ops = writer.load_ops(row, [0x300 | (i + 1) for i in range(n)])
            self.assertEqual(hashlib.sha256(repr(ops).encode()).hexdigest()[:16], digest, (row, n))

    def test_program_flow_pulses_each_line_once(self):
        from apu import max7000, programmer
        sent, naps = [], []
        real = programmer._send, programmer.read_setup_epm7160e
        programmer._send = lambda apu, ops: sent.append(list(ops)) or b""
        programmer.read_setup_epm7160e = lambda apu, **kw: sent.append(("setup", kw))
        try:
            programmer.program_epm7160e(None, {3: [0x3FE] * 80, 90: [0x3FF] * 16}, tprog_ms=60, vpp_mv=12250,
                                        sleep=naps.append)
        finally:
            programmer._send, programmer.read_setup_epm7160e = real
        self.assertEqual(sent[0], ("setup", {"vcc_mv": 5000, "vpd_mv": 12250, "vmarg_mv": 0}))
        self.assertEqual(naps, [0.06, 0.06])
        strobe = lambda ops: [v & 0x10 for k, p, v in ops if p == 0x0D][-1]
        self.assertEqual([strobe(o) for o in sent[1:]], [0x10, 0, 0x10, 0])     # on, off, on, off

    def test_erase_sequences(self):
        from apu import max7000
        start = max7000.erase_start_ops()
        self.assertEqual([o for o in start if o[0] != "N"][-6:],
                         [("W", 0x0D, 0x10), ("W", 0x0D, 0x12), ("W", 0x0C, 0x00), ("W", 0x01, 0xC0),
                          ("W", 0x01, 0x00), ("W", 0x14, 0x08)])
        self.assertIn(("W", 0x0C, 0x80), max7000.erase_start_ops(signature_row=True))
        self.assertEqual(max7000.erase_end_ops()[-1], ("W", 0x07, 0x00))

    def test_pulse_is_ended_before_the_next_line(self):
        from apu import max7000
        writer = max7000.LineWriter()
        first = writer.load_ops(1, [0x3FF] * 80)
        self.assertEqual(first[-2], ("W", 0x0D, 0xB1))          # strobe bit raised last
        second = writer.load_ops(2, [0x3FF] * 80)
        self.assertEqual(second[1], ("W", 0x0D, 0xA1))           # and dropped first
        self.assertEqual(writer.end_pulse_ops()[1], ("W", 0x0D, 0xA1))


@unittest.skipUnless((V / "m7064084.pcf").exists(), "vendor files not extracted")
class RealFiles(unittest.TestCase):
    def test_7064(self):
        p = Pcf.load(V / "m7064084.pcf")
        self.assertEqual(p.variants, 4)
        self.assertEqual(p.nominal_vcc(), 5000)
        self.assertEqual(p.siid_volts(), (5000, 0, 12000, 0))
        self.assertTrue(p.id_text.startswith("ALTERA93"))
        ids = [r for r in p.regions if r.kind == KIND_ID]
        self.assertEqual((ids[0].row, ids[0].col, ids[0].count, ids[0].mask), (0x7C, 0, 8, 0xFF))
        self.assertEqual(p.fuse_count, 29488)

    def test_variant_blocks(self):
        blocks = Pcf.load_all(V / "m7064084.pcf")
        self.assertEqual(len(blocks), 4)
        self.assertEqual([b.base for b in blocks], [8, 11788, 23526, 38086])
        self.assertEqual([b.nominal_vcc() for b in blocks], [5000, 5000, 3300, 2500])
        self.assertEqual(blocks[1].siid_volts(), (5000, 2000, 10000, 0))
        self.assertEqual(len(Pcf.load_all(V / "e7160084.pcf")), 2)

    def test_fuse_map_is_injective_and_in_range(self):
        for name in ("m7064084.pcf", "e7160084.pcf", "m7128084.pcf"):
            p = Pcf.load(V / name)
            cells = set()
            for r in p.regions:
                if r.kind != 0x100:
                    cells |= {(r.row + i, r.col + c) for i in range(r.n) for c in range(r.count)}
            seen = set()
            for _, row, col, mask in p.fuses():
                self.assertIn((row, col), cells, name)
                seen.add((row, col, mask))
            self.assertEqual(len(seen), p.fuse_count, name)
            self.assertEqual(p.fuse_count, p.declared_fuses, name)


if __name__ == "__main__":
    unittest.main()
