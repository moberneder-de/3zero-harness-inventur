import json
import shutil
import tempfile
import unittest
from pathlib import Path

import harness


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_ohne_datei_gilt_claude_code(self):
        self.assertEqual(harness.aktuell(self.tmp), "claude-code")

    def test_der_letzte_start_zaehlt(self):
        harness.merken(self.tmp, "codex")
        self.assertEqual(harness.aktuell(self.tmp), "codex")
        harness.merken(self.tmp, "claude-code")
        self.assertEqual(harness.aktuell(self.tmp), "claude-code")

    def test_kaputte_oder_fremde_angabe_gilt_als_claude_code(self):
        for inhalt in ("{ kaputt", json.dumps({"harness": "gemini"}), json.dumps(["codex"])):
            with self.subTest(inhalt=inhalt):
                (self.tmp / "harness.json").write_text(inhalt)
                self.assertEqual(harness.aktuell(self.tmp), "claude-code")

    def test_unbekannter_harness_wird_nicht_gemerkt(self):
        with self.assertRaises(ValueError):
            harness.merken(self.tmp, "gemini")
        self.assertFalse((self.tmp / "harness.json").exists())

    def test_dateien_je_harness(self):
        self.assertEqual({h: (d["inventar"], d["dashboard"], d["bewertung"]) for h, d in harness.HARNESSE.items()}, {
            "claude-code": ("inventar.json", "dashboard.html", "bewertung-claude-code.json"),
            "codex": ("inventar-codex.json", "dashboard-codex.html", "bewertung-codex.json"),
        })
