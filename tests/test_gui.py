import json
import pathlib
import threading
import unittest
import urllib.request

from apu import gui

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor/maxplus2-10.2"


@unittest.skipUnless(VENDOR.exists(), "vendor files not extracted")
class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = gui.serve(VENDOR / "MAX+plus_II_Tutorial/chiptrip", VENDOR / "MAX+plus_II_BASELINE", open_browser=False)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def get(self, path):
        with urllib.request.urlopen(self.server.url + path, timeout=120) as r:
            body = r.read().decode()
        return json.loads(body) if path.startswith("api/") else body

    def test_page_and_file_list(self):
        self.assertIn("<title>apu-revive</title>", self.get(""))
        names = [f["name"] for f in self.get("api/files")["files"]]
        self.assertIn("chiptrip.gdf", names)
        self.assertIn("chiptrip.tbl", names)

    def test_source_compile_simulate(self):
        self.assertIn("SUBDESIGN chiptrip", self.get("api/source?name=chiptrip.gdf")["text"])
        compiled = self.get("api/compile?name=auto_max.tdf&device=EPM7032")
        self.assertIn("macrocells used", compiled["fit"])
        sim = self.get("api/simulate?name=chiptrip.gdf&tbl=chiptrip.tbl")
        self.assertEqual((sim["checks"], sim["mismatches"]), (80, []))
        self.assertIn("at_altera", sim["signals"])

    def test_errors_are_reported_not_fatal(self):
        self.assertIn("error", self.get("api/source?name=../../etc/passwd"))
        self.assertIn("error", self.get("api/compile?name=chiptrip.gdf&device=NOPE"))


if __name__ == "__main__":
    unittest.main()
