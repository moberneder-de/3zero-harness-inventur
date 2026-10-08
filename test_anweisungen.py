import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path

import anweisungen


class Ordner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        (self.home / ".claude").mkdir(parents=True)

    def schreibe(self, pfad, text="x" * 40):
        pfad = Path(pfad)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(text)
        return pfad

    def memory(self, projekt):
        """Der Ordner heißt wie der Pfad, mit - für alles außer Buchstaben und Ziffern. Auch im Temp-Pfad: mkdtemp
        setzt gelegentlich einen Unterstrich in den Namen, und mit nur dem Schrägstrich ersetzt fiel der Test dann aus."""
        return self.home / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", str(projekt)) / "memory/MEMORY.md"

    def claude(self, settings=None, projekte=(), verwaltet=()):
        return anweisungen.claude(self.home, settings or {}, [str(p) for p in projekte], verwaltet)


class Global(Ordner):
    def test_was_in_jede_sitzung_laedt(self):
        firma = self.schreibe(self.tmp / "firma/CLAUDE.md", "f" * 80)
        eigen = self.schreibe(self.home / ".claude/CLAUDE.md", "e" * 400)
        immer = self.schreibe(self.home / ".claude/rules/stil.md", "s" * 40)
        bedarf = self.schreibe(self.home / ".claude/rules/tief/api.md", '---\npaths:\n  - "src/**"\n---\n' + "a" * 40)
        zeilen = self.claude(verwaltet=[firma, self.tmp / "fehlt/CLAUDE.md"])
        self.assertEqual([(z["art"], z["name"], z["status"], z["laedt"]) for z in zeilen], [
            ("CLAUDE.md", str(firma), "aktiv", True), ("CLAUDE.md", "~/.claude/CLAUDE.md", "aktiv", True),
            ("Regel", "~/.claude/rules/stil.md", "aktiv", True), ("Regel", "~/.claude/rules/tief/api.md", "bei Bedarf", False)])
        self.assertEqual((zeilen[0]["verwaltet"], zeilen[1]["tokens"], zeilen[1]["pfad"], zeilen[1]["bereich"]), (True, 100, str(eigen), "global"))
        self.assertRegex(zeilen[1]["geaendert"], r"^\d{4}-\d\d-\d\d$")
        self.assertTrue(all("verwaltet" not in z for z in zeilen[1:]))
        self.assertEqual((immer.name, bedarf.name), ("stil.md", "api.md"))

    def test_leerer_rechner(self):
        shutil.rmtree(self.home / ".claude")
        self.assertEqual(self.claude(projekte=[self.tmp / "fehlt"]), [])
        self.assertEqual(anweisungen.claude(self.home, None, []), [])

    def test_ausgeschlossen_genau_und_per_muster(self):
        eigen = self.schreibe(self.home / ".claude/CLAUDE.md")
        regel = self.schreibe(self.home / ".claude/rules/a.md")
        genau = {z["art"]: z for z in self.claude({"claudeMdExcludes": [str(eigen), 3]})}
        self.assertEqual((genau["CLAUDE.md"]["status"], genau["CLAUDE.md"]["laedt"], genau["CLAUDE.md"]["ausgeschlossen"]), ("aus", False, "eigen"))
        self.assertEqual(genau["Regel"]["status"], "aktiv")
        per_muster = {z["art"]: z for z in self.claude({"claudeMdExcludes": ["**/rules/**"]})}
        self.assertEqual((per_muster["Regel"]["status"], per_muster["Regel"]["ausgeschlossen"], per_muster["Regel"]["marken"]), ("aus", "muster", ["per Muster"]))
        self.assertEqual((regel.name, per_muster["CLAUDE.md"]["status"]), ("a.md", "aktiv"))
        self.assertEqual([z["status"] for z in self.claude({"claudeMdExcludes": "kein Array"})], ["aktiv", "aktiv"])

    def test_importe_zaehlen_nur_vorhandene_dateien(self):
        self.schreibe(self.home / "extra.md")
        self.schreibe(self.home / ".claude/teil.md")
        self.schreibe(self.home / ".claude/CLAUDE.md", "Siehe @teil.md und @~/extra.md, nicht @anthropic-ai/sdk oder m@x.de.\n"
                                                       "```\n@teil.md im Codeblock\n```\n@./teil.md nochmal\n")
        (z,) = self.claude()
        self.assertEqual((z["importe"], z["marken"]), (2, ["Importe"]))

    def test_datei_in_anderer_kodierung(self):
        (self.home / ".claude/CLAUDE.md").write_bytes("Prüfung".encode("latin-1"))
        (z,) = self.claude()
        self.assertEqual(z["status"], "aktiv")


