import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build

PAPIERKORB = str(Path.home() / ".Trash")


def plugin(status):
    return {"id": "plugin:superpowers@claude-plugins-official", "name": "superpowers", "marktplatz": "claude-plugins-official",
            "marktplatz_repo": "anthropics/claude-plugins-official", "autor": "Anthropic", "homepage": None, "version": "6.3.0",
            "installiert": "2026-03-27", "aktualisiert": "2026-09-01", "status": status, "beschreibung": "",
            "skills": [], "befehle": [], "agenten": [], "hooks": [], "mcp": {}, "tokens": 0, "nutzung": None, "aktivitaet": 0}


def skill(name, typ="Skill", quelle_art="skills-CLI", override=None, aufruf="automatisch + /", pfad=None, plugin_id=None):
    e = {"id": f"{'agent' if typ == 'Agent' else 'skill'}:{name}", "name": name, "typ": typ, "beschreibung": "",
         "status": "aus" if override == "off" else "aktiv", "override": override, "aufruf": aufruf, "im_kontext": True,
         "tokens": 10, "nutzung": None, "pfad": pfad or f"/Users/x/.claude/skills/{name}/SKILL.md", "meta_autor": None,
         "quelle": {"art": quelle_art, "autor": "Matt Pocock"}}
    if plugin_id:
        e["plugin"] = plugin_id
    return e


def mcp(name, art, quelle):
    return {"id": f"mcp:{name}", "name": name, "status": "verbunden", "aus_in_projekten": [], "art": art,
            "quelle": quelle, "ziel": "https://example.org/mcp"}


