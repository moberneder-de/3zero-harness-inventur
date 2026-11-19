import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from inventur import harness
from inventur import werkzeuge

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
                     "https://registry.npmjs.org/skills/latest": {"version": "999.0.0"}}  # neuer als jede feste SKILLS_CLI
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
        self.assertEqual((skills["installiert"], skills["neueste"], skills["status"]), (harness.SKILLS_CLI.split("@")[1], "999.0.0", "veraltet"))
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


    def test_veraltet_nur_wenn_homebrew_schon_neueres_hat(self):
        """endoflife.date kennt eine Version oft vor Homebrew. Solange Homebrew nichts Neueres anbietet, täte
        `brew upgrade` nichts: kein Befehl, und es zählt nicht als offene Aufgabe."""
        antworten = {"https://endoflife.date/api/v1/products/python/": {"result": {"releases": [
            {"name": "3.14", "latest": {"name": "3.14.7"}, "eolFrom": "2030-10-31"}]}}}
        for angebot, status, befehl in (("3.14.6", "wartet auf Homebrew", None), ("3.14.7", "veraltet", "brew upgrade python@3.14"),
                                        (None, "veraltet", "brew upgrade python@3.14")):
            with self.subTest(angebot=angebot), mock.patch.object(werkzeuge, "abrufen", side_effect=antworten.get), \
                    mock.patch.object(werkzeuge, "homebrew_version", return_value=angebot) as gefragt, \
                    mock.patch.object(werkzeuge, "installiert", side_effect=lambda w: ("3.14.6", "/opt/homebrew/Cellar/python@3.14/3.14.6/bin/python3.14")
                                      if w == "python3" else (None, None)):
                (self.tmp / "werkzeuge.json").unlink(missing_ok=True)
                python = next(w for w in werkzeuge.pruefen({"mcp": [], "projekt_mcp": [], "hooks": []}, "claude-code", self.tmp, date(2026, 9, 26))
                              if w["id"] == "python3")
            self.assertEqual((python["status"], python["befehl"], python["neueste"]), (status, befehl, "3.14.7"))
            gefragt.assert_called_once_with("python@3.14")
            self.assertEqual(werkzeuge.offen([python], []), 1 if status == "veraltet" else 0)