class OutputStyles(Ordner):
    def stil(self, datei, kopf, text="t" * 40):
        return self.schreibe(self.home / ".claude/output-styles" / datei, f"---\n{kopf}\n---\n{text}")

    def test_aktiv_ist_der_gewaehlte(self):
        self.stil("einfach.md", "name: Erklär es mir einfach\ndescription: beibehalten: Ja")
        self.stil("knapp.md", "keep-coding-instructions: true")
        zeilen = self.claude({"outputStyle": "Erklär es mir einfach"})
        self.assertEqual([(z["name"], z["status"], z["laedt"], z["gewaehlt"], z["marken"]) for z in zeilen], [
            ("Erklär es mir einfach", "aktiv", True, True, ["ohne Programmier-Anweisungen"]), ("knapp", "aus", False, False, [])])
        self.assertTrue(all(z["art"] == "Output-Style" and z["bereich"] == "global" for z in zeilen))

    def test_ohne_wahl_laedt_keiner(self):
        self.stil("knapp.md", "keep-coding-instructions: TRUE")
        for settings in ({}, {"outputStyle": "default"}, {"outputStyle": 3}):
            with self.subTest(settings=settings):
                self.assertEqual([(z["status"], z["marken"]) for z in self.claude(settings)], [("aus", [])])

    def test_eingebauter_und_unbekannter_name(self):
        """Der Name unterscheidet Groß und Klein: `concise` gibt es nicht, Claude Code nimmt dann den Standard."""
        (z,) = self.claude({"outputStyle": "Concise"})
        self.assertEqual((z["name"], z["pfad"], z["status"], z["laedt"], z["marken"], z["gewaehlt"]), ("Concise", None, "aktiv", True, ["eingebaut"], True))
        (z,) = self.claude({"outputStyle": "concise"})
        self.assertEqual((z["status"], z["laedt"], z["marken"]), ("wirkungslos", False, ["unbekannt"]))