class Aktionen(unittest.TestCase):
    def test_aktives_plugin(self):
        self.assertEqual(build.aktionen(plugin("aktiv")), {
            "deinstallieren": {"argv": ["claude", "plugin", "uninstall", "superpowers@claude-plugins-official"]},
            "deaktivieren": {"argv": ["claude", "plugin", "disable", "superpowers@claude-plugins-official"]},
        })

    def test_nicht_aktives_plugin_bietet_kein_deaktivieren(self):
        for status in ("deaktiviert", "nicht aktiviert"):
            with self.subTest(status):
                self.assertEqual(list(build.aktionen(plugin(status))), ["deinstallieren"])

    def test_skill_aus_skills_cli(self):
        self.assertEqual(build.aktionen(skill("grilling")), {
            "deinstallieren": {"argv": ["npx", "skills", "remove", "--global", "-y", "grilling"]},
            "deaktivieren": {"override": "off"},
            "nur manuell": {"override": "user-invocable-only"},
        })

    def test_was_schon_gilt_wird_nicht_angeboten(self):
        self.assertEqual(list(build.aktionen(skill("find-skills", override="off"))), ["deinstallieren", "nur manuell"])
        self.assertEqual(list(build.aktionen(skill("ask-matt", aufruf="nur /"))), ["deinstallieren", "deaktivieren"])

    def test_lokaler_skill_wandert_als_ordner_in_den_papierkorb(self):
        e = skill("3zero-style", quelle_art="lokal", pfad="/Users/x/.claude/skills/3zero-style/SKILL.md")
        self.assertEqual(build.aktionen(e)["deinstallieren"],
                         {"argv": ["mv", "/Users/x/.claude/skills/3zero-style", PAPIERKORB + "/"]})

    def test_lokaler_befehl_wandert_als_datei_in_den_papierkorb(self):
        e = skill("prime", typ="Befehl", quelle_art="lokal", pfad="/Users/x/.claude/commands/prime.md")
        self.assertEqual(build.aktionen(e)["deinstallieren"],
                         {"argv": ["mv", "/Users/x/.claude/commands/prime.md", PAPIERKORB + "/"]})

    def test_plugin_bestandteil_nur_abschaltbar(self):
        e = skill("codex:rescue", typ="Befehl", quelle_art="Plugin", plugin_id="codex@openai-codex")
        self.assertEqual(build.aktionen(e), {
            "deaktivieren": {"override": "off"},
            "nur manuell": {"override": "user-invocable-only"},
        })

    def test_agent_ohne_aktionen(self):
        e = skill("code-simplifier:code-simplifier", typ="Agent", quelle_art="Plugin", aufruf="als Subagent",
                  plugin_id="code-simplifier@claude-plugins-official")
        self.assertEqual(build.aktionen(e), {})

    def test_mcp_server(self):
        faelle = [
            (mcp("sentry", "User", "~/.claude.json"),
             {"deinstallieren": {"argv": ["claude", "mcp", "remove", "sentry", "-s", "user"]},
              "deaktivieren": {"sperren": {"serverName": "sentry"}}}),
            (mcp("plugin:context7:context7", "Plugin", "context7@claude-plugins-official"),
             {"deaktivieren": {"sperren": {"serverName": "plugin:context7:context7"}}}),
            (mcp("claude.ai Gmail", "claude.ai-Connector", "claude.ai-Konto"),
             {"deaktivieren": {"sperren": {"serverName": "claude.ai Gmail"}}}),
        ]
        for e, erwartet in faelle:
            with self.subTest(e["art"]):
                self.assertEqual(build.aktionen(e), erwartet)

    def test_gesperrter_mcp_server_bietet_kein_deaktivieren(self):
        e = {**mcp("claude.ai Gmail", "claude.ai-Connector", "claude.ai-Konto"), "gesperrt": True}
        self.assertEqual(build.aktionen(e), {})

    def test_projekt_skill_nur_deinstallierbar_im_projekt(self):
        cli = {**skill("seo-audit", pfad="/x/Kit/.claude/skills/seo-audit/SKILL.md"), "id": "skill:Kit/seo-audit", "projekt": "/x/Kit"}
        self.assertEqual(build.aktionen(cli), {"deinstallieren": {"argv": ["npx", "skills", "remove", "-y", "seo-audit"], "cwd": "/x/Kit"}})
        lokal = {**skill("q-lang", quelle_art="lokal", pfad="/x/Q/.claude/skills/q-lang/SKILL.md"), "id": "skill:Q/q-lang", "projekt": "/x/Q"}
        self.assertEqual(build.aktionen(lokal), {"deinstallieren": {"argv": ["mv", "/x/Q/.claude/skills/q-lang", PAPIERKORB + "/"]}})

    def test_projekt_mcp_nur_deinstallierbar_im_projekt(self):
        def e(art, status):
            return {"id": "mcp:Q/q", "name": "q", "projekt": "/x/Q", "art": art, "status": status, "quelle": "/x/Q/.mcp.json"}
        self.assertEqual(build.aktionen(e(".mcp.json", "freigegeben")),
                         {"deinstallieren": {"argv": ["claude", "mcp", "remove", "q", "-s", "project"], "cwd": "/x/Q"}})
        self.assertEqual(build.aktionen(e("lokal", "eingetragen")),
                         {"deinstallieren": {"argv": ["claude", "mcp", "remove", "q", "-s", "local"], "cwd": "/x/Q"}})
        self.assertEqual(build.aktionen(e("lokal", "Ordner fehlt")), {})

    def test_update_nur_fuer_skills_cli_mit_neuerem_stand(self):
        self.assertEqual(build.aktionen({**skill("grilling"), "update": True})["aktualisieren"],
                         {"argv": ["npx", "skills", "update", "grilling", "-g", "-y"]})
        self.assertNotIn("aktualisieren", build.aktionen({**skill("grilling"), "update": False}))
        self.assertNotIn("aktualisieren", build.aktionen(skill("grilling")))

    def test_hook_ohne_aktionen(self):
        self.assertEqual(build.aktionen({"id": "hook:einstellungen:Stop", "typ": "Hook"}), {})