def git(ordner, *argv):
    subprocess.run(["git", "-C", str(ordner), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main", *argv],
                   check=True, capture_output=True)


class Quellcode(unittest.TestCase):
    """MCP-Server, die als Quellcode unter ~ liegen (meist ein Git-Klon): Pflegezustand und Abhängigkeiten."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.patch = mock.patch.object(werkzeuge, "HOME", self.home)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        # ein „Upstream“ und ein Klon davon, ohne Netz
        self.upstream = self.tmp / "upstream"
        self.upstream.mkdir()
        git(self.upstream, "init", "-q")
        (self.upstream / "index.ts").write_text("x")
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-qm", "erster")
        self.klon = self.home / ".claude/mcp-servers/notiz-mcp"
        self.klon.parent.mkdir(parents=True)
        git(self.tmp, "clone", "-q", str(self.upstream), str(self.klon))

    def test_quellen_aus_eigenen_und_projekt_servern(self):
        """Plugin-Server pflegt das Plugin-System, Programme außerhalb von ~ (etwa /opt/homebrew, selbst ein Git-Repo) gehören nicht dazu."""
        projekt = self.home / "Kit"
        (projekt / "bin").mkdir(parents=True)
        (projekt / "package.json").write_text("{}")
        (projekt / "bin/q.ts").write_text("x")
        inhalt = {"mcp": [{"name": "notiz", "art": "User", "ziel": f"{self.home}/.bun/bin/bun run {self.klon}/index.ts"},
                          {"name": "plug", "art": "Plugin", "ziel": f"node {self.klon}/index.ts"},
                          {"name": "brew", "art": "User", "ziel": "/opt/homebrew/bin/node x.js"}],
                  "projekt_mcp": [{"name": "q", "art": ".mcp.json", "ziel": "bun bin/q.ts", "projekt": str(projekt)}]}
        self.assertEqual(werkzeuge.quellen(inhalt), {str(self.klon): ["notiz"], str(projekt): ["q (Kit)"]})

    def test_upstream_aktuell_neuer_voraus(self):
        self.assertEqual(werkzeuge.upstream(self.klon, lambda url: None)["status"], "aktuell")
        (self.upstream / "neu.ts").write_text("y")
        git(self.upstream, "add", ".")
        git(self.upstream, "commit", "-qm", "zweiter")
        u = werkzeuge.upstream(self.klon, lambda url: None)
        self.assertEqual((u["status"], u["befehl"]), ("neuere Commits", f"git -C {self.klon} pull"))
        git(self.klon, "pull", "-q")
        (self.klon / "eigen.ts").write_text("z")
        git(self.klon, "add", ".")
        git(self.klon, "commit", "-qm", "eigener")
        self.assertEqual(werkzeuge.upstream(self.klon, lambda url: None)["status"], "lokal voraus")

    def test_verglichen_wird_derselbe_zweig(self):
        """Ein eigenes Projekt steht oft auf einem Arbeitszweig; gegen den Hauptzweig verglichen hieße das „neuere Commits“."""
        git(self.klon, "checkout", "-qb", "arbeit")
        (self.klon / "eigen.ts").write_text("z")
        git(self.klon, "add", ".")
        git(self.klon, "commit", "-qm", "eigener")
        self.assertEqual(werkzeuge.upstream(self.klon, lambda url: None)["status"], "nur lokal")
        git(self.klon, "push", "-q", "origin", "arbeit")
        self.assertEqual(werkzeuge.upstream(self.klon, lambda url: None)["status"], "aktuell")

    def test_lokale_aenderungen_und_kein_programm_aus_der_git_config(self):
        """core.fsmonitor in .git/config startet bei git status ein Programm; der Server liest fremde Klone."""
        marker = self.tmp / "gelaufen"
        git(self.klon, "config", "core.fsmonitor", f"touch {marker}; false")
        (self.klon / "index.ts").write_text("geändert")
        self.assertTrue(werkzeuge.upstream(self.klon, lambda url: None)["lokale_aenderungen"])
        self.assertFalse(marker.exists())

    def test_github_liefert_datum_und_ruhend(self):
        git(self.klon, "remote", "set-url", "origin", "https://github.com/chad/notiz-mcp.git")
        kopf = subprocess.run(["git", "-C", str(self.klon), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        antwort = [{"sha": kopf, "commit": {"committer": {"date": "2025-12-21T23:31:07Z"}}}]
        u = werkzeuge.upstream(self.klon, lambda url: antwort if url == "https://api.github.com/repos/chad/notiz-mcp/commits?sha=main&per_page=1" else None)
        self.assertEqual((u["status"], u["letzter_commit"], u["repo"]), ("aktuell", "2025-12-21", "chad/notiz-mcp"))
        self.assertTrue(werkzeuge.ruht(u, date(2026, 9, 26)))
        self.assertFalse(werkzeuge.ruht({**u, "letzter_commit": "2026-08-01"}, date(2026, 9, 26)))

    def test_pakete_mit_meldungen_und_update_befehl(self):
        (self.klon / "package.json").write_text(json.dumps({"dependencies": {"@mcp/sdk": "^1.25.1", "fehlt": "^1"}, "devDependencies": {"typescript": "^5"}}))
        (self.klon / "bun.lock").write_text("")
        (self.klon / "node_modules/@mcp/sdk").mkdir(parents=True)
        (self.klon / "node_modules/@mcp/sdk/package.json").write_text('{"version": "1.25.1"}')
        antworten = {
            "https://registry.npmjs.org/@mcp/sdk/latest": {"version": "1.30.1"},
            "https://api.github.com/advisories?ecosystem=npm&affects=@mcp/sdk@1.25.1": [
                {"ghsa_id": "GHSA-1", "severity": "high", "summary": "Leck", "html_url": "https://github.com/advisories/GHSA-1",
                 "vulnerabilities": [{"package": {"name": "@mcp/sdk"}, "vulnerable_version_range": "<= 1.25.3", "first_patched_version": "1.26.0"}]}]}
        pakete, befehl = werkzeuge.pakete(self.klon, antworten.get)
        self.assertEqual(pakete, [{"name": "@mcp/sdk", "installiert": "1.25.1", "neueste": "1.30.1", "meldungen": [
            {"id": "GHSA-1", "schwere": "high", "titel": "Leck", "betroffen": "<= 1.25.3", "behoben": "1.26.0", "url": "https://github.com/advisories/GHSA-1"}]}])
        self.assertEqual(befehl, f"cd {self.klon} && bun update @mcp/sdk")

    def test_aktuelle_pakete_ohne_befehl(self):
        (self.klon / "package.json").write_text(json.dumps({"dependencies": {"a": "^1"}}))
        (self.klon / "package-lock.json").write_text("{}")
        (self.klon / "node_modules/a").mkdir(parents=True)
        (self.klon / "node_modules/a/package.json").write_text('{"version": "1.0.0"}')
        antworten = {"https://registry.npmjs.org/a/latest": {"version": "1.0.0"}, "https://api.github.com/advisories?ecosystem=npm&affects=a@1.0.0": []}
        self.assertEqual(werkzeuge.pakete(self.klon, antworten.get), ([{"name": "a", "installiert": "1.0.0", "neueste": "1.0.0", "meldungen": []}], None))


class Offen(unittest.TestCase):
    def test_zaehlt_was_etwas_zu_tun_gibt(self):
        """Die Zahl am Tab „Versionen“; nach denselben Status meldet der Wochenlauf."""
        liste = [{"status": s} for s in ("aktuell", "veraltet", "ohne Pflege", "fehlt", "unbekannt")]
        quellcode = [{"befehl": "bun update x", "upstream": {}}, {"befehl": None, "upstream": {"befehl": "git pull"}},
                     {"befehl": None, "upstream": {"befehl": None}}, {"ruht": True}]
        self.assertEqual(werkzeuge.offen(liste, quellcode), 3 + 2)
        self.assertEqual(werkzeuge.offen([], []), 0)


if __name__ == "__main__":
    unittest.main()
