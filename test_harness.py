import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        for inhalt in ("{ kaputt", json.dumps({"harness": "gemini"}), json.dumps(["codex"]), json.dumps({"harness": []})):
            with self.subTest(inhalt=inhalt):
                (self.tmp / "harness.json").write_text(inhalt)
                self.assertEqual(harness.aktuell(self.tmp), "claude-code")

    def test_unbekannter_harness_wird_nicht_gemerkt(self):
        with self.assertRaises(ValueError):
            harness.merken(self.tmp, "gemini")
        self.assertFalse((self.tmp / "harness.json").exists())

    def test_schreiben_ersetzt_die_datei_in_einem_schritt(self):
        """Der Server liest Inventar und Dashboard, während build.py sie schreibt. Bis zum Tausch muss die alte Datei
        ganz dastehen und die neue schon fertig sein, sonst sieht ein Klick ein halbes Inventar."""
        ziel = self.tmp / "inventar.json"
        ziel.write_text("alt")
        gesehen, echt = [], harness.os.replace

        def tausch(quelle, datei):
            gesehen.append((Path(quelle).read_text(), Path(datei).read_text()))
            echt(quelle, datei)
        with mock.patch.object(harness.os, "replace", side_effect=tausch):
            harness.schreiben(ziel, "neu")
        self.assertEqual(gesehen, [("neu", "alt")])
        self.assertEqual([d.name for d in self.tmp.iterdir()], ["inventar.json"])
        self.assertEqual(ziel.read_text(), "neu")

    def test_zwei_schreiber_zugleich(self):
        """Mit dem festen Zwischennamen <datei>.neu nahm der zweite Schreiber dem ersten die Datei weg:
        FileNotFoundError im Tausch, oder die Datei des einen mit dem Text des anderen."""
        ziel = self.tmp / "inventar.json"
        echt, erster = harness.os.replace, []

        def tausch(quelle, datei):
            if not erster:  # während der erste tauschen will, schreibt ein zweiter dieselbe Datei
                erster.append(True)
                harness.schreiben(ziel, "zweiter")
            echt(quelle, datei)
        with mock.patch.object(harness.os, "replace", side_effect=tausch):
            harness.schreiben(ziel, "erster")
        self.assertEqual(ziel.read_text(), "erster")
        self.assertEqual([d.name for d in self.tmp.iterdir()], ["inventar.json"])

    def test_schreiben_behaelt_rechte_und_folgt_dem_symlink(self):
        ziel = self.tmp / "echt.json"
        ziel.write_text("alt")
        ziel.chmod(0o640)
        (self.tmp / "link.json").symlink_to(ziel)
        harness.schreiben(self.tmp / "link.json", "neu")
        self.assertTrue((self.tmp / "link.json").is_symlink())
        self.assertEqual((ziel.read_text(), ziel.stat().st_mode & 0o777), ("neu", 0o640))

    def test_dateien_je_harness(self):
        self.assertEqual({h: (d["inventar"], d["dashboard"], d["bewertung"]) for h, d in harness.HARNESSE.items()}, {
            "claude-code": ("inventar.json", "dashboard.html", "bewertung-claude-code.json"),
            "codex": ("inventar-codex.json", "dashboard-codex.html", "bewertung-codex.json"),
        })