class ProjektSkills(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def schreibe(self, pfad, text):
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(text)

    def test_skills_und_befehle_mit_herkunft(self):
        kit = self.tmp / "Kit"
        self.schreibe(kit / ".claude/skills/seo-audit/SKILL.md", "---\nname: seo-audit\ndescription: SEO prüfen\n---\n")
        self.schreibe(kit / ".claude/skills/q-lang/SKILL.md", "---\nname: q-lang\ndescription: Q schreiben\n---\n")
        self.schreibe(kit / ".claude/commands/bauen.md", "# Bauen\n")
        self.schreibe(kit / "skills-lock.json", json.dumps({"skills": {"seo-audit": {"source": "coreyhaines31/marketingskills"}}}))
        eintraege = {e["id"]: e for e in build.projekt_skills([str(kit), str(self.tmp / "gibtsnicht")])}
        self.assertEqual(sorted(eintraege), ["skill:Kit/bauen", "skill:Kit/q-lang", "skill:Kit/seo-audit"])
        seo = eintraege["skill:Kit/seo-audit"]
        self.assertEqual((seo["name"], seo["projekt"], seo["quelle"]["art"], seo["quelle"]["repo"]),
                         ("seo-audit", str(kit), "skills-CLI", "coreyhaines31/marketingskills"))
        self.assertGreater(seo["tokens"], 0)
        self.assertEqual(eintraege["skill:Kit/q-lang"]["quelle"]["art"], "lokal")
        self.assertEqual(eintraege["skill:Kit/bauen"]["typ"], "Befehl")

    def test_persoenliche_skills_unterscheiden_symlink_und_kopie(self):
        repo, claude = self.tmp / "Repo", self.tmp / ".claude"
        self.schreibe(repo / "skill/SKILL.md", "---\nname: eigen\ndescription: aus dem Repo\n---\n")
        self.schreibe(repo / "commands/start.md", "# Start\n")
        self.schreibe(claude / "skills/kopie/SKILL.md", "---\nname: kopie\ndescription: kopiert\n---\n")
        (claude / "skills/eigen").symlink_to(repo / "skill")
        (claude / "commands").mkdir()
        (claude / "commands/start.md").symlink_to(repo / "commands/start.md")
        with mock.patch.object(build, "CLAUDE", claude), mock.patch.object(build, "LOCK", {}):
            arten = {e["name"]: e["quelle"]["art"] for e in build.persoenliche_skills()}
        self.assertEqual(arten, {"eigen": "Symlink", "kopie": "lokal", "start": "Symlink"})

    def test_home_ist_kein_projekt(self):
        self.assertEqual(build.projekt_skills([str(Path.home())]), [])

    def test_projekt_mcp_mit_status_aus_der_konfiguration(self):
        q = self.tmp / "Q"
        self.schreibe(q / ".mcp.json", json.dumps({"mcpServers": {
            "q": {"command": "bun", "args": ["bin/qmcp.ts"]}, "neu": {"command": "neu"}, "nein": {"command": "nein"}, "aus": {"command": "aus"}}}))
        self.schreibe(q / ".claude/settings.local.json", json.dumps({"enabledMcpjsonServers": ["q", "aus"]}))
        projekte = {
            str(q): {"disabledMcpjsonServers": ["nein"], "disabledMcpServers": ["aus"], "mcpServers": {"lokal1": {"command": "x"}}},
            str(self.tmp / "Alt"): {"mcpServers": {"upnote": {"command": "bun"}}},
            str(self.tmp / "Leer"): {},
        }
        with mock.patch.object(build, "GESPERRT", set()):
            eintraege = {e["id"]: e for e in build.projekt_mcp(projekte)}
        self.assertEqual({i: (e["art"], e["status"]) for i, e in eintraege.items()}, {
            "mcp:Q/q": (".mcp.json", "freigegeben"),
            "mcp:Q/neu": (".mcp.json", "wartet auf Freigabe"),
            "mcp:Q/nein": (".mcp.json", "abgelehnt"),
            "mcp:Q/aus": (".mcp.json", "deaktiviert"),
            "mcp:Q/lokal1": ("lokal", "eingetragen"),
            "mcp:Alt/upnote": ("lokal", "Ordner fehlt"),
        })
        self.assertEqual((eintraege["mcp:Q/q"]["projekt"], eintraege["mcp:Q/q"]["ziel"]), (str(q), "bun bin/qmcp.ts"))


class Hooks(unittest.TestCase):
    def test_eigene_hooks_und_statuszeile(self):
        settings = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bash ~/.claude/handoff-hook.sh", "timeout": 10}]}]},
                    "statusLine": {"type": "command", "command": "bash ~/.claude/statusline-command.sh"}}
        hooks = {e["id"]: e for e in build.eigene_hooks(settings, "/x/settings.json")}
        self.assertEqual(sorted(hooks), ["hook:einstellungen:Stop", "hook:einstellungen:statusLine"])
        stop = hooks["hook:einstellungen:Stop"]
        self.assertEqual((stop["ereignis"], stop["status"], stop["pfad"]), ("Stop", "aktiv", "/x/settings.json"))
        self.assertEqual(stop["befehle"], [{"befehl": "bash ~/.claude/handoff-hook.sh", "matcher": None, "bedingung": None}])
        self.assertEqual(hooks["hook:einstellungen:statusLine"]["ereignis"], "Statuszeile")

    def test_ein_eintrag_je_ereignis_mit_allen_befehlen(self):
        roh = {"PostToolUse": [
            {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "pruefen"}]},
            {"hooks": [{"type": "command", "command": "pruefen", "if": "Bash(git commit:*)"}]}]}
        (e,) = build.hook_eintraege(roh, "sg@m", {"art": "Plugin", "autor": "Anthropic"}, "/p/hooks/hooks.json", "aus")
        self.assertEqual((e["id"], e["name"], e["status"]), ("hook:sg@m:PostToolUse", "PostToolUse", "aus"))
        self.assertEqual(e["befehle"], [{"befehl": "pruefen", "matcher": "Edit|Write", "bedingung": None},
                                        {"befehl": "pruefen", "matcher": None, "bedingung": "Bash(git commit:*)"}])

    def test_ausgaben_aus_transkripten(self):
        def zeile(zeit, ereignis, befehl, stdout):
            return json.dumps({"type": "attachment", "timestamp": zeit, "attachment": {
                "type": "hook_success", "hookEvent": ereignis, "command": befehl, "stdout": stdout}}) + "\n"
        kontext = lambda text: json.dumps({"hookSpecificOutput": {"additionalContext": text}})
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        datei = tmp / "sitzung.jsonl"
        datei.write_text(
            zeile("2026-09-10T08:00:00Z", "SessionStart", "start", kontext("x" * 800))
            + zeile("2026-09-13T08:00:00Z", "SessionStart", "start", kontext("x" * 400))
            + zeile("2026-09-12T08:00:00Z", "PostToolUse", "pruefen", json.dumps({"metrics": {"pattern_hits": 0}}))
            + zeile("2026-09-12T09:00:00Z", "PostToolUse", "pruefen", kontext("y" * 40))
            + zeile("2026-09-12T10:00:00Z", "Stop", "pruefen", "nur für die Anzeige")
            + zeile("2026-09-11T08:00:00Z", "UserPromptSubmit", "erinnern", "Denk an die Tests")
            + "kein json, aber \"hook_success\"\n")
        self.assertEqual(build.hook_ausgaben([datei, tmp / "fehlt.jsonl"]), {
            ("SessionStart", "start"): {"zuletzt": "2026-09-13", "tokens": 100, "max": 200, "laeufe": 2},
            ("PostToolUse", "pruefen"): {"zuletzt": "2026-09-12", "tokens": 10, "max": 10, "laeufe": 1},
            ("UserPromptSubmit", "erinnern"): {"zuletzt": "2026-09-11", "tokens": 4, "max": 4, "laeufe": 1},
        })

    def test_messen_zaehlt_nur_sessionstart_je_sitzung(self):
        hooks = build.hook_eintraege({
            "SessionStart": [{"hooks": [{"type": "command", "command": "start"}]}],
            "PostToolUse": [{"hooks": [{"type": "command", "command": "pruefen"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "still"}]}]}, "p@m", {"art": "Plugin"}, "/p/hooks.json")
        build.messen(hooks, {("SessionStart", "start"): {"zuletzt": "2026-09-13", "tokens": 100, "max": 200, "laeufe": 2},
                             ("PostToolUse", "pruefen"): {"zuletzt": "2026-09-12", "tokens": 10, "max": 10, "laeufe": 1}})
        h = {e["ereignis"]: e for e in hooks}
        self.assertEqual((h["SessionStart"]["tokens"], h["SessionStart"]["ausgabe"]["max"]), (100, 200))
        self.assertEqual((h["PostToolUse"]["tokens"], h["PostToolUse"]["ausgabe"]["laeufe"]), (0, 1))
        self.assertEqual((h["Stop"]["tokens"], h["Stop"]["ausgabe"]), (0, None))

    def test_befehl_fuer_projekt_skills(self):
        cli = {**skill("seo-audit"), "projekt": "/x/Kit"}
        self.assertEqual(build.befehl(cli, "entfernen"), 'cd "/x/Kit" && npx skills remove seo-audit')
        lokal = {**skill("q-lang", quelle_art="lokal", pfad="/x/Q/.claude/skills/q-lang/SKILL.md"), "projekt": "/x/Q"}
        self.assertEqual(build.befehl(lokal, None), 'mv "/x/Q/.claude/skills/q-lang" ~/.Trash/')

    def test_befehl_fuer_projekt_mcp(self):
        e = {"id": "mcp:Q/q", "name": "q", "projekt": "/x/Q", "art": ".mcp.json", "status": "freigegeben"}
        self.assertEqual(build.befehl(e, "entfernen"), 'cd "/x/Q" && claude mcp remove "q" -s project')
        self.assertEqual(build.befehl({**e, "art": "lokal"}, None), 'cd "/x/Q" && claude mcp remove "q" -s local')
        self.assertEqual(build.befehl({**e, "art": "lokal", "status": "Ordner fehlt"}, None),
                         '~/.claude.json → "projects" → "/x/Q" → "mcpServers" → "q" von Hand löschen, während keine Claude-Sitzung läuft')

    def test_befehl_fuer_eigene_hooks(self):
        self.assertEqual(build.befehl({"id": "hook:einstellungen:Stop", "ereignis": "Stop", "quelle": {}}, None),
                         '~/.claude/settings.json → "hooks" → "Stop" entfernen')
        self.assertEqual(build.befehl({"id": "hook:einstellungen:statusLine", "ereignis": "Statuszeile", "quelle": {}}, None),
                         '~/.claude/settings.json → "statusLine" entfernen')


