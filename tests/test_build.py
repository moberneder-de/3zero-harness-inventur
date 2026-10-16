import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from inventur import build
from inventur import claude


class Verlauf(unittest.TestCase):
    def test_kontextlast_zaehlt_nur_was_in_jeder_sitzung_laedt(self):
        """Ohne die eingebauten Skills des Harness: Codex legt sie als Dateien ab und könnte sie messen,
        Claude Code spielt sie zur Laufzeit ein. Gezählt wird nur, was beide sehen — sonst hieße dieselbe
        Kachel in beiden Dashboards gleich und meinte Verschiedenes, und der Verlauf wäre unvergleichbar."""
        inhalt = {"skills": [{"tokens": 10}, {"tokens": 0}], "hooks": [{"tokens": 5}],
                  "mcp": [{"tokens": 100}, {"tokens": None}], "eingebaut": [{"tokens": 2}, {}],
                  "projekt_skills": [{"tokens": 999}], "projekt_mcp": [{"tokens": 999}]}
        self.assertEqual(build.kontextlast(inhalt), 115)

    def test_kontextlast_zaehlt_globale_anweisungen_die_laden(self):
        """CLAUDE.md, Regeln und der gewählte Output-Style laden wie Skill-Beschreibungen in jede Sitzung. Was aus ist,
        erst bei Bedarf lädt oder nur in einem Projekt gilt, zählt nicht."""
        z = lambda bereich, tokens, laedt: {"bereich": bereich, "tokens": tokens, "laedt": laedt}
        inhalt = {"skills": [{"tokens": 10}], "anweisungen": [z("global", 300, True), z("global", 50, False), z("/p", 9000, True)]}
        self.assertEqual(build.kontextlast(inhalt), 310)

    def test_je_tag_eine_zeile_der_letzte_lauf_gilt(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        pfad = tmp / "verlauf.json"
        build.verlauf_fortschreiben(pfad, {"datum": "2026-09-17", "kontextlast": 100})
        build.verlauf_fortschreiben(pfad, {"datum": "2026-09-18", "kontextlast": 90})
        reihe = build.verlauf_fortschreiben(pfad, {"datum": "2026-09-18", "kontextlast": 80})
        self.assertEqual(reihe, [{"datum": "2026-09-17", "kontextlast": 100}, {"datum": "2026-09-18", "kontextlast": 80}])
        self.assertEqual(json.loads(pfad.read_text(encoding="utf-8")), reihe)

    def test_kaputte_datei_beginnt_neu(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        pfad = tmp / "verlauf.json"
        pfad.write_text("kein json", encoding="utf-8")
        self.assertEqual(build.verlauf_fortschreiben(pfad, {"datum": "2026-09-18"}), [{"datum": "2026-09-18"}])

    def pfad(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        return tmp / "verlauf.json"

    def test_ids_nur_fuer_heute_und_den_letzten_lauf_davor(self):
        """Mehr braucht der Vergleich nicht; der Verlauf wächst jeden Tag und steckt ganz im Dashboard."""
        pfad = self.pfad()
        for tag in ("2026-09-26", "2026-09-27", "2026-09-28"):
            reihe = build.verlauf_fortschreiben(pfad, {"datum": tag, "kontextlast": 1, "ids": {"mcp": [f"mcp:{tag}"]}})
        self.assertEqual([("ids" in e) for e in reihe], [False, True, True])
        self.assertEqual(json.loads(pfad.read_text(encoding="utf-8")), reihe)

    def test_neu_und_entfernt_seit_dem_letzten_lauf_vor_heute(self):
        """Mehrere Läufe am selben Tag überschreiben sich; verglichen wird deshalb mit dem Tag davor, sonst wäre nach dem
        zweiten Lauf alles „unverändert“. Ein Status zählt nicht, nur ob die ID da ist."""
        pfad = self.pfad()
        build.verlauf_fortschreiben(pfad, {"datum": "2026-09-27", "ids": {"mcp": ["mcp:a"], "skills": ["skill:x", "skill:y"]}})
        build.verlauf_fortschreiben(pfad, {"datum": "2026-09-28", "ids": {"mcp": ["mcp:a", "mcp:neu"], "skills": ["skill:x", "skill:y"]}})
        reihe = build.verlauf_fortschreiben(pfad, {"datum": "2026-09-28", "ids": {"mcp": ["mcp:a", "mcp:neu"], "skills": ["skill:x"]}})
        self.assertEqual(build.aenderungen(reihe), {"seit": "2026-09-27", "neu": {"mcp": ["mcp:neu"]}, "entfernt": {"skills": ["skill:y"]}})

    def test_nichts_geaendert(self):
        reihe = [{"datum": "2026-09-27", "ids": {"mcp": ["mcp:a"]}}, {"datum": "2026-09-28", "ids": {"mcp": ["mcp:a"]}}]
        self.assertEqual(build.aenderungen(reihe), {"seit": "2026-09-27", "neu": {}, "entfernt": {}})

    def test_ohne_vergleich_keine_aenderungen(self):
        """Erster Lauf, oder der Tag davor stammt aus einem Verlauf ohne IDs: Sonst stünde alles als „neu“ da."""
        self.assertIsNone(build.aenderungen([{"datum": "2026-09-28", "ids": {"mcp": ["mcp:a"]}}]))
        self.assertIsNone(build.aenderungen([{"datum": "2026-09-27", "kontextlast": 5}, {"datum": "2026-09-28", "ids": {"mcp": ["mcp:a"]}}]))

    def test_art_ohne_alten_stand_zaehlt_nicht(self):
        reihe = [{"datum": "2026-09-27", "ids": {"mcp": ["mcp:a"]}},
                 {"datum": "2026-09-28", "ids": {"mcp": ["mcp:a"], "projekt_mcp": ["mcp:P/q"]}}]
        self.assertEqual(build.aenderungen(reihe), {"seit": "2026-09-27", "neu": {}, "entfernt": {}})

    def test_verlauf_mit_eintraegen_ohne_datum(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        pfad = tmp / "verlauf.json"
        pfad.write_text(json.dumps([{"kontextlast": 1}, {"datum": 5}, {"datum": None}, {"datum": "2026-09-17"}]))
        self.assertEqual(build.verlauf_fortschreiben(pfad, {"datum": "2026-09-18"}), [{"datum": "2026-09-17"}, {"datum": "2026-09-18"}])


class AnweisungenAktionen(unittest.TestCase):
    """Abschalten heißt hier nie, die Datei anzufassen: Sie liegt oft in einem Repo. Geschaltet wird in ~/.claude/settings.json."""

    def zeile(self, art="CLAUDE.md", pfad="/p/CLAUDE.md", **extra):
        return {"bereich": "/p", "art": art, "name": "CLAUDE.md", "pfad": pfad, "status": "aktiv", "marken": [], **extra}

    def aktionen(self, **zeile):
        (z,) = build.anweisungen_aktionen([self.zeile(**zeile)])
        return z["aktionen"]

    def test_datei_ausschliessen_und_wieder_einschliessen(self):
        self.assertEqual(self.aktionen(), {"deaktivieren": {"ausschliessen": "/p/CLAUDE.md"}})
        self.assertEqual(self.aktionen(art="Regel", status="bei Bedarf"), {"deaktivieren": {"ausschliessen": "/p/CLAUDE.md"}})
        self.assertEqual(self.aktionen(status="aus", ausgeschlossen="eigen"), {"einschalten": {"einschliessen": "/p/CLAUDE.md"}})

    def test_ohne_aktion(self):
        """Ein Muster oder ein Eintrag im Projekt träfe mehr als diese Zeile; verwaltete Dateien gehören dem Administrator."""
        self.assertEqual(self.aktionen(status="aus", ausgeschlossen="muster"), {})
        self.assertEqual(self.aktionen(verwaltet=True), {})
        self.assertEqual(self.aktionen(art="Memory"), {})
        self.assertEqual(self.aktionen(art="AGENTS.md", pfad="/h/.codex/AGENTS.md"), {})

    def test_output_style(self):
        self.assertEqual(self.aktionen(art="Output-Style", name="Einfach", gewaehlt=True), {"deaktivieren": {"output_style": None}})
        self.assertEqual(self.aktionen(art="Output-Style", name="Knapp", gewaehlt=False, status="aus"), {"einschalten": {"output_style": "Knapp"}})
        # ein eingebauter oder unbekannter Name hat keine Datei: abwählen geht, wählen nur, was es als Datei gibt
        self.assertEqual(self.aktionen(art="Output-Style", pfad=None, name="concise", gewaehlt=True, status="wirkungslos"), {"deaktivieren": {"output_style": None}})

    def test_pruefen_nur_fuer_die_claude_md_eines_projekts_und_nur_mit_dem_skill(self):
        self.assertEqual(build.anweisungen_aktionen([self.zeile()], True)[0]["pruefen"], {"datei": "/p/CLAUDE.md", "projekt": "/p"})
        for zeile in (self.zeile(bereich="global"), self.zeile(geerbt=True), self.zeile(verwaltet=True), self.zeile(art="Regel"), self.zeile(art="Memory")):
            with self.subTest(zeile=zeile):
                self.assertNotIn("pruefen", build.anweisungen_aktionen([zeile], True)[0])
        self.assertNotIn("pruefen", build.anweisungen_aktionen([self.zeile()])[0])  # ohne den Skill kein Knopf

    def test_id_nennt_bereich_und_datei(self):
        """Dieselbe geerbte CLAUDE.md steht in jedem Projekt darunter: Mit dem Pfad allein teilten sich die Zeilen eine ID."""
        a, b = build.anweisungen_aktionen([self.zeile(), {**self.zeile(), "bereich": "/q"}])
        self.assertEqual((a["id"], b["id"]), ("anweisung:/p:/p/CLAUDE.md", "anweisung:/q:/p/CLAUDE.md"))
        (stil,) = build.anweisungen_aktionen([self.zeile(art="Output-Style", pfad=None, name="Concise", gewaehlt=True, bereich="global")])
        self.assertEqual(stil["id"], "anweisung:global:Concise")


class HarnessWahl(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.daten = self.tmp / "daten"  # eigener Ordner: so fällt auf, wenn etwas wieder ins Werkzeug schreibt
        self.daten.mkdir()
        (self.tmp / "template.html").write_text("<script>__DATEN__</script>")
        # werkzeuge.pruefen fragte sonst das Netz und startete echte Programme
        self.werkzeuge = mock.patch.object(build.werkzeuge, "pruefen", return_value=[{"id": "python3", "status": "aktuell"}])
        self.patches = [mock.patch.object(build, "HIER", self.tmp), mock.patch.object(build, "DATEN", self.daten),
                        # main() liest ~/.claude.json und settings.json: hier aus einem leeren Home
                        mock.patch.object(claude, "HOME", self.tmp / "home"), mock.patch.object(claude, "CLAUDE", self.tmp / "home/.claude"),
                        mock.patch.object(claude, "AGENTS", self.tmp / "home/.agents"),
                        mock.patch.object(build, "LOGO", self.tmp / "logo.svg"), self.werkzeuge,
                        mock.patch.object(build.werkzeuge, "quellcode_pruefen", return_value=[{"id": "/h/x", "ruht": True}]),
                        # sonst läsen die Tests die echten Einstellungen des Rechners
                        mock.patch.object(build.berechtigungen, "claude", return_value=[]),
                        mock.patch.object(build.berechtigungen, "codex", return_value=[]),
                        mock.patch.object(build.modelle, "claude", return_value={"eingestellt": [], "gemessen": []}),
                        mock.patch.object(build.modelle, "codex", return_value={"eingestellt": [], "gemessen": []}),
                        mock.patch.object(build.anweisungen, "claude", return_value=[]),
                        mock.patch.object(build.anweisungen, "codex", return_value=[])]
        (self.tmp / "logo.svg").write_text("<svg></svg>")
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)

    def leeres_inventar(self, **extra):
        return {"live_status": None, "bewertung_stand": None, "overrides_verwaist": [], "mcp": [], "plugins": [], "skills": [],
                "projekt_skills": [], "projekt_mcp": [], "hooks": [], "mess_tage": None, "eingebaut": [], "gruppen": [],
                "hinweise": [], "unbewertet": [], "entfallen": [], **extra}

    def test_codex_schreibt_eigene_dateien_und_merkt_sich_den_harness(self):
        from inventur import codex
        with mock.patch.object(codex, "inventar", return_value=self.leeres_inventar()), mock.patch.object(build, "version", return_value="codex-cli 0.154.0"):
            build.main(["--harness", "codex"])
        daten = json.loads((self.daten / "inventar-codex.json").read_text())
        self.assertEqual((daten["harness"], daten["harness_name"], daten["version"]), ("codex", "Codex", "codex-cli 0.154.0"))
        self.assertTrue((self.daten / "dashboard-codex.html").exists())
        self.assertFalse((self.daten / "inventar-claude-code.json").exists())
        self.assertEqual(json.loads((self.daten / "harness.json").read_text()), {"harness": "codex"})

    def test_alles_geschriebene_wird_in_einem_schritt_getauscht(self):
        """Der Server liest diese Dateien bei jedem Klick und jedem Seitenaufruf, auch während build.py läuft."""
        getauscht, echt = [], build.harness.os.replace

        def tausch(quelle, datei):
            getauscht.append(Path(datei).name)
            echt(quelle, datei)
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), \
                mock.patch.object(build, "version", return_value=None), mock.patch.object(build.harness.os, "replace", side_effect=tausch):
            build.main(["--harness", "claude-code"])
        self.assertEqual(sorted(getauscht), ["dashboard-claude-code.html", "harness.json", "inventar-claude-code.json", "verlauf-claude-code.json"])
        self.assertEqual(sorted(d.name for d in self.daten.iterdir()), sorted(getauscht))

    def test_ohne_angabe_gilt_der_gemerkte_harness(self):
        from inventur import codex
        (self.daten / "harness.json").write_text('{"harness": "codex"}')
        with mock.patch.object(codex, "inventar", return_value=self.leeres_inventar()) as lesen, \
                mock.patch.object(claude, "inventar") as gelesen, mock.patch.object(build, "version", return_value=None):
            build.main([])
        lesen.assert_called_once()
        gelesen.assert_not_called()

    def test_claude_code_behaelt_seine_dateien(self):
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar(live_status=True)), \
                mock.patch.object(build, "version", return_value="2.1.272 (Claude Code)"):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual((daten["harness"], daten["harness_name"], daten["version"]), ("claude-code", "Claude Code", "2.1.272 (Claude Code)"))
        self.assertTrue((self.daten / "dashboard-claude-code.html").exists())

    def test_verlauf_liegt_je_harness_getrennt_und_kommt_ins_inventar(self):
        """Die IDs bleiben in der Verlaufsdatei; ins Inventar und damit ins Dashboard kommt nur, was sich geändert hat."""
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar(skills=[{"id": "skill:x", "tokens": 40}])), \
                mock.patch.object(build, "version", return_value=None):
            build.main(["--harness", "claude-code"])
        reihe = json.loads((self.daten / "verlauf-claude-code.json").read_text())
        self.assertEqual([r["kontextlast"] for r in reihe], [40])
        self.assertEqual(reihe[0]["ids"]["skills"], ["skill:x"])
        inventar = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual(inventar["verlauf"], [{k: v for k, v in r.items() if k != "ids"} for r in reihe])
        self.assertIsNone(inventar["aenderungen"])
        self.assertFalse((self.daten / "verlauf-codex.json").exists())

    def test_bewertung_traegt_die_uhrzeit_der_datei(self):
        # der Skill schreibt nur ein Datum in `stand`; wann der Lauf durchkam, sagt die Änderungszeit
        datei = self.daten / "bewertung-claude-code.json"
        datei.write_text('{"stand": "2026-09-17"}', encoding="utf-8")
        os.utime(datei, (1789000000, 1789000000))
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), \
                mock.patch.object(build, "version", return_value=None):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        erwartet = datetime.fromtimestamp(1789000000).strftime("%Y-%m-%d %H:%M")
        self.assertEqual(daten["bewertung_erstellt"], erwartet)

    def test_ohne_bewertungsdatei_keine_uhrzeit(self):
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), \
                mock.patch.object(build, "version", return_value=None):
            build.main(["--harness", "claude-code"])
        self.assertIsNone(json.loads((self.daten / "inventar-claude-code.json").read_text())["bewertung_erstellt"])

    def test_lesefehler_und_ungenutzte_kommen_ins_inventar(self):
        def inventar(*_):
            build.LESEFEHLER.append({"quelle": "Projekt /x", "fehler": "RuntimeError: kaputt"})
            return self.leeres_inventar(skills=[{"id": "skill:s", "name": "s", "im_kontext": True, "tokens": 5, "nutzung": None}])
        build.LESEFEHLER.append({"quelle": "vom letzten Lauf", "fehler": "…"})
        with mock.patch.object(claude, "inventar", side_effect=inventar), mock.patch.object(build, "version", return_value=None):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual(daten["lesefehler"], [{"quelle": "Projekt /x", "fehler": "RuntimeError: kaputt"}])
        self.assertEqual((daten["skills"][0]["ungenutzt"], daten["ungenutzt_tage"]), (True, 30))

    def test_anweisungen_kommen_ins_inventar_und_zaehlen_zur_kontextlast(self):
        zeile = {"bereich": "global", "art": "CLAUDE.md", "name": "~/.claude/CLAUDE.md", "pfad": "/h/.claude/CLAUDE.md",
                 "tokens": 300, "status": "aktiv", "laedt": True, "marken": []}
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar(skills=[{"id": "skill:a", "tokens": 10}])), \
                mock.patch.object(build, "version", return_value=None), mock.patch.object(build, "ungenutzt_markieren"), \
                mock.patch.object(build.anweisungen, "claude", return_value=[dict(zeile)]):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual((daten["kontextlast"], daten["anweisungen"][0]["id"], daten["anweisungen"][0]["aktionen"]),
                         (310, "anweisung:global:/h/.claude/CLAUDE.md", {"deaktivieren": {"ausschliessen": "/h/.claude/CLAUDE.md"}}))
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), mock.patch.object(build, "version", return_value=None), \
                mock.patch.object(build.anweisungen, "claude", side_effect=RuntimeError("kaputt")):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual((daten["anweisungen"], daten["lesefehler"]), ([], [{"quelle": "Anweisungen", "fehler": "RuntimeError: kaputt"}]))

    def test_berechtigungen_kommen_ins_inventar_und_ein_fehler_kostet_nur_sie(self):
        regel = {"bereich": "global", "quelle": "~/.claude/settings.json", "art": "allow", "regel": "Bash", "marken": ["breit"]}
        mcp = [{"id": "mcp:plugin:github:github", "name": "plugin:github:github"},
               {"id": "mcp:sites", "name": "sites", "schluessel": "plugin:sites:web"}]
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar(mcp=mcp, projekt_mcp=[{"id": "mcp:P/q", "name": "q"}])), \
                mock.patch.object(build, "version", return_value=None), \
                mock.patch.object(build.berechtigungen, "claude", return_value=[regel]) as gelesen:
            build.main(["--harness", "claude-code"])
        self.assertEqual(json.loads((self.daten / "inventar-claude-code.json").read_text())["berechtigungen"], [regel])
        self.assertEqual(gelesen.call_args.args[2], {"plugin_github_github", "plugin_sites_web", "q"})
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), mock.patch.object(build, "version", return_value=None), \
                mock.patch.object(build.berechtigungen, "claude", side_effect=RuntimeError("kaputt")):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual((daten["berechtigungen"], daten["lesefehler"]), ([], [{"quelle": "Berechtigungen", "fehler": "RuntimeError: kaputt"}]))

    def test_verwaiste_regeln_bieten_entfernen_an(self):
        from inventur import codex
        datei = "/x/P/.claude/settings.local.json"
        zeilen = [{"bereich": "/x/P", "quelle": "~/P/.claude/settings.local.json", "datei": datei, "art": "allow", "regel": "mcp__weg__lesen", "marken": ["verwaist"]},
                  {"bereich": "/x/P", "quelle": "~/P/.claude/settings.local.json", "datei": datei, "art": "allow", "regel": "Bash", "marken": ["breit"]},
                  {"bereich": "global", "quelle": "/etc/managed-settings.json", "datei": "/etc/managed-settings.json", "art": "allow", "regel": "mcp__weg__lesen",
                   "marken": ["verwaist"], "verwaltet": True},
                  {"bereich": "/x/P", "quelle": "~/P/.claude/settings.local.json", "datei": datei, "art": "Modus", "regel": "defaultMode: bypassPermissions", "marken": ["breit"]},
                  {"bereich": "/x/P", "quelle": "~/P/.claude/settings.local.json", "datei": datei, "art": "allow", "regel": "Bash(ls)", "marken": []},
                  {"bereich": "/x/P", "quelle": "~/P/.claude/settings.local.json", "datei": datei, "art": "allow", "regel": "WebSearch", "marken": ["doppelt"]},
                  {"bereich": "/x/P", "quelle": "~/P/.claude/settings.local.json", "datei": datei, "art": "deny", "regel": "Read(x)", "marken": ["widerspricht"]}]
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), mock.patch.object(build, "version", return_value=None), \
                mock.patch.object(build.berechtigungen, "claude", return_value=zeilen):
            build.main(["--harness", "claude-code"])
        rechte = json.loads((self.daten / "inventar-claude-code.json").read_text())["berechtigungen"]
        self.assertEqual((rechte[0]["id"], rechte[0]["aktionen"]), (f"recht:{datei}:allow:mcp__weg__lesen", {"entfernen": {
            "regel_entfernen": {"datei": datei, "art": "allow", "regel": "mcp__weg__lesen"}}}))
        self.assertEqual(rechte[1]["aktionen"], {"entfernen": {"regel_entfernen": {"datei": datei, "art": "allow", "regel": "Bash"}}})
        self.assertNotIn("aktionen", rechte[2])  # verwaltete Einstellungen gehören dem Administrator
        self.assertNotIn("aktionen", rechte[3])  # ein Modus ist keine Regel in einer Liste
        self.assertNotIn("aktionen", rechte[4])  # ohne Marke gibt es nichts aufzuräumen
        self.assertEqual([("aktionen" in z) for z in rechte[5:]], [True, True])  # doppelt, widerspricht
        ordner = [{"bereich": "/weg", "quelle": "~/.codex/config.toml", "art": "Vertrauen", "regel": "trusted", "marken": ["verwaist"]},
                  {"bereich": "/da", "quelle": "~/.codex/config.toml", "art": "Vertrauen", "regel": "trusted", "marken": []}]
        with mock.patch.object(codex, "inventar", return_value=self.leeres_inventar()), mock.patch.object(build, "version", return_value=None), \
                mock.patch.object(build.berechtigungen, "codex", return_value=ordner):
            build.main(["--harness", "codex"])
        rechte = json.loads((self.daten / "inventar-codex.json").read_text())["berechtigungen"]
        self.assertEqual((rechte[0]["id"], rechte[0]["aktionen"]), ("recht:Vertrauen:/weg", {"entfernen": {"toml_tabelle_entfernen": ["projects", "/weg"]}}))
        self.assertNotIn("aktionen", rechte[1])

    def test_modelle_kommen_ins_inventar_und_ein_fehler_kostet_nur_sie(self):
        agent = self.tmp / "pruefer.md"
        agent.write_text("---\nname: pruefer\nmodel: haiku\n---\n")
        skill = self.tmp / "SKILL.md"
        skill.write_text("---\nname: bauen\neffort: low\n---\n")
        ergebnis = {"eingestellt": [{"bereich": "global", "quelle": "~/.claude/settings.json", "name": "model", "wert": "opus", "marken": []}],
                    "gemessen": [{"projekt": "~/P", "modell": "claude-opus-5-5", "subagent": False, "antworten": 1, "tokens": 2, "aufwand": []}]}
        inventar = self.leeres_inventar(skills=[{"id": "agent:pruefer", "name": "pruefer", "typ": "Agent", "pfad": str(agent)}],
                                        projekt_skills=[{"id": "skill:P/bauen", "name": "bauen", "typ": "Skill", "pfad": str(skill), "projekt": "/x/P"}])
        with mock.patch.object(claude, "inventar", return_value=inventar), mock.patch.object(build, "version", return_value=None), \
                mock.patch.object(claude, "transkripte", return_value=["t.jsonl"]), \
                mock.patch.object(build.modelle, "claude", return_value=ergebnis) as gelesen:
            build.main(["--harness", "claude-code"])
        self.assertEqual(json.loads((self.daten / "inventar-claude-code.json").read_text())["modelle"], {**ergebnis, "tage": build.MESS_TAGE})
        self.assertEqual(gelesen.call_args.args[2], [
            {"bereich": "global", "eintrag": "Agent pruefer", "pfad": str(agent), "kopf": {"name": "pruefer", "model": "haiku"}},
            {"bereich": "/x/P", "eintrag": "Skill bauen", "pfad": str(skill), "kopf": {"name": "bauen", "effort": "low"}}])
        self.assertEqual(gelesen.call_args.args[3], ["t.jsonl"])
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), mock.patch.object(build, "version", return_value=None), \
                mock.patch.object(build.modelle, "claude", side_effect=RuntimeError("kaputt")):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual((daten["modelle"], daten["lesefehler"]),
                         ({"eingestellt": [], "gemessen": [], "tage": build.MESS_TAGE}, [{"quelle": "Modelle", "fehler": "RuntimeError: kaputt"}]))

    def test_modelle_unter_codex_lesen_nur_frische_sitzungen(self):
        from inventur import codex
        frisch, alt = self.tmp / "frisch.jsonl", self.tmp / "alt.jsonl"
        for d in (frisch, alt):
            d.write_text("")
        os.utime(alt, (0, 0))
        with mock.patch.object(codex, "inventar", return_value=self.leeres_inventar()), mock.patch.object(build, "version", return_value=None), \
                mock.patch.object(codex, "sitzungen", return_value=[alt, frisch, self.tmp / "fehlt.jsonl"]), \
                mock.patch.object(codex, "lies_toml", return_value={"model": "gpt-6"}), \
                mock.patch.object(build.modelle, "codex", return_value={"eingestellt": [], "gemessen": []}) as lesen:
            build.main(["--harness", "codex"])
        self.assertEqual(lesen.call_args.args[:2], ({"model": "gpt-6"}, [frisch]))
        self.assertEqual(json.loads((self.daten / "inventar-codex.json").read_text())["modelle"]["tage"], build.MESS_TAGE)

    def test_werkzeuge_kommen_ins_inventar_und_ein_fehler_kostet_nur_sie(self):
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), mock.patch.object(build, "version", return_value=None):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual((daten["werkzeuge"], daten["quellcode"]), ([{"id": "python3", "status": "aktuell"}], [{"id": "/h/x", "ruht": True}]))
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), mock.patch.object(build, "version", return_value=None), \
                mock.patch.object(build.werkzeuge, "pruefen", side_effect=RuntimeError("kaputt")), \
                mock.patch.object(build.werkzeuge, "quellcode_pruefen", side_effect=RuntimeError("auch")):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual((daten["werkzeuge"], daten["quellcode"], daten["lesefehler"]), ([], [], [
            {"quelle": "Werkzeuge", "fehler": "RuntimeError: kaputt"}, {"quelle": "MCP-Server aus Quellcode", "fehler": "RuntimeError: auch"}]))

    def test_datenordner_nur_fuer_den_benutzer(self):
        """Das Inventar nennt Pfade und MCP-Ziele; andere Benutzer des Rechners sollen es nicht lesen."""
        self.daten.chmod(0o755)
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar()), \
                mock.patch.object(build, "version", return_value=None):
            build.main(["--harness", "claude-code"])
        self.assertEqual(self.daten.stat().st_mode & 0o777, 0o700)

    def test_daten_beenden_das_skript_nicht(self):
        """`<!--<script` in einem Namen versetzte den HTML-Parser in den „script data double escaped state“: Das
        folgende </script> beendete das Skript nicht mehr, und das Dashboard blieb leer."""
        boese = "<!--<script></script>"
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar(hinweise=[{"titel": boese, "text": "x"}])), \
                mock.patch.object(build, "version", return_value=None):
            build.main(["--harness", "claude-code"])
        html = (self.daten / "dashboard-claude-code.html").read_text()
        self.assertEqual(html.count("<"), 2)  # nur <script> und </script> der Vorlage
        self.assertEqual(json.loads(html[len("<script>"):-len("</script>")])["hinweise"][0]["titel"], boese)

    def test_kaputte_bewertung_erreicht_das_dashboard_bereinigt(self):
        with mock.patch.object(claude, "inventar", return_value=self.leeres_inventar(gruppen=[{"titel": "x"}], bewertung_stand=5)), \
                mock.patch.object(build, "version", return_value=None):
            build.main(["--harness", "claude-code"])
        daten = json.loads((self.daten / "inventar-claude-code.json").read_text())
        self.assertEqual((daten["gruppen"], daten["bewertung_stand"]), ([], None))


