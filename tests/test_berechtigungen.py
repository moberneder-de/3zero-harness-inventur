import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from inventur import berechtigungen as b


class Breit(unittest.TestCase):
    def test_ganzes_werkzeug_oder_interpreter(self):
        for regel in ("Bash", "Bash(*)", "Bash(:*)", "Edit", "Write(**)", "Read", "WebFetch", "mcp__github", "mcp__github__*",
                      "Bash(python3:*)", "Bash(python3 *)", "Bash(python3 -c ' *)", "Bash(npx:*)", "Bash(/usr/bin/env:*)", "Bash(sh -c *)"):
            self.assertTrue(b.breit(regel), regel)

    def test_eingeschraenkt(self):
        for regel in ("Bash(git push:*)", "Bash(npx tsc *)", "Bash(python3 -m pytest *)", "Bash(bun test *)", "WebSearch",
                      "WebFetch(domain:github.com)", "Read(~/.zshrc)", "Skill(update-config)", "mcp__github__get_me", "Bash(python3)"):
            self.assertFalse(b.breit(regel), regel)


class Claude(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        self.verwaltet = mock.patch.object(b, "VERWALTET", [self.home / "managed-settings.json"])
        self.verwaltet.start()
        self.addCleanup(self.verwaltet.stop)

    def schreibe(self, pfad, daten):
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(json.dumps(daten))

    def regeln(self, zeilen):
        return {(z["bereich"], z["art"], z["regel"]): z["marken"] for z in zeilen}

    def test_jede_zeile_nennt_ihre_datei_fuer_die_aktion(self):
        projekt = self.home / "P"
        self.schreibe(projekt / ".claude/settings.local.json", {"permissions": {"allow": ["Bash(ls)"], "defaultMode": "auto"}})
        zeilen = b.claude(self.home, [str(projekt)], set())
        self.assertEqual({z["datei"] for z in zeilen}, {str(projekt / ".claude/settings.local.json")})

    def test_doppelt_ist_eine_projektregel_die_schon_global_gilt(self):
        projekt = self.home / "P"
        self.schreibe(self.home / ".claude/settings.json", {"permissions": {"allow": ["WebSearch", "Bash(ls)"], "deny": ["Read(x)"]}})
        self.schreibe(projekt / ".claude/settings.json", {"permissions": {"allow": ["WebSearch", "Bash(pwd)"], "ask": ["Bash(ls)"]}})
        self.schreibe(projekt / ".claude/settings.local.json", {"permissions": {"allow": ["Bash(pwd)"], "deny": ["Read(x)"]}})
        marken = self.regeln(b.claude(self.home, [str(projekt)], set()))
        p = str(projekt)
        self.assertEqual(marken[(p, "allow", "WebSearch")], ["doppelt"])
        self.assertEqual(marken[(p, "deny", "Read(x)")], ["doppelt"])
        self.assertEqual(marken[("global", "allow", "WebSearch")], [])  # die globale bleibt, die im Projekt ist überflüssig
        self.assertEqual(marken[(p, "ask", "Bash(ls)")], ["widerspricht"])  # andere Art: kein Doppel
        self.assertEqual(marken[(p, "allow", "Bash(pwd)")], [])  # zweimal im selben Projekt, in zwei Dateien: nicht global

    def test_global_und_projekt_mit_marken(self):
        projekt = self.home / "P"
        self.schreibe(self.home / ".claude/settings.json", {"permissions": {
            "allow": ["Bash(git status)", "mcp__weg__suche", "mcp__da__lies"], "deny": ["Read(./.env)"],
            "defaultMode": "bypassPermissions", "additionalDirectories": ["~", "~/Daten"]}})
        self.schreibe(projekt / ".claude/settings.local.json", {"permissions": {"allow": ["Bash(python3:*)", "Read(./.env)"], "ask": ["Bash(git push:*)"]}})
        zeilen = b.claude(self.home, [str(projekt)], {"da"})
        self.assertEqual(self.regeln(zeilen), {
            ("global", "allow", "Bash(git status)"): [],
            ("global", "allow", "mcp__weg__suche"): ["verwaist"],
            ("global", "allow", "mcp__da__lies"): [],
            ("global", "deny", "Read(./.env)"): [],
            ("global", "Modus", "defaultMode: bypassPermissions"): ["breit"],
            ("global", "Ordner", "~"): ["breit"],
            ("global", "Ordner", "~/Daten"): [],
            (str(projekt), "allow", "Bash(python3:*)"): ["breit"],
            (str(projekt), "allow", "Read(./.env)"): ["widerspricht"],
            (str(projekt), "ask", "Bash(git push:*)"): [],
        })
        quellen = {z["quelle"] for z in zeilen}
        self.assertEqual(quellen, {"~/.claude/settings.json", "~/P/.claude/settings.local.json"})

    def test_home_als_projekt_liest_die_benutzerdatei_nicht_doppelt(self):
        """Claude Code führt ~ auch als Projekt; ~/.claude/settings.json ist dann dieselbe Datei."""
        self.schreibe(self.home / ".claude/settings.json", {"permissions": {"allow": ["WebSearch"]}})
        zeilen = b.claude(self.home, [str(self.home)], set())
        self.assertEqual([(z["bereich"], z["regel"]) for z in zeilen], [("global", "WebSearch")])

    def test_verwaltete_einstellungen(self):
        self.schreibe(self.home / "managed-settings.json", {"permissions": {"deny": ["WebFetch"]}})
        (zeile,) = b.claude(self.home, [], set())
        self.assertEqual((zeile["bereich"], zeile["art"], zeile["regel"], zeile["verwaltet"]), ("global", "deny", "WebFetch", True))

    def test_kaputte_dateien_und_werte(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_text("kein json")
        self.schreibe(self.home / "P/.claude/settings.json", {"permissions": {"allow": "Bash", "deny": [3, "Edit"], "defaultMode": 5}})
        self.assertEqual([(z["art"], z["regel"]) for z in b.claude(self.home, [str(self.home / "P")], set())], [("deny", "Edit")])


class Codex(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_modus_profile_vertrauen_und_regeln(self):
        (self.tmp / "da").mkdir()
        (self.tmp / "rules").mkdir()
        (self.tmp / "rules/default.rules").write_text(
            'prefix_rule(pattern=["python3", "/x/build.py"], decision="allow")\n'
            'prefix_rule(pattern=["/bin/zsh", "-lc"], decision="allow")\n'
            'prefix_rule(pattern=["git", ["push", "fetch"]], decision="prompt")\n'
            '# Kommentar\n'
            'prefix_rule(pattern=kaputt, decision="allow")\n')
        cfg = {"approval_policy": "never", "sandbox_mode": "workspace-write",
               "profiles": {"voll": {"sandbox_mode": "danger-full-access"}, "kaputt": "x"},
               "projects": {str(self.tmp / "da"): {"trust_level": "trusted"}, str(self.tmp / "weg"): {"trust_level": "trusted"}, "x": 3}}
        zeilen = b.codex(cfg, self.tmp / "rules", self.tmp)
        self.assertEqual({(z["bereich"], z["quelle"], z["art"], z["regel"]): z["marken"] for z in zeilen}, {
            ("global", "~/.codex/config.toml", "Modus", "approval_policy: never"): ["breit"],
            ("global", "~/.codex/config.toml", "Modus", "sandbox_mode: workspace-write"): [],
            ("global", "~/.codex/config.toml → profiles.voll", "Modus", "sandbox_mode: danger-full-access"): ["breit"],
            ("global", "~/rules/default.rules", "allow", "python3 /x/build.py"): [],
            ("global", "~/rules/default.rules", "allow", "/bin/zsh -lc"): ["breit"],
            ("global", "~/rules/default.rules", "prompt", "git {push|fetch}"): [],
            ("global", "~/rules/default.rules", "unlesbar", 'prefix_rule(pattern=kaputt, decision="allow")'): [],
            (str(self.tmp / "da"), "~/.codex/config.toml", "Vertrauen", "trusted"): [],
            (str(self.tmp / "weg"), "~/.codex/config.toml", "Vertrauen", "trusted"): ["verwaist"],
        })

    def test_ohne_alles(self):
        self.assertEqual(b.codex({}, self.tmp / "fehlt", self.tmp), [])


if __name__ == "__main__":
    unittest.main()
