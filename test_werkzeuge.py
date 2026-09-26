import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import harness
import werkzeuge

PYTHON_LINIEN = [{"name": "3.15", "latest": "3.15.1", "eol": "2031-10-31"}, {"name": "3.14", "latest": "3.14.7", "eol": "2030-10-31"},
                 {"name": "3.9", "latest": "3.9.25", "eol": "2025-10-31"}]


class Versionen(unittest.TestCase):
    def test_version_aus_der_ausgabe(self):
        for ausgabe, erwartet in (("Python 3.14.6", "3.14.6"), ("v26.3.0", "26.3.0"), ("2.1.282 (Claude Code)", "2.1.282"),
                                  ("gh version 2.97.0 (2026-07-31)\nhttps://…", "2.97.0"), ("codex-cli 0.154.0", "0.154.0"), ("", None)):
            with self.subTest(ausgabe=ausgabe):
                self.assertEqual(werkzeuge.version_aus(ausgabe), erwartet)

    def test_status_nach_linie_und_pflege(self):
        """Veraltet heißt: in der eigenen Linie gibt es Neueres. Eine neuere Linie allein macht nichts veraltet."""
        heute = date(2026, 9, 26)
        self.assertEqual(werkzeuge.bewerten("3.14.6", PYTHON_LINIEN, heute),
                         {"status": "veraltet", "neueste": "3.14.7", "pflege_bis": "2030-10-31", "neue_linie": "3.15.1"})
        self.assertEqual(werkzeuge.bewerten("3.14.7", PYTHON_LINIEN, heute)["status"], "aktuell")
        self.assertEqual(werkzeuge.bewerten("3.9.25", PYTHON_LINIEN, heute)["status"], "ohne Pflege")
        self.assertEqual(werkzeuge.bewerten("2.101.0", "2.101.0", heute), {"status": "aktuell", "neueste": "2.101.0", "pflege_bis": None, "neue_linie": None})
        self.assertEqual(werkzeuge.bewerten("2.97.0", "2.101.0", heute)["status"], "veraltet")  # 97 < 101, nicht als Text verglichen
        self.assertEqual(werkzeuge.bewerten("1.0", None, heute)["status"], "unbekannt")
        self.assertEqual(werkzeuge.bewerten(None, "1.0", heute)["status"], "fehlt")


class Gebraucht(unittest.TestCase):
    def test_aus_mcp_servern_und_hooks(self):
        """Ein Pfad zählt nur, wenn er auf dasselbe Programm zeigt wie der Name im PATH: Codex bringt ein eigenes Node mit."""
        inhalt = {"mcp": [{"name": "upnote", "ziel": "/h/.bun/bin/bun run index.ts"}, {"name": "repl", "ziel": "/App/node repl.mjs"},
                          {"name": "fern", "ziel": "https://x.org/mcp"}, {"name": "leer", "ziel": ""}],
                  "projekt_mcp": [{"name": "q", "ziel": "bunx q", "projekt": "/x/Kit"}, {"name": "u", "ziel": "uvx tool", "projekt": "/x/Kit"}],
                  "hooks": [{"name": "Stop", "befehle": [{"befehl": 'bash "${CLAUDE_PLUGIN_ROOT}/x.sh" "${CLAUDE_PLUGIN_ROOT}/pruefen.py"'}]},
                            {"name": "Start", "befehle": [{"befehl": "npx -y tool"}, {"befehl": "MCP node_repl.turn_ended"}]}]}
        pfade = {"bun": "/h/.bun/bin/bun", "node": "/usr/bin/node"}
        with mock.patch.object(werkzeuge.shutil, "which", side_effect=lambda n: pfade.get(n)), \
                mock.patch.object(werkzeuge.os.path, "realpath", side_effect=lambda p: p):
            self.assertEqual(werkzeuge.gebraucht(inhalt), {
                "bun": ["MCP-Server upnote", "MCP-Server q (Kit)"], "uv": ["MCP-Server u (Kit)"],
                "python3": ["Hook Stop"], "node": ["Hook Start"]})