class Ungenutzt(unittest.TestCase):
    def test_laedt_in_jede_sitzung_und_seit_30_tagen_ungenutzt(self):
        """Wer erst seit Kurzem installiert ist, hatte keine 30 Tage, um genutzt zu werden. Für Plugins zählt deren
        Installation; Codex kennt sie nicht und liefert nur den Stand des Ordners."""
        def s(name, **kw):
            return {"name": name, "im_kontext": True, "tokens": 10, "nutzung": None, **kw}
        skills = [s("nie"), s("alt", nutzung={"anzahl": 3, "zuletzt": "2026-08-01"}),
                  s("frisch", nutzung={"anzahl": 1, "zuletzt": "2026-09-20"}), s("neu", installiert="2026-09-10"),
                  s("manuell", im_kontext=False, tokens=0), s("plugin-neu", plugin="p@m"), s("plugin-alt", plugin="q@m"),
                  s("codex-neu", plugin="r@m")]
        plugins = [{"name": "p", "marktplatz": "m", "installiert": "2026-09-15", "aktualisiert": None},
                   {"name": "q", "marktplatz": "m", "installiert": "2026-01-01", "aktualisiert": "2026-09-01"},
                   {"name": "r", "marktplatz": "m", "installiert": None, "aktualisiert": "2026-09-20"}]
        build.ungenutzt_markieren(skills, plugins, datetime(2026, 9, 24))
        self.assertEqual([x["name"] for x in skills if x.get("ungenutzt")], ["nie", "alt", "plugin-alt"])