class Updates(unittest.TestCase):
    LOCK = {
        "grilling": {"source": "mattpocock/skills", "skillPath": "skills/productivity/grilling/SKILL.md", "skillFolderHash": "alt"},
        "code-review": {"source": "mattpocock/skills", "skillPath": "skills/engineering/code-review/SKILL.md", "skillFolderHash": "c1"},
        "archify": {"source": "tt-a1i/archify", "skillPath": "archify/SKILL.md", "skillFolderHash": "a1"},
        "weg": {"source": "mattpocock/skills", "skillPath": "skills/weg/SKILL.md", "skillFolderHash": "w1"},
        "offline": {"source": "x/offline", "skillPath": "SKILL.md", "skillFolderHash": "o1"},
    }

    def test_github_baum_mit_wurzel(self):
        antwort = json.dumps({"sha": "wurzel", "tree": [{"path": "archify", "type": "tree", "sha": "a1"},
                                                        {"path": "archify/SKILL.md", "type": "blob", "sha": "b"}]})
        with mock.patch.object(build.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=antwort)) as run:
            self.assertEqual(build.github_baum("tt-a1i/archify"), {"": "wurzel", "archify": "a1"})
        self.assertEqual(run.call_args.args[0], ["gh", "api", "repos/tt-a1i/archify/git/trees/HEAD?recursive=1"])

    def test_github_baum_ohne_gh_oder_netz(self):
        with mock.patch.object(build.subprocess, "run", side_effect=OSError):
            self.assertIsNone(build.github_baum("x/y"))
        with mock.patch.object(build.subprocess, "run", return_value=mock.Mock(returncode=1, stdout="")):
            self.assertIsNone(build.github_baum("x/y"))

    def test_update_je_skill_aus_dem_ordnerstand(self):
        skills = [skill(n) for n in self.LOCK] + [skill("3zero-style", quelle_art="lokal"), skill("superpowers:brainstorming", plugin_id="superpowers@x")]
        baeume = {"mattpocock/skills": {"skills/productivity/grilling": "neu", "skills/engineering/code-review": "c1"},
                  "tt-a1i/archify": {"": "r", "archify": "a1"}}
        with mock.patch.object(build, "github_baum", side_effect=lambda repo: baeume.get(repo)) as abruf:
            build.updates_markieren(skills, self.LOCK)
        self.assertEqual({e["name"]: e.get("update") for e in skills},
                         {"grilling": True, "code-review": False, "archify": False, "weg": None, "offline": None,
                          "3zero-style": None, "superpowers:brainstorming": None})
        self.assertEqual(sorted(c.args[0] for c in abruf.call_args_list), ["mattpocock/skills", "tt-a1i/archify", "x/offline"])