class UpdateBefehl(unittest.TestCase):
    def test_je_installationsart(self):
        for pfad, erwartet in (("/opt/homebrew/Cellar/python@3.14/3.14.6/bin/python3.14", "brew upgrade python@3.14"),
                               ("/usr/local/Cellar/gh/2.97.0/bin/gh", "brew upgrade gh"),
                               ("/h/.npm-global/lib/node_modules/@openai/codex/bin/codex.js", "npm install -g @openai/codex@latest"),
                               ("/h/.bun/bin/bun", "bun upgrade"), ("/h/.local/share/claude/versions/2.1.282", "claude update"),
                               ("/usr/bin/python3", None)):
            with self.subTest(pfad=pfad):
                self.assertEqual(werkzeuge.update_befehl(pfad), erwartet)


class Pruefen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_ganzer_ablauf_mit_zwischenspeicher(self):
        """Das Netz wird einmal am Tag gefragt; ohne Netz gilt der letzte Stand, und nichts bricht ab."""
        antworten = {"https://endoflife.date/api/v1/products/python/": {"result": {"releases": [
                         {"name": "3.14", "latest": {"name": "3.14.7"}, "eolFrom": "2030-10-31"}]}},
                     "https://registry.npmjs.org/skills/latest": {"version": "1.8.0"}}
        abrufe = []

        def abrufen(url):
            abrufe.append(url)
            return antworten.get(url)
        versionen = {"python3": "3.14.6"}
        with mock.patch.object(werkzeuge, "abrufen", side_effect=abrufen), \
                mock.patch.object(werkzeuge, "installiert", side_effect=lambda w: (versionen.get(w), f"/opt/homebrew/Cellar/{w}/1/bin/{w}")):
            liste = werkzeuge.pruefen({"mcp": [], "projekt_mcp": [], "hooks": []}, "claude-code", self.tmp, date(2026, 9, 26))
            n = len(abrufe)
            werkzeuge.pruefen({"mcp": [], "projekt_mcp": [], "hooks": []}, "claude-code", self.tmp, date(2026, 9, 26))
        self.assertEqual(len(abrufe), n)  # am selben Tag aus dem Zwischenspeicher
        eintraege = {w["id"]: w for w in liste}
        self.assertEqual((eintraege["python3"]["status"], eintraege["python3"]["befehl"]), ("veraltet", "brew upgrade python3"))
        self.assertIn("Inventur", eintraege["python3"]["gebraucht_von"][0])
        skills = eintraege["skills"]
        self.assertEqual((skills["installiert"], skills["neueste"], skills["status"]), (harness.SKILLS_CLI.split("@")[1], "1.8.0", "veraltet"))
        self.assertIn("SKILLS_CLI", skills["befehl"])  # nie ein automatisches Update der festen Version
        self.assertEqual(eintraege["claude"]["status"], "fehlt")  # Harness-CLI gehört immer dazu
        self.assertNotIn("codex", eintraege)
        self.assertTrue((self.tmp / "werkzeuge.json").is_file())

    def test_ohne_netz_bleibt_der_letzte_stand(self):
        (self.tmp / "werkzeuge.json").write_text(json.dumps({"datum": "2026-09-01", "neueste": {"npm:skills": "1.7.0"}}))
        with mock.patch.object(werkzeuge, "abrufen", return_value=None), \
                mock.patch.object(werkzeuge, "installiert", return_value=(None, None)):
            liste = werkzeuge.pruefen({"mcp": [], "projekt_mcp": [], "hooks": []}, "codex", self.tmp, date(2026, 9, 26))
        self.assertEqual(next(w for w in liste if w["id"] == "skills")["neueste"], "1.7.0")


if __name__ == "__main__":
    unittest.main()