class BewertungFuersDashboard(unittest.TestCase):
    def test_nur_was_das_template_lesen_kann(self):
        """Die Bewertung schreibt ein Lauf, der fremde Texte liest. Fehlte `mitglieder` oder war `stand` keine
        Zeichenkette, stürzte das Template ab und zeigte nicht einmal den Knopf „neu bewerten“."""
        inhalt = {"skills": [], "bewertung_stand": 20260917,
                  "gruppen": [{"titel": "A", "mitglieder": ["skill:a", 3]}, {"titel": "ohne"}, {"mitglieder": "skill:a"}, "x"],
                  "hinweise": [{"titel": "T", "text": "X"}, {"titel": "T"}, {"titel": 1, "text": "X"}, "x"]}
        self.assertEqual(build.fuers_dashboard(inhalt), {
            "skills": [], "bewertung_stand": None, "gruppen": [{"titel": "A", "mitglieder": ["skill:a"]}],
            "hinweise": [{"titel": "T", "text": "X"}]})
        self.assertEqual(build.fuers_dashboard({"gruppen": {}, "hinweise": "x", "bewertung_stand": "2026-09-17"}),
                         {"gruppen": [], "hinweise": [], "bewertung_stand": "2026-09-17"})


class UpdateVomPlugin(unittest.TestCase):
    """MCP-Server, Hooks und Mods eines Plugins haben keine eigene Version: Ihr Update kommt mit dem Plugin.
    Damit der Statusfilter in ihren Tabs „Update verfügbar“ findet, tragen sie das Plugin, das es anbietet."""

    def test_bestandteile_tragen_das_plugin_mit_update(self):
        inhalt = {"plugins": [{"id": "plugin:p@m", "name": "p", "update": True}, {"id": "plugin:q@m", "name": "q", "update": False}],
                  "mcp": [{"id": "mcp:plugin:p:s", "art": "Plugin", "quelle": "p@m"}, {"id": "mcp:user", "art": "User", "quelle": "~/.claude.json"},
                          {"id": "mcp:plugin:q:s", "art": "Plugin", "quelle": "q@m"}],
                  "hooks": [{"id": "hook:p@m:Stop", "plugin": "p@m"}, {"id": "hook:einstellungen:Stop"}],
                  "mods": [{"id": "mod:p@m", "plugin": "p@m"}, {"id": "mod:/x/dev-mods/s/a", "ordner": "/x/dev-mods/s/a"}],
                  "skills": [{"id": "skill:p:x", "plugin": "p@m"}]}
        build.updates_vom_plugin(inhalt)
        self.assertEqual({e["id"]: e.get("update_plugin") for art in ("mcp", "hooks", "mods", "skills") for e in inhalt[art]}, {
            "mcp:plugin:p:s": "p", "mcp:user": None, "mcp:plugin:q:s": None, "hook:p@m:Stop": "p", "hook:einstellungen:Stop": None,
            "mod:p@m": "p", "mod:/x/dev-mods/s/a": None, "skill:p:x": None})  # Skills zeigen ihr Update im eigenen Filter