class McpStatus(unittest.TestCase):
    def test_gesperrt_schlaegt_live_status(self):
        cfg = {"claudeAiMcpEverConnected": ["claude.ai Gmail", "claude.ai Google Drive"]}
        live = {n: {"ziel": "https://example.org/mcp", "zustand": "✔ Connected"} for n in cfg["claudeAiMcpEverConnected"]}
        with mock.patch.object(build, "CFG", cfg), mock.patch.object(build, "GESPERRT", {"claude.ai Gmail"}):
            status = {e["name"]: (e["status"], e["gesperrt"]) for e in build.mcp_server([], live)}
        self.assertEqual(status, {"claude.ai Gmail": ("deaktiviert", True), "claude.ai Google Drive": ("verbunden", False)})

    def test_projektserver_stehen_nicht_in_der_globalen_liste(self):
        cfg = {"mcpServers": {"upnote": {"command": "bun"}}, "projects": {"/x/Alt": {"mcpServers": {"upnote": {"command": "bun"}, "nur-hier": {"command": "x"}}}}}
        with mock.patch.object(build, "CFG", cfg), mock.patch.object(build, "GESPERRT", set()):
            eintraege = build.mcp_server([], None)
        self.assertEqual([(e["name"], e["art"]) for e in eintraege], [("upnote", "User")])
        self.assertNotIn("projekte", eintraege[0])

    def test_nur_serverName_eintraege_zaehlen(self):
        self.assertEqual(build.gesperrte_namen({"deniedMcpServers": [
            {"serverName": "claude.ai Gmail"}, {"serverUrl": "https://example.org/*"}, "kaputt"]}), {"claude.ai Gmail"})


if __name__ == "__main__":
    unittest.main()
