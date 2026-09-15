import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import codex


class CodexOrdner(unittest.TestCase):
    """Nachgebautes HOME mit .codex und .agents in einem Temp-Ordner."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.patches = [mock.patch.object(codex, "HOME", self.home), mock.patch.object(codex, "CODEX", self.home / ".codex"),
                        mock.patch.object(codex, "AGENTS", self.home / ".agents"), mock.patch.object(codex, "HIER", self.tmp)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp)

    def schreibe(self, pfad, text):
        pfad = Path(pfad)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(text)
        return pfad

    def skill(self, ordner, name, beschreibung="tut etwas", implizit=True):
        datei = self.schreibe(Path(ordner) / name / "SKILL.md", f"---\nname: {name}\ndescription: {beschreibung}\n---\n")
        if not implizit:
            self.schreibe(Path(ordner) / name / "agents" / "openai.yaml", "policy:\n  allow_implicit_invocation: false\n")
        return datei


class Skills(CodexOrdner):
    def test_herkunft_aufruf_und_abschalten(self):
        agents, eigene = self.home / ".agents/skills", self.home / ".codex/skills"
        self.skill(agents, "grilling")
        self.skill(agents, "kopie")
        repo = self.tmp / "Repo"
        self.skill(repo, "skill", implizit=False)
        eigene.mkdir(parents=True)
        (eigene / "inventur").symlink_to(repo / "skill")
        self.skill(eigene, "grilling", beschreibung="gleichnamig")
        self.skill(eigene / ".system", "imagegen")
        lock = {"grilling": {"source": "mattpocock/skills", "skillPath": "skills/deprecated/grilling/SKILL.md", "installedAt": "2026-08-02T10:00:00Z"}}
        cfg = {"skills": {"config": [{"path": str(agents / "kopie" / "SKILL.md"), "enabled": False}, {"name": "x", "enabled": True}]}}
        skills = {e["name"]: e for e in codex.eigene_skills(codex.abgeschaltete(cfg), lock, {"mattpocock": "Matt Pocock"})}
        self.assertEqual(sorted(skills), ["grilling", "inventur", "kopie"])
        g = skills["grilling"]
        self.assertEqual((g["quelle"]["art"], g["quelle"]["autor"], g["installiert"], g["reife"], g["beschreibung"]),
                         ("skills-CLI", "Matt Pocock", "2026-08-02", "upstream veraltet", "tut etwas"))
        self.assertEqual((g["aufruf"], g["status"], g["id"]), ("automatisch + $", "aktiv", "skill:grilling"))
        self.assertGreater(g["tokens"], 0)
        self.assertEqual((skills["kopie"]["status"], skills["kopie"]["tokens"], skills["kopie"]["quelle"]["art"]), ("aus", 0, "lokal"))
        self.assertEqual((skills["inventur"]["aufruf"], skills["inventur"]["tokens"], skills["inventur"]["quelle"]["art"]), ("nur $", 0, "Symlink"))

    def test_eingebaute_mit_was_aus_der_bewertung(self):
        system = self.home / ".codex/skills/.system"
        self.skill(system, "imagegen", beschreibung="Bilder erzeugen")
        self.skill(system, "review-agent", implizit=False)
        eingebaut = codex.eingebaute((set(), set()), {"eingebaut": [{"name": "imagegen", "was": "Erzeugt Bilder."}]})
        self.assertEqual([(b["name"], b["was"], b["aufruf"]) for b in eingebaut],
                         [("imagegen", "Erzeugt Bilder.", "automatisch + $"), ("review-agent", "tut etwas", "nur $")])
        self.assertGreater(eingebaut[0]["tokens"], 0)


class Nutzung(CodexOrdner):
    def zeile(self, tag, payload):
        return json.dumps({"timestamp": f"{tag}T10:00:00Z", "type": "event_msg", "payload": payload})

    def test_skills_je_sitzung_mcp_je_aufruf(self):
        skill = self.skill(self.home / ".agents/skills", "archify")
        lesen = {"type": "item_completed", "item": {"type": "CommandExecution", "command": f"['/bin/zsh', '-lc', \"sed -n '1,240p' {skill}\"]"}}
        mcp = {"type": "item_completed", "item": {"type": "McpToolCall", "server": "node_repl", "tool": "js"}}
        gewaehlt = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"<skill>\n<name>archify</name>\n<path>{skill}</path>\n---"}],
                    "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["skills.selected_skill_instructions"]}}
        eins = self.schreibe(self.home / ".codex/sessions/2026/09/01/rollout-1.jsonl",
                             "\n".join([self.zeile("2026-09-01", lesen), self.zeile("2026-09-01", lesen), self.zeile("2026-09-01", mcp), "kaputt"]))
        zwei = self.schreibe(self.home / ".codex/archived_sessions/rollout-2.jsonl",
                             "\n".join([self.zeile("2026-09-03", gewaehlt), self.zeile("2026-09-03", mcp)]))
        self.assertEqual(sorted(codex.sitzungen()), sorted([eins, zwei]))
        skills, server = codex.nutzung_zaehlen(codex.sitzungen())
        self.assertEqual(codex.nutzung(skills.get(str(skill.resolve()))), {"anzahl": 2, "zuletzt": "2026-09-03"})
        self.assertEqual(codex.nutzung(server.get("node_repl")), {"anzahl": 2, "zuletzt": "2026-09-03"})
        self.assertIsNone(codex.nutzung(None))


GEHEIM = "sk-geheim-1234"


class Plugins(CodexOrdner):
    def plugin(self, marktplatz, name, version, manifest, remote=False):
        wurzel = self.home / ".codex/plugins/cache" / marktplatz / name / version
        self.schreibe(wurzel / ".codex-plugin/plugin.json", json.dumps(manifest))
        if remote:
            self.schreibe(wurzel.parent / ".codex-remote-plugin-install.json", '{"schema_version":1}')
        return wurzel

    def test_status_bestandteile_und_keine_geheimnisse(self):
        docs = self.plugin("openai-primary-runtime", "documents", "26.905", {
            "name": "documents", "version": "26.905", "description": "Dokumente", "author": {"name": "OpenAI"},
            "skills": "./skills/", "apps": "./.app.json",
            "hooks": {"hooks": {"Stop": [{"hooks": [{"type": "mcp_tool", "server": "node_repl", "tool": "turn_ended"}]}]}}})
        self.skill(docs / "skills", "documents")
        self.schreibe(docs / ".app.json", json.dumps({"apps": {"connected_documents": {"id": "connector_x"}}}))
        self.schreibe(docs / ".mcp.json", json.dumps({"mcpServers": {"docs": {"command": "node", "args": ["s.mjs"], "env": {"TOKEN": GEHEIM}}}}))
        self.plugin("openai-curated", "github", "bd2122cb", {"name": "github"})
        self.plugin("openai-curated-remote", "sites", "0.1.62", {"name": "sites"}, remote=True)
        self.plugin("openai-bundled", "chrome", "1.0", {"name": "chrome"})
        self.plugin("openai-bundled", "visualize", "1.0", {"name": "visualize"})
        cfg = {"plugins": {"documents@openai-primary-runtime": {"enabled": True}, "github@openai-curated": {"enabled": True},
                           "visualize@openai-bundled": {"enabled": False}}}
        liste, teile, hooks = codex.plugins(cfg, (set(), set()), {})
        status = {p["id"]: p["status"] for p in liste}
        self.assertEqual(status, {"plugin:chrome@openai-bundled": "nicht aktiviert", "plugin:documents@openai-primary-runtime": "aktiv",
                                  "plugin:github@openai-curated": "wirkungslos", "plugin:sites@openai-curated-remote": "im Konto",
                                  "plugin:visualize@openai-bundled": "deaktiviert"})
        d = next(p for p in liste if p["name"] == "documents")
        self.assertEqual((d["skills"], d["apps"], d["hooks"], d["autor"], d["version"], d["befehle"], d["agenten"]),
                         (["documents:documents"], ["connected_documents"], ["Stop"], "OpenAI", "26.905", [], []))
        self.assertEqual(d["mcp"], {"docs": {"command": "node", "args": ["s.mjs"]}})
        self.assertEqual([(t["name"], t["plugin"], t["status"]) for t in teile], [("documents:documents", "documents@openai-primary-runtime", "aktiv")])
        self.assertEqual([(h["id"], h["befehle"][0]["befehl"], h["status"]) for h in hooks],
                         [("hook:documents@openai-primary-runtime:Stop", "MCP node_repl.turn_ended", "aktiv")])
        self.assertNotIn(GEHEIM, json.dumps([liste, teile, hooks]))

    def test_remote_katalog_abgeschaltet_macht_curated_wirksam(self):
        self.plugin("openai-curated", "github", "bd2122cb", {"name": "github"})
        cfg = {"plugins": {"github@openai-curated": {"enabled": True}}, "features": {"remote_plugin": False}}
        self.assertEqual(codex.plugins(cfg, (set(), set()), {})[0][0]["status"], "aktiv")


class Mcp(CodexOrdner):
    def test_eigene_und_plugin_server_ohne_geheimnisse(self):
        cfg = {"mcp_servers": {"node_repl": {"command": "/x/node_repl", "env": {"KEY": GEHEIM}},
                               "computer-use": {"command": "cua", "args": ["mcp"], "enabled": False},
                               "remote": {"url": "https://example.org/mcp", "bearer_token": GEHEIM}},
               "plugins": {"a@m": {"mcp_servers": {"aus": {"enabled": False}}}}}
        plugin_liste = [{"name": "a", "marktplatz": "m", "status": "aktiv", "mcp": {"an": {"command": "a"}, "aus": {"command": "b"}}},
                        {"name": "b", "marktplatz": "m", "status": "deaktiviert", "mcp": {"x": {"command": "x"}}}]
        server = {m["id"]: m for m in codex.mcp_server(cfg, plugin_liste, {"node_repl": ["2026-09-01", "2026-09-02"]})}
        self.assertEqual({i: m["status"] for i, m in server.items()}, {
            "mcp:computer-use": "deaktiviert", "mcp:node_repl": "aktiv", "mcp:remote": "aktiv",
            "mcp:plugin:a:an": "aktiv", "mcp:plugin:a:aus": "deaktiviert", "mcp:plugin:b:x": "inaktiv (Plugin aus)"})
        self.assertEqual(server["mcp:node_repl"]["nutzung"], {"anzahl": 2, "zuletzt": "2026-09-02"})
        self.assertEqual((server["mcp:remote"]["ziel"], server["mcp:remote"]["art"], server["mcp:plugin:a:an"]["quelle"]),
                         ("https://example.org/mcp", "User", "a@m"))
        self.assertEqual((server["mcp:node_repl"]["gesperrt"], server["mcp:node_repl"]["aus_in_projekten"]), (False, []))
        self.assertNotIn(GEHEIM, json.dumps(list(server.values())))


class Befehle(unittest.TestCase):
    def test_texte_je_art(self):
        skill = {"id": "skill:grilling", "name": "grilling", "pfad": "/h/.agents/skills/grilling/SKILL.md", "quelle": {"art": "skills-CLI"}}
        self.assertEqual(codex.befehl(skill, "entfernen"), "npx skills remove --global grilling")
        self.assertEqual(codex.befehl(skill, "nur manuell"), "/h/.agents/skills/grilling/agents/openai.yaml → policy: allow_implicit_invocation: false")
        self.assertEqual(codex.befehl(skill, "abschalten"), '~/.codex/config.toml → [[skills.config]] path = "/h/.agents/skills/grilling/SKILL.md", enabled = false')
        self.assertEqual(codex.befehl({**skill, "quelle": {"art": "lokal"}}, "entfernen"), 'mv "/h/.agents/skills/grilling" ~/.Trash/')
        self.assertEqual(codex.befehl({**skill, "projekt": "/x/Kit"}, None), 'cd "/x/Kit" && npx skills remove grilling')
        self.assertEqual(codex.befehl({**skill, "plugin": "documents@m"}, "entfernen"), "codex plugin remove documents@m")
        self.assertEqual(codex.befehl({"id": "plugin:documents@m", "name": "documents", "marktplatz": "m"}, None), "codex plugin remove documents@m")
        self.assertEqual(codex.befehl({"id": "mcp:node_repl", "name": "node_repl", "art": "User"}, None), "codex mcp remove node_repl")
        self.assertEqual(codex.befehl({"id": "mcp:plugin:a:x", "name": "plugin:a:x", "art": "Plugin", "quelle": "a@m"}, None), "codex plugin remove a@m")
        self.assertEqual(codex.befehl({"id": "mcp:Kit/q", "name": "q", "projekt": "/x/Kit", "quelle": "/x/Kit/.codex/config.toml"}, None),
                         "/x/Kit/.codex/config.toml → [mcp_servers.q] entfernen")
        self.assertEqual(codex.befehl({"id": "hook:config:notify", "ereignis": "notify", "pfad": "/h/.codex/config.toml"}, None),
                         '/h/.codex/config.toml → "notify" entfernen')
        self.assertEqual(codex.befehl({"id": "hook:b@m:Stop", "ereignis": "Stop", "pfad": "/c", "plugin": "b@m"}, None), "codex plugin remove b@m")


class Inventar(CodexOrdner):
    def test_ganzer_bestand_ohne_aktionen(self):
        self.skill(self.home / ".agents/skills", "grilling")
        self.skill(self.home / ".codex/skills/.system", "imagegen")
        self.schreibe(self.home / ".codex/config.toml", '[mcp_servers.node_repl]\ncommand = "n"\n')
        self.schreibe(self.tmp / "bewertung-codex.json", json.dumps({"stand": "2026-09-15", "eintraege": {
            "skill:grilling": {"was": "Fragt nach.", "empfehlung": "behalten"}, "skill:weg": {"was": "alt"}}}))
        with mock.patch.object(codex, "updates_markieren"):
            daten = codex.inventar()
        self.assertEqual(sorted(daten), ["bewertung_stand", "eingebaut", "entfallen", "gruppen", "hinweise", "hook_tage", "hooks",
                                         "live_status", "mcp", "overrides_verwaist", "plugins", "projekt_mcp", "projekt_skills", "skills", "unbewertet"])
        self.assertEqual((daten["unbewertet"], daten["entfallen"], daten["live_status"], daten["bewertung_stand"]),
                         (["mcp:node_repl"], ["skill:weg"], None, "2026-09-15"))
        g = daten["skills"][0]
        self.assertEqual((g["bewertung"]["was"], g["aktionen"], g["befehl"]), ("Fragt nach.", {}, "mv \"%s\" ~/.Trash/" % (self.home / ".agents/skills/grilling")))
        self.assertEqual([b["name"] for b in daten["eingebaut"]], ["imagegen"])


class HooksUndProjekte(CodexOrdner):
    def test_eigene_hooks_und_notify(self):
        cfg = {"notify": ["/x/client", "turn-ended"],
               "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "pruefe.sh"}]}], "state": {"k": {"enabled": False}}}}
        self.schreibe(self.home / ".codex/hooks.json", json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "start.sh"}]}]}}))
        hooks = {h["id"]: h for h in codex.eigene_hooks(cfg)}
        self.assertEqual(sorted(hooks), ["hook:config:PreToolUse", "hook:config:notify", "hook:hooks.json:SessionStart"])
        self.assertEqual(hooks["hook:config:notify"]["befehle"][0]["befehl"], "/x/client turn-ended")
        self.assertEqual(hooks["hook:config:PreToolUse"]["befehle"][0]["matcher"], "Bash")

    def test_projekte_mit_vertrauen(self):
        kit, fremd = self.tmp / "Kit", self.tmp / "Fremd"
        self.skill(kit / ".agents/skills", "seo-audit")
        self.skill(kit / ".codex/skills", "q-lang")
        self.schreibe(kit / "skills-lock.json", json.dumps({"skills": {"seo-audit": {"source": "coreyhaines31/marketingskills"}}}))
        self.schreibe(kit / ".codex/config.toml", '[mcp_servers.q]\ncommand = "bun"\nargs = ["q.ts"]\n[mcp_servers.q.env]\nK = "' + GEHEIM + '"\n')
        self.schreibe(fremd / ".codex/config.toml", '[mcp_servers.f]\ncommand = "f"\n')
        cfg = {"projects": {str(kit): {"trust_level": "trusted"}, str(fremd): {"trust_level": "untrusted"},
                            str(self.home): {"trust_level": "trusted"}, str(self.tmp / "weg"): {"trust_level": "trusted"}}}
        with mock.patch.object(codex, "HOME", self.home):
            skills, mcp = codex.projekte(cfg, (set(), set()), {})
        self.assertEqual(sorted((s["id"], s["quelle"]["art"], s["projekt"]) for s in skills),
                         [("skill:Kit/q-lang", "lokal", str(kit)), ("skill:Kit/seo-audit", "skills-CLI", str(kit))])
        self.assertEqual(sorted((m["id"], m["status"]) for m in mcp), [("mcp:Fremd/f", "nicht vertraut"), ("mcp:Kit/q", "eingetragen")])
        self.assertNotIn(GEHEIM, json.dumps(mcp))