class Projekte(Ordner):
    def test_dateien_eines_projekts(self):
        p = self.tmp / "arbeit/Mein Projekt"
        self.schreibe(p / "CLAUDE.md", "c" * 400)
        self.schreibe(p / "CLAUDE.local.md")
        self.schreibe(p / ".claude/rules/a.md")
        self.schreibe(p / ".claude/rules/b.md", "---\npaths: src/**\n---\n")
        self.schreibe(p / "AGENTS.md")
        self.schreibe(self.tmp / "arbeit/CLAUDE.md", "o" * 80)
        self.schreibe(self.memory(p), "m" * 40)
        zeilen = self.claude(projekte=[p])
        self.assertEqual([(z["art"], Path(z["pfad"]).name, z["status"], z.get("geerbt", False)) for z in zeilen], [
            ("CLAUDE.md", "CLAUDE.md", "aktiv", True), ("CLAUDE.md", "CLAUDE.md", "aktiv", False), ("CLAUDE.md", "CLAUDE.local.md", "aktiv", False),
            ("Regel", "a.md", "aktiv", False), ("Regel", "b.md", "bei Bedarf", False),
            ("AGENTS.md", "AGENTS.md", "wirkungslos", False), ("Memory", "MEMORY.md", "aktiv", False)])
        self.assertEqual({z["bereich"] for z in zeilen}, {str(p)})
        self.assertEqual(sum(z["tokens"] for z in zeilen if z["laedt"]), 20 + 100 + 10 + 10 + 10)

    def test_zwei_claude_md_im_selben_projekt_fallen_auf(self):
        """Claude Code lädt CLAUDE.md und .claude/CLAUDE.md beide. Wer nur eine öffnet, hält sie für die ganze Anleitung
        (so in 3zero-Q-Ecosystem: Die zweite kam Monate später dazu, die erste wurde übersehen). CLAUDE.local.md ist
        dafür gedacht, daneben zu liegen, und eine geerbte Datei gehört nicht zum Projekt."""
        p = self.tmp / "arbeit/P"
        for datei in (p / "CLAUDE.md", p / ".claude/CLAUDE.md", p / "CLAUDE.local.md", self.tmp / "arbeit/CLAUDE.md"):
            self.schreibe(datei)
        marken = {z["name"].removeprefix(str(self.tmp)): z["marken"] for z in self.claude(projekte=[p])}
        self.assertEqual(marken, {"/arbeit/CLAUDE.md": [], "/arbeit/P/CLAUDE.md": ["zwei CLAUDE.md"],
                                  "/arbeit/P/.claude/CLAUDE.md": ["zwei CLAUDE.md"], "/arbeit/P/CLAUDE.local.md": []})
        (p / ".claude/CLAUDE.md").unlink()
        self.assertEqual([z["marken"] for z in self.claude(projekte=[p])], [[], [], []])

    def test_agents_md_laedt_nur_ohne_claude_md(self):
        p = self.tmp / "P"
        self.schreibe(p / "AGENTS.md")
        (z,) = self.claude(projekte=[p])
        self.assertEqual((z["art"], z["status"], z["laedt"]), ("AGENTS.md", "aktiv", True))

    def test_home_ist_kein_projekt_und_seine_datei_nicht_geerbt(self):
        """~ steht als Projekt in ~/.claude.json. ~/.claude/CLAUDE.md ist die globale Datei und stünde sonst in jedem
        Projekt noch einmal; ~/CLAUDE.md dagegen lädt in jedem Projekt darunter."""
        self.schreibe(self.home / ".claude/CLAUDE.md")
        self.schreibe(self.home / "CLAUDE.md")
        p = self.home / "P"
        self.schreibe(p / "CLAUDE.md")
        zeilen = self.claude(projekte=[self.home, p])
        self.assertEqual([(z["bereich"], z["name"], z.get("geerbt", False)) for z in zeilen], [
            ("global", "~/.claude/CLAUDE.md", False), (str(p), "~/CLAUDE.md", True), (str(p), "~/P/CLAUDE.md", False)])

    def test_claude_ordner_als_projekt_zeigt_die_globale_datei_nicht_noch_einmal(self):
        """Lief einmal eine Sitzung in ~/.claude, steht der Ordner als Projekt in ~/.claude.json. Seine CLAUDE.md ist
        die globale und stand dort ein zweites Mal. Sein MEMORY.md bleibt: Es lädt nur in einer Sitzung in ~/.claude."""
        self.schreibe(self.home / ".claude/CLAUDE.md")
        self.schreibe(self.memory(self.home / ".claude"))
        zeilen = self.claude(projekte=[self.home / ".claude"])
        self.assertEqual([(z["bereich"], z["art"]) for z in zeilen], [("global", "CLAUDE.md"), (str(self.home / ".claude"), "Memory")])

    def test_muster_aus_den_einstellungen_des_projekts(self):
        p = self.tmp / "P"
        datei = self.schreibe(p / "CLAUDE.md")
        self.schreibe(p / ".claude/settings.local.json", json.dumps({"claudeMdExcludes": [str(datei)], "autoMemoryEnabled": False}))
        self.schreibe(p / ".claude/settings.json", "kein json")
        self.schreibe(self.memory(p))
        zeilen = self.claude(projekte=[p])
        # nicht „eigen“: Der Eintrag steht im Projekt, das Dashboard nimmt nur aus ~/.claude/settings.json wieder heraus
        self.assertEqual([(z["art"], z["status"], z.get("ausgeschlossen")) for z in zeilen], [("CLAUDE.md", "aus", "muster"), ("Memory", "aus", None)])

    def test_memory_laedt_nur_den_anfang(self):
        p = self.tmp / "P"
        p.mkdir()
        memory = self.memory(p)
        self.schreibe(memory, "\n".join(["z" * 39] * 300))
        (z,) = self.claude(projekte=[p])
        self.assertEqual((z["tokens"], z["marken"]), ((200 * 40 - 1) // 4, ["gekürzt geladen"]))
        self.schreibe(memory, "ä" * 30_000)
        (z,) = self.claude(projekte=[p])
        self.assertEqual(z["tokens"], 12_500 // 4)

    def test_unlesbares_projekt_faellt_weg(self):
        gut, gesperrt = self.tmp / "Gut", self.tmp / "Gesperrt"
        for p in (gut, gesperrt):
            self.schreibe(p / "CLAUDE.md")
        (gesperrt / "CLAUDE.md").chmod(0)
        self.addCleanup((gesperrt / "CLAUDE.md").chmod, 0o644)
        if os.geteuid() == 0:
            self.skipTest("root liest alles")
        self.assertEqual([z["bereich"] for z in self.claude(projekte=[gut, gesperrt])], [str(gut)])


class Codex(Ordner):
    def test_erste_nicht_leere_datei_gilt(self):
        self.assertEqual(anweisungen.codex(self.home), [])
        self.schreibe(self.home / ".codex/AGENTS.md", "  \n")
        (z,) = anweisungen.codex(self.home)
        self.assertEqual((z["name"], z["status"], z["laedt"], z["tokens"]), ("~/.codex/AGENTS.md", "wirkungslos", False, 0))
        self.schreibe(self.home / ".codex/AGENTS.md", "a" * 40)
        self.assertEqual([(z["status"], z["tokens"]) for z in anweisungen.codex(self.home)], [("aktiv", 10)])
        self.schreibe(self.home / ".codex/AGENTS.override.md", "o" * 80)
        self.assertEqual([(Path(z["pfad"]).name, z["status"]) for z in anweisungen.codex(self.home)],
                         [("AGENTS.override.md", "aktiv"), ("AGENTS.md", "wirkungslos")])


if __name__ == "__main__":
    unittest.main()