class Empfehlungen(unittest.TestCase):
    """Was das Dashboard aus der Bewertung ableitet. Das rechnete das Template selbst, ohne einen einzigen Test."""

    def eintrag(self, id, empfehlung=None, **felder):
        return {"id": id, "name": id.split(":", 1)[1], "bewertung": {"empfehlung": empfehlung} if empfehlung else None, **felder}

    def test_bestandteile_erben_die_empfehlung_ihres_plugins(self):
        e = self.eintrag
        inhalt = {"plugins": [e("plugin:p@m", "entfernen", marktplatz="m"), e("plugin:q@m", marktplatz="m")],
                  "skills": [e("skill:p:eigen", "behalten", plugin="p@m"), e("skill:p:erbt", plugin="p@m"),
                             e("skill:q:ohne", plugin="q@m"), e("skill:lose"), e("skill:fremd", plugin="weg@m")],
                  "mcp": [e("mcp:plugin:p:s", art="Plugin", quelle="p@m"), e("mcp:user", art="User", quelle="~/.claude.json")]}
        build.empfehlungen_ableiten(inhalt)
        self.assertEqual({x["id"]: x["wirksam"] for art in ("plugins", "skills", "mcp") for x in inhalt[art]}, {
            "plugin:p@m": {"empfehlung": "entfernen", "geerbt": False}, "plugin:q@m": None,
            "skill:p:eigen": {"empfehlung": "behalten", "geerbt": False}, "skill:p:erbt": {"empfehlung": "entfernen", "geerbt": True},
            "skill:q:ohne": None, "skill:lose": None, "skill:fremd": None,
            "mcp:plugin:p:s": {"empfehlung": "entfernen", "geerbt": True}, "mcp:user": None})

    def test_ersparnis_zaehlt_jede_tokenzahl_einmal(self):
        """Die Tokens eines Plugins sind schon die Summe seiner Teile: Geht es ganz, zählen die Teile nicht noch einmal."""
        e = self.eintrag
        inhalt = {"plugins": [e("plugin:weg@m", "entfernen", name="weg", marktplatz="m", tokens=100),
                              e("plugin:bleibt@m", "behalten", name="bleibt", marktplatz="m", tokens=80)],
                  "skills": [e("skill:weg:a", "abschalten", plugin="weg@m", tokens=60),      # steckt in den 100
                             e("skill:bleibt:b", "nur manuell", plugin="bleibt@m", tokens=30),  # zählt einzeln
                             e("skill:erledigt", "abschalten", tokens=7, umgesetzt=True),      # kostet heute nichts mehr
                             e("skill:offen", "prüfen", tokens=9),                             # noch keine Entscheidung
                             e("skill:lose", "entfernen", tokens=4), e("skill:ohne", "entfernen")],
                  "hooks": [e("hook:x:Stop", "entfernen", tokens=5)],
                  "mcp": [e("mcp:a", "abschalten", tokens=2)],
                  "projekt_skills": [e("skill:P/x", "entfernen", tokens=50, projekt="/P")]}  # lädt nur dort, nicht in der Kontextlast
        build.empfehlungen_ableiten(inhalt)
        self.assertEqual(inhalt["ersparnis"], 100 + 30 + 4 + 5 + 2)

    def test_leeres_inventar(self):
        inhalt = {}
        build.empfehlungen_ableiten(inhalt)
        self.assertEqual(inhalt, {"ersparnis": 0})


if __name__ == "__main__":
    unittest.main()
