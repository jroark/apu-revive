import pathlib
import struct
import unittest

from apu import gdf

VENDOR = pathlib.Path(__file__).resolve().parent.parent / "vendor/maxplus2-10.2"


def vstr(text):
    return b"v" + struct.pack("<H", len(text)) + text.encode() + b"\0"


def build():
    """A tiny drawing: one named wire and one two-pin symbol placed once."""
    d = b"GDF\0\0\0\x06\x00" + b"\0" + b"e\x03\x02\x02D"
    d += b"f" + b"j" + struct.pack("<hh", 800, 600)
    d += b"k" + struct.pack("<hhhhH", 100, 200, 180, 200, 0x1234)
    d += b"i" + b"q" + bytes([6, 4]) + struct.pack("<hhH", 110, 190, 0xC071) + vstr("Arial,10") + struct.pack("<hh", 30, 16) + vstr("clk")
    d += b"g" + b"f" + b"j" + struct.pack("<hh", 64, 40)
    d += b"q" + bytes([2, 0]) + struct.pack("<hhH", 6, 27, 0x48) + vstr("IN")
    d += b"p" + struct.pack("<hh", 0, 24)
    d += b"q" + bytes([3, 0]) + struct.pack("<hhH", 53, 18, 0x48) + vstr("OUT")
    d += b"p" + struct.pack("<hh", 64, 16)
    d += b"q" + bytes([1, 4]) + struct.pack("<hhH", 1, 31, 0x806E) + vstr("Arial,6") + struct.pack("<hh", 21, 9) + vstr("NOT")
    d += b"r" + struct.pack("<hhB", 180, 176, 0)
    d += b"h" + b"q" + bytes([41, 4]) + struct.pack("<hhH", 1, 0, 0xC066) + vstr("Arial,6") + struct.pack("<hh", 5, 10) + vstr("7")
    return d + b"t"


class Gdf(unittest.TestCase):
    def test_records(self):
        version, header, records, rest = gdf.parse(build())
        self.assertEqual((version, header, rest), (6, b"\x03\x02\x02D", 0))
        self.assertEqual(records[0], ("graphic", 800, 600))
        self.assertEqual(records[1], ("line", "k", 100, 200, 180, 200, 0x1234))
        self.assertEqual(gdf.wires(records), [(100, 200, 180, 200, "clk")])
        syms = gdf.symbols(records)
        self.assertEqual(len(syms), 1)
        self.assertEqual((syms[0]["name"], syms[0]["size"]), ("NOT", (64, 40)))
        self.assertEqual(syms[0]["pins"], [("IN", 2, 0, 24), ("OUT", 3, 64, 16)])
        self.assertEqual(syms[0]["instances"], [{"x": 180, "y": 176, "orientation": 0, "id": "7"}])

    def test_errors(self):
        self.assertRaises(gdf.GdfError, gdf.parse, b"nope")
        self.assertRaises(gdf.GdfError, gdf.parse, build()[:-1])            # no terminator
        self.assertRaises(gdf.GdfError, gdf.parse, build()[:-1] + b"Z")

    @unittest.skipUnless(VENDOR.exists(), "vendor files not extracted")
    def test_every_vendor_schematic_parses_to_the_end(self):
        files = sorted(VENDOR.glob("**/*.gdf"))
        self.assertGreater(len(files), 300)
        for path in files:
            self.assertEqual(gdf.parse(path.read_bytes())[3], 0, path.name)


@unittest.skipUnless(VENDOR.exists(), "vendor files not extracted")
class SchematicLogic(unittest.TestCase):
    LIB = VENDOR / "MAX+plus_II_BASELINE"

    def resolver(self, *extra):
        from apu import ahdl_eval
        return ahdl_eval.directory_resolver(*extra, *ahdl_eval.vendor_library(self.LIB))

    def test_library_4count_counts(self):
        from apu import ahdl_eval
        text = gdf.to_ahdl((self.LIB / "max2lib/mf/4count.gdf").read_bytes(), "4count")
        value = lambda o: o["qd"] * 8 + o["qc"] * 4 + o["qb"] * 2 + o["qa"]
        sim = ahdl_eval.Simulator(text)
        sim.set(setn=1, clrn=1, ldn=1, cin=1, dnup=0)
        self.assertEqual([value(sim.clock()) for _ in range(17)], list(range(1, 16)) + [0, 1])
        sim.set(dnup=1)
        self.assertEqual([value(sim.clock()) for _ in range(3)], [0, 15, 14])
        sim.set(ldn=0, d=1, c=0, b=1, a=0)
        self.assertEqual(value(sim.clock()), 10)                     # synchronous load
        sim.set(ldn=1, cin=0)
        self.assertEqual(value(sim.clock()), 10)                     # carry-in low holds
        sim.set(clrn=0)
        self.assertEqual(value(sim.outputs()), 0)                    # asynchronous clear

    def test_tutorial_design_using_two_library_schematics(self):
        from apu import ahdl_eval
        tutorial = VENDOR / "MAX+plus_II_Tutorial/ahdl"
        sim = ahdl_eval.Simulator((tutorial / "macro2.tdf").read_text(encoding="latin1"), self.resolver(tutorial))
        high = []
        for _ in range(17):
            out = sim.clock()
            high.append([k for k, v in out.items() if v])
        self.assertEqual(high, [["out%d" % (i % 16)] for i in range(1, 18)])


@unittest.skipUnless(VENDOR.exists(), "vendor files not extracted")
class AgainstVendorSimulator(unittest.TestCase):
    def test_chiptrip_matches_the_vendor_simulation_table(self):
        import re
        from apu import ahdl_eval, tbl
        tutorial = VENDOR / "MAX+plus_II_Tutorial/chiptrip"
        library = ahdl_eval.vendor_library(VENDOR / "MAX+plus_II_BASELINE")
        table = tbl.parse((tutorial / "chiptrip.tbl").read_text(encoding="latin1"))
        design = gdf.to_ahdl((tutorial / "chiptrip.gdf").read_bytes(), "chiptrip")
        sim = ahdl_eval.Simulator(design, ahdl_eval.directory_resolver(tutorial, *library))
        rename = lambda n: re.sub(r"\|[\w~]+:(\d+)\|", r"u\1/", n).lstrip("|")
        checks, mismatches = tbl.compare(table, sim, rename, table.outputs + table.buried)
        self.assertEqual(checks, 80)
        self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
