import json
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
K = ROOT / "vendor/maxplus2-10.2/7k"
CHIP = ROOT / "re/traces/bulk_7032lc44_a.json"        # a real EPM7032's read-back (not in the repo)


@unittest.skipUnless(K.exists(), "vendor files not extracted")
class Epm7032Layout(unittest.TestCase):
    def setUp(self):
        from apu import daf, fusemap, maf
        self.fm = fusemap.FuseMap(daf.parse((K / "m7032044.daf").read_bytes()), maf.parse((K / "m7032.maf").read_bytes()))

    def test_every_fuse_has_exactly_one_owner(self):
        from apu import assemble
        owner = assemble.layout(self.fm)                  # raises on overlaps
        kinds = {}
        for o in owner.values():
            kinds[o[0]] = kinds.get(o[0], 0) + 1
        self.assertEqual(len(owner), self.fm.fuses)
        self.assertEqual(kinds, {"term": 14080, "route": 144, "option": 448})

    def test_literals_land_in_their_columns(self):
        from apu import assemble
        blank = assemble.decode(self.fm, [1] * self.fm.fuses)
        blank.terms[5][2] = [("in", 3, False), ("in", 7, True), ("exp", 2)]
        fuses = assemble.encode(self.fm, blank)
        cols = self.fm.product_terms(fuses, 5)[2]
        self.assertEqual(cols[:2], [7, 14])                 # true on 2m + 1, complement on 2m
        self.assertEqual(self.fm.expander_of[cols[2]], 2)
        self.assertEqual(assemble.decode(self.fm, fuses).terms[5][2], blank.terms[5][2])

    @unittest.skipUnless(CHIP.exists(), "no EPM7032 read-back on this machine")
    def test_real_chip_round_trips(self):
        from apu import assemble, jedec
        from apu.pcf import Pcf
        image = {int(k, 16): v["data"] for k, v in json.loads(CHIP.read_text())["rows"].items()}
        fuses = jedec.fuses_from_image(Pcf.load(K / "m7032044.pcf"), image)
        self.assertEqual(assemble.encode(self.fm, assemble.decode(self.fm, fuses)), fuses)


if __name__ == "__main__":
    unittest.main()
