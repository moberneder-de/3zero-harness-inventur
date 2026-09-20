import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import codex
import harness


class CodexOrdner(unittest.TestCase):
    """Nachgebautes HOME mit .codex und .agents in einem Temp-Ordner."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.patches = [mock.patch.object(codex, "HOME", self.home), mock.patch.object(codex, "CODEX", self.home / ".codex"),
                        mock.patch.object(codex, "AGENTS", self.home / ".agents"), mock.patch.object(codex, "CLAUDE", self.home / ".claude"),
                        mock.patch.object(codex, "HIER", self.tmp), mock.patch.object(harness, "DATEN", self.tmp)]
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

    def test_geteilte_skills_erkennen(self):
        """Ein Skill aus ~/.agents/skills lädt auch in Claude Code, sobald ~/.claude/skills darauf verweist."""
        agents, claude = self.home / ".agents/skills", self.home / ".claude/skills"
        self.skill(agents, "grilling")
        self.skill(agents, "nur-codex")
        self.skill(self.home / ".codex/skills", "pdf")
        claude.mkdir(parents=True)
        (claude / "grilling").symlink_to(agents / "grilling")
        skills = {e["name"]: e.get("geteilt", False) for e in codex.eigene_skills((set(), set()), {}, {})}
        self.assertEqual(skills, {"grilling": True, "nur-codex": False, "pdf": False})

    def test_eingebaute_mit_was_aus_der_bewertung(self):
        system = self.home / ".codex/skills/.system"
        self.skill(system, "imagegen", beschreibung="Bilder erzeugen")
        self.skill(system, "review-agent", implizit=False)
        eingebaut = codex.eingebaute((set(), set()), {"eingebaut": [{"name": "imagegen", "was": "Erzeugt Bilder."}]})
        self.assertEqual([(b["name"], b["was"], b["aufruf"]) for b in eingebaut],
                         [("imagegen", "Erzeugt Bilder.", "automatisch + $"), ("review-agent", "tut etwas", "nur $")])
        self.assertGreater(eingebaut[0]["tokens"], 0)


class ZweitesFormat(CodexOrdner):
    """Codex kennt neben SKILL.md ein zweites Format: agents/openai.yaml mit interface-Block, ohne SKILL.md.
    openai-docs liegt so vor und fehlte dadurch ganz im Inventar, obwohl es benutzt wurde."""

    YAML = ('interface:\n  display_name: "OpenAI Docs"\n'
            '  short_description: "Doku zu Codex und den Modellen"\n  icon_small: "./assets/x.svg"\n')

    def test_eingebauter_skill_ohne_skill_md(self):
        self.schreibe(self.home / ".codex/skills/.system/openai-docs/agents/openai.yaml", self.YAML)
        eingebaut = codex.eingebaute((set(), set()), {})
        self.assertEqual([(b["name"], b["was"]) for b in eingebaut], [("openai-docs", "Doku zu Codex und den Modellen")])
        self.assertGreater(eingebaut[0]["tokens"], 0)

    def test_eigener_skill_ohne_skill_md(self):
        self.schreibe(self.home / ".agents/skills/openai-docs/agents/openai.yaml", self.YAML)
        skills = codex.eigene_skills((set(), set()), {}, {})
        self.assertEqual([(s["name"], s["beschreibung"], s["aufruf"]) for s in skills],
                         [("openai-docs", "Doku zu Codex und den Modellen", "automatisch + $")])

    def test_ordner_ohne_beides_zaehlt_nicht(self):
        self.schreibe(self.home / ".agents/skills/leer/README.md", "nichts")
        self.assertEqual(codex.eigene_skills((set(), set()), {}, {}), [])


class NurManuell(CodexOrdner):
    """`allow_implicit_invocation: false` zählt nur unter `policy:`. Die alte Regex traf jeden eingerückten
    Schlüssel: Ein Skill mit einem `defaults:`-Block galt als „nur $“, bekam 0 Tokens und verlor seine Aktion,
    obwohl Codex ihn automatisch lädt. Der Server schreibt den Wert ausdrücklich nur in den policy-Block."""

    def yaml(self, text):
        ordner = self.home / ".agents/skills/x"
        self.schreibe(ordner / "agents" / "openai.yaml", text)
        return ordner

    def test_unter_policy_zaehlt(self):
        self.assertTrue(codex.nur_manuell(self.yaml("policy:\n  allow_implicit_invocation: false\n")))

    def test_unter_einem_anderen_block_zaehlt_nicht(self):
        self.assertFalse(codex.nur_manuell(self.yaml(
            "defaults:\n  allow_implicit_invocation: false\npolicy:\n  allow_implicit_invocation: true\n")))

    def test_ohne_datei_und_ohne_schluessel(self):
        self.assertFalse(codex.nur_manuell(self.home / ".agents/skills/gibt-es-nicht"))
        self.assertFalse(codex.nur_manuell(self.yaml("policy:\n  andere: false\n")))


class KaputteConfig(CodexOrdner):
    """Eine kaputte config.toml ergab still „alles aus, nichts eingetragen“: keine MCP-Server, alle Plugins
    nicht aktiviert, keine Projekte. Die nächste Bewertung schriebe Empfehlungen auf dieser Grundlage."""

    def test_kaputte_datei_faellt_auf(self):
        self.schreibe(self.home / ".codex/config.toml", "[kaputt\n")
        self.assertFalse(codex.config_lesbar())

    def test_heile_datei_und_fehlende_datei_sind_in_ordnung(self):
        self.assertTrue(codex.config_lesbar())  # es gibt sie nicht, das ist kein Fehler
        self.schreibe(self.home / ".codex/config.toml", 'model = "gpt-5"\n')
        self.assertTrue(codex.config_lesbar())


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
        self.assertEqual(codex.nutzung(skills.get(codex.nutzungsschluessel(skill))), {"anzahl": 2, "zuletzt": "2026-09-03"})
        self.assertEqual(codex.nutzung(server.get("node_repl")), {"anzahl": 2, "zuletzt": "2026-09-03"})
        self.assertIsNone(codex.nutzung(None))

    def test_nutzung_ueberlebt_ein_plugin_update(self):
        """Plugin-Skills liegen unter einem Versionsordner. Nach einem Update zeigte der Eintrag auf die neue
        Version, die gezählten Sitzungen auf die alte — jedes Plugin stand danach auf „nie genutzt“."""
        cache = self.home / ".codex/plugins/cache"
        alt = self.skill(cache / "openai-bundled/sites/0.1.43/skills", "sites-building")
        neu = self.skill(cache / "openai-curated-remote/sites/0.1.65/skills", "sites-building")
        lesen = {"type": "item_completed", "item": {"type": "CommandExecution", "command": f"['/bin/zsh', '-lc', \"cat {alt}\"]"}}
        self.schreibe(self.home / ".codex/sessions/2026/09/01/rollout-1.jsonl", self.zeile("2026-09-01", lesen))
        skills, _ = codex.nutzung_zaehlen(codex.sitzungen())
        self.assertEqual(codex.nutzung(skills.get(codex.nutzungsschluessel(neu))), {"anzahl": 1, "zuletzt": "2026-09-01"})

    def test_nutzungsschluessel_haengt_am_ordner_nicht_an_der_datei(self):
        """Ein Skill kann als SKILL.md oder als agents/openai.yaml vorliegen, und das Format wechselt: openai-docs
        wurde als SKILL.md gelesen und liegt heute als openai.yaml. Beides muss denselben Skill bezeichnen."""
        ordner = self.home / ".codex/skills/.system/openai-docs"
        self.assertEqual(codex.nutzungsschluessel(ordner / "SKILL.md"),
                         codex.nutzungsschluessel(ordner / "agents" / "openai.yaml"))

    def test_nutzungsschluessel_laesst_marktplatz_und_version_weg(self):
        cache = self.home / ".codex/plugins/cache"
        self.assertEqual(codex.nutzungsschluessel(cache / "openai-bundled/sites/0.1.43/skills/x/SKILL.md"),
                         codex.nutzungsschluessel(cache / "openai-curated-remote/sites/0.1.65/skills/x/SKILL.md"))
        self.assertNotEqual(codex.nutzungsschluessel(cache / "m/sites/1/skills/x/SKILL.md"),
                            codex.nutzungsschluessel(cache / "m/anderes/1/skills/x/SKILL.md"))
        eigen = self.schreibe(self.home / ".agents/skills/archify/SKILL.md", "---\n---\n")
        self.assertEqual(codex.nutzungsschluessel(eigen), str(eigen.parent.resolve()))
        self.assertNotEqual(codex.nutzungsschluessel(cache / "m/p/1/skills/x/SKILL.md"),
                            codex.nutzungsschluessel(cache / "m/p/1/skills/y/SKILL.md"))

    def test_sitzung_die_den_inventur_skill_waehlt_zaehlt_nicht(self):
        """Interaktive Läufe von $3zero-harness-inventur lesen selbst viele SKILL.md; das darf nicht als Nutzung zählen."""
        skill = self.skill(self.home / ".agents/skills", "archify")
        gewaehlt = {"type": "message", "role": "user", "content": [{"type": "input_text",
                    "text": f"<skill>\n<name>3zero-harness-inventur</name>\n<path>{self.home}/.codex/skills/3zero-harness-inventur/SKILL.md</path>\n---"}],
                    "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["skills.selected_skill_instructions"]}}
        lesen = {"type": "item_completed", "item": {"type": "CommandExecution", "command": f"['/bin/zsh', '-lc', \"sed -n '1,240p' {skill}\"]"}}
        mcp = {"type": "item_completed", "item": {"type": "McpToolCall", "server": "node_repl", "tool": "js"}}
        eins = self.schreibe(self.home / ".codex/sessions/2026/09/01/rollout-1.jsonl",
                             "\n".join([self.zeile("2026-09-01", gewaehlt), self.zeile("2026-09-01", lesen), self.zeile("2026-09-01", mcp)]))
        skills, server = codex.nutzung_zaehlen([eins])
        self.assertEqual((skills, server), ({}, {}))

    def test_sitzung_im_projektordner_zaehlt_nicht(self):
        """session_meta mit payload.cwd = Projektordner heißt: der Lauf war eine Bewertung, keine echte Nutzung."""
        skill = self.skill(self.home / ".agents/skills", "archify")
        meta = json.dumps({"type": "session_meta", "payload": {"cwd": str(codex.HIER)}})
        lesen = self.zeile("2026-09-02", {"type": "item_completed", "item": {"type": "CommandExecution",
                           "command": f"['/bin/zsh', '-lc', \"sed -n '1,240p' {skill}\"]"}})
        eins = self.schreibe(self.home / ".codex/sessions/2026/09/02/rollout-2.jsonl", "\n".join([meta, lesen]))
        skills, server = codex.nutzung_zaehlen([eins])
        self.assertEqual((skills, server), ({}, {}))


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

    def test_gleicher_plugin_name_in_zwei_marktplaetzen(self):
        """Sonst trügen beide Skills dieselbe ID, und eine Aktion träfe das falsche Plugin."""
        eins = self.plugin("openai-bundled", "sites", "0.1.57", {"name": "sites"})
        zwei = self.plugin("openai-curated-remote", "sites", "0.1.62", {"name": "sites"}, remote=True)
        self.skill(eins / "skills", "sites-building")
        self.skill(zwei / "skills", "sites-building")
        allein = self.plugin("openai-primary-runtime", "pdf", "1.0", {"name": "pdf"})
        self.skill(allein / "skills", "pdf")
        liste, teile, _ = codex.plugins({"plugins": {"sites@openai-bundled": {"enabled": True}}}, (set(), set()), {})
        self.assertEqual(sorted(t["id"] for t in teile),
                         ["skill:pdf:pdf", "skill:sites@openai-bundled:sites-building", "skill:sites@openai-curated-remote:sites-building"])
        self.assertEqual(sorted(p["skills"] for p in liste if p["name"] == "sites"),
                         [["sites@openai-bundled:sites-building"], ["sites@openai-curated-remote:sites-building"]])
        self.assertEqual([t["plugin"] for t in teile if t["name"].startswith("sites@openai-bundled")], ["sites@openai-bundled"])

    def test_remote_katalog_abgeschaltet_macht_curated_wirksam(self):
        self.plugin("openai-curated", "github", "bd2122cb", {"name": "github"})
        cfg = {"plugins": {"github@openai-curated": {"enabled": True}}, "features": {"remote_plugin": False}}
        self.assertEqual(codex.plugins(cfg, (set(), set()), {})[0][0]["status"], "aktiv")

    def test_kaputte_manifeste_brechen_plugins_nicht_ab(self):
        wurzel = self.home / ".codex/plugins/cache/mkt/kaputt/1.0"
        self.schreibe(wurzel / "plugin.json", json.dumps([]))
        self.schreibe(wurzel / ".mcp.json", json.dumps([1]))
        self.schreibe(wurzel / "hooks/hooks.json", json.dumps("x"))
        liste, teile, hooks = codex.plugins({}, (set(), set()), {})
        self.assertEqual([p["id"] for p in liste], ["plugin:kaputt@mkt"])
        self.assertEqual((teile, hooks), ([], []))


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

    def test_werkzeuge_und_kontext_bleiben_ungemessen(self):
        # Codex protokolliert weder die Werkzeugliste eines Servers noch seinen Anweisungsblock
        (server,) = codex.mcp_server({"mcp_servers": {"node_repl": {"command": "n"}}}, [], {})
        self.assertEqual([server["werkzeuge"], server["tokens"]], [None, None])

    def test_kaputte_mcp_servers_bricht_nicht_ab(self):
        self.assertEqual(codex.mcp_server({"mcp_servers": ["x"]}, [], {}), [])


class Befehle(unittest.TestCase):
    def test_texte_je_art(self):
        skill = {"id": "skill:grilling", "name": "grilling", "pfad": "/h/.agents/skills/grilling/SKILL.md", "quelle": {"art": "skills-CLI"}}
        self.assertEqual(codex.befehl(skill, "entfernen"), "npx skills remove --global grilling")
        self.assertEqual(codex.befehl(skill, "nur manuell"), "/h/.agents/skills/grilling/agents/openai.yaml → policy: allow_implicit_invocation: false")
        self.assertEqual(codex.befehl(skill, "abschalten"), '~/.codex/config.toml → [[skills.config]] path = "/h/.agents/skills/grilling/SKILL.md", enabled = false')
        self.assertEqual(codex.befehl({**skill, "quelle": {"art": "lokal"}}, "entfernen"), 'mv "/h/.agents/skills/grilling" ~/.Trash/')
        self.assertEqual(codex.befehl({**skill, "projekt": "/x/Kit"}, None), 'cd /x/Kit && npx skills remove grilling')
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
    def test_ganzer_bestand(self):
        self.skill(self.home / ".agents/skills", "grilling")
        self.skill(self.home / ".codex/skills/.system", "imagegen")
        self.schreibe(self.home / ".codex/config.toml", '[mcp_servers.node_repl]\ncommand = "n"\n')
        self.schreibe(self.tmp / "bewertung-codex.json", json.dumps({"stand": "2026-09-15", "eintraege": {
            "skill:grilling": {"was": "Fragt nach.", "empfehlung": "behalten"}, "skill:weg": {"was": "alt"}}}))
        with mock.patch.object(codex, "updates_markieren"):
            daten = codex.inventar()
        self.assertEqual(sorted(daten), ["bewertung_stand", "config_lesbar", "eingebaut", "entfallen", "gruppen", "hinweise", "hook_tage", "hooks",
                                         "live_status", "mcp", "overrides_verwaist", "plugins", "projekt_mcp", "projekt_skills", "skills", "unbewertet"])
        self.assertEqual((daten["unbewertet"], daten["entfallen"], daten["live_status"], daten["bewertung_stand"]),
                         (["mcp:node_repl"], ["skill:weg"], None, "2026-09-15"))
        g = daten["skills"][0]
        self.assertEqual(g["bewertung"]["was"], "Fragt nach.")
        self.assertEqual(sorted(g["aktionen"]), ["deaktivieren", "deinstallieren", "nur manuell"])
        self.assertEqual(g["befehl"], "mv \"%s\" ~/.Trash/" % (self.home / ".agents/skills/grilling"))
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

    def test_kaputte_hooks_json_bricht_eigene_hooks_nicht_ab(self):
        self.schreibe(self.home / ".codex/hooks.json", json.dumps([1]))
        self.assertEqual(codex.eigene_hooks({}), [])

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

    def test_gleichnamige_projektordner_bekommen_eigene_ids(self):
        """Wie in build.py: Zwei Projekte namens „app“ teilten sich sonst eine ID, und eine Aktion träfe das erste."""
        a, b = self.tmp / "a/app", self.tmp / "b/app"
        for projekt in (a, b):
            self.skill(projekt / ".agents/skills", "x")
            self.schreibe(projekt / ".codex/config.toml", '[mcp_servers.q]\ncommand = "bun"\n')
        with mock.patch.object(codex, "HOME", self.home):
            skills, mcp = codex.projekte({"projects": {str(a): {}, str(b): {}}}, (set(), set()), {})
        self.assertEqual(sorted(s["id"] for s in skills), sorted([f"skill:{a}/x", f"skill:{b}/x"]))
        self.assertEqual(sorted(m["id"] for m in mcp), sorted([f"mcp:{a}/q", f"mcp:{b}/q"]))

    def test_kaputte_projects_bricht_nicht_ab(self):
        self.assertEqual(codex.projekte({"mcp_servers": ["x"], "projects": ["y"]}, (set(), set()), {}), ([], []))


class Aktionen(unittest.TestCase):
    def test_plugins(self):
        p = {"id": "plugin:documents@openai-primary-runtime", "name": "documents", "marktplatz": "openai-primary-runtime", "status": "aktiv"}
        self.assertEqual(codex.aktionen(p), {
            "deinstallieren": {"argv": ["codex", "plugin", "remove", "documents@openai-primary-runtime"]},
            "deaktivieren": {"toml": {"tabelle": 'plugins."documents@openai-primary-runtime"', "schluessel": "enabled", "wert": "false"}}})
        self.assertEqual(codex.aktionen({**p, "status": "deaktiviert"}),
                         {"deinstallieren": {"argv": ["codex", "plugin", "remove", "documents@openai-primary-runtime"]}})
        konto = {"id": "plugin:sites@openai-curated-remote", "name": "sites", "marktplatz": "openai-curated-remote", "status": "im Konto"}
        self.assertEqual(codex.aktionen(konto), {"deinstallieren": {"argv": ["codex", "plugin", "remove", "sites@openai-curated-remote"]}})
        self.assertEqual(codex.aktionen({"id": 'plugin:a"b@m', "name": 'a"b', "marktplatz": "m", "status": "aktiv"})["deaktivieren"],
                         {"toml": {"tabelle": 'plugins."a\\"b@m"', "schluessel": "enabled", "wert": "false"}})

    def test_mcp_server(self):
        eigen = {"id": "mcp:node_repl", "name": "node_repl", "art": "User", "status": "aktiv"}
        self.assertEqual(codex.aktionen(eigen), {
            "deinstallieren": {"argv": ["codex", "mcp", "remove", "node_repl"]},
            "deaktivieren": {"toml": {"tabelle": "mcp_servers.node_repl", "schluessel": "enabled", "wert": "false"}}})
        self.assertEqual(codex.aktionen({**eigen, "status": "deaktiviert"}),
                         {"deinstallieren": {"argv": ["codex", "mcp", "remove", "node_repl"]}})
        self.assertEqual(codex.aktionen({"id": "mcp:computer use", "name": "computer use", "art": "User", "status": "aktiv"})["deaktivieren"],
                         {"toml": {"tabelle": 'mcp_servers."computer use"', "schluessel": "enabled", "wert": "false"}})
        plugin = {"id": "mcp:plugin:documents:docs", "name": "plugin:documents:docs", "art": "Plugin",
                  "quelle": "documents@openai-primary-runtime", "status": "aktiv"}
        self.assertEqual(codex.aktionen(plugin), {"deaktivieren": {"toml": {
            "tabelle": 'plugins."documents@openai-primary-runtime".mcp_servers.docs', "schluessel": "enabled", "wert": "false"}}})
        self.assertEqual(codex.aktionen({**plugin, "status": "inaktiv (Plugin aus)"}), {})
        self.assertEqual(codex.aktionen({"id": "mcp:Kit/q", "name": "q", "art": ".codex/config.toml", "projekt": "/x/Kit", "status": "eingetragen"}), {})

    def test_skills(self):
        cli = {"id": "skill:grilling", "name": "grilling", "typ": "Skill", "status": "aktiv", "aufruf": "automatisch + $",
               "pfad": "/h/.agents/skills/grilling/SKILL.md", "quelle": {"art": "skills-CLI"}}
        self.assertEqual(codex.aktionen({**cli, "update": True}), {
            "aktualisieren": {"argv": ["npx", "skills", "update", "grilling", "-g", "-y"]},
            "deinstallieren": {"argv": ["npx", "skills", "remove", "--global", "-y", "grilling"], "gemeinsam": True},
            "deaktivieren": {"skill_aus": "/h/.agents/skills/grilling/SKILL.md"},
            "nur manuell": {"nur_manuell": "/h/.agents/skills/grilling"}})
        eigen = {**cli, "pfad": "/h/.codex/skills/pdf/SKILL.md", "name": "pdf", "quelle": {"art": "lokal"}}
        self.assertEqual(codex.aktionen(eigen)["deinstallieren"],
                         {"argv": ["mv", "/h/.codex/skills/pdf", str(codex.HOME / ".Trash") + "/"]})
        aus = {**cli, "status": "aus", "aufruf": "nur $"}
        self.assertEqual(sorted(codex.aktionen(aus)), ["deinstallieren"])
        teil = {**cli, "id": "skill:documents:documents", "name": "documents:documents", "plugin": "documents@openai-primary-runtime",
                "pfad": "/h/.codex/plugins/cache/x/documents/1/skills/documents/SKILL.md"}
        self.assertEqual(codex.aktionen(teil), {"deaktivieren": {"skill_aus": teil["pfad"]}})
        projekt = {**cli, "id": "skill:Kit/seo", "name": "seo", "projekt": "/x/Kit"}
        self.assertEqual(codex.aktionen(projekt), {"deinstallieren": {"argv": ["npx", "skills", "remove", "-y", "seo"], "cwd": "/x/Kit"}})

    def test_deinstallieren_markiert_gemeinsame_skills(self):
        """gemeinsam=True gilt bei npx skills remove immer und bei mv nur unter ~/.agents/skills."""
        with mock.patch.object(codex, "AGENTS", Path("/x/agents")):
            agents_skill = {"id": "skill:kopie", "name": "kopie", "typ": "Skill", "status": "aktiv", "aufruf": "automatisch + $",
                            "pfad": "/x/agents/skills/kopie/SKILL.md", "quelle": {"art": "lokal"}}
            self.assertTrue(codex.aktionen(agents_skill)["deinstallieren"]["gemeinsam"])
            eigen = {**agents_skill, "pfad": "/h/.codex/skills/pdf/SKILL.md", "quelle": {"art": "Symlink"}}
            self.assertNotIn("gemeinsam", codex.aktionen(eigen)["deinstallieren"])

    def test_hooks_ohne_aktionen(self):
        self.assertEqual(codex.aktionen({"id": "hook:config:notify", "typ": "Hook"}), {})
