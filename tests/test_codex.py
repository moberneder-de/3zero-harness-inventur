import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from inventur import codex
from inventur import harness

CLI = harness.SKILLS_CLI


class CodexOrdner(unittest.TestCase):
    """Nachgebautes HOME mit .codex und .agents in einem Temp-Ordner."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        self.patches = [mock.patch.object(codex, "HOME", self.home), mock.patch.object(codex, "CODEX", self.home / ".codex"),
                        mock.patch.object(codex, "AGENTS", self.home / ".agents"), mock.patch.object(codex, "CLAUDE", self.home / ".claude"),
                        mock.patch.object(harness, "PROJEKT", self.tmp), mock.patch.object(harness, "DATEN", self.tmp)]
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
        self.assertEqual((skills["kopie"].get("abgeschaltet"), skills["grilling"].get("abgeschaltet")), (True, None))
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

    def test_aktionen_treffen_den_skill_ordner(self):
        """`pfad` ist hier agents/openai.yaml, sein Elternordner also agents/: Deinstallieren legte nur diesen
        in den Papierkorb und meldete ok, der Skill blieb liegen."""
        ordner = self.home / ".agents/skills/openai-docs"
        self.schreibe(ordner / "agents/openai.yaml", self.YAML)
        (s,) = codex.eigene_skills((set(), set()), {}, {})
        a = codex.aktionen(s)
        self.assertEqual((a["deinstallieren"]["papierkorb"], a["nur manuell"]["nur_manuell"]), (str(ordner), str(ordner)))
        self.assertEqual(codex.befehl(s, "deinstallieren"), codex.papierkorb_befehl(str(ordner)))
        self.assertEqual(codex.befehl(s, "nur manuell"), f"{ordner}/agents/openai.yaml → policy: allow_implicit_invocation: false")

    def test_beschreibung_als_blockskalar(self):
        """`short_description: >-` mit eingerückten Folgezeilen ergab die Beschreibung „>-“."""
        ordner = self.home / ".agents/skills/x"
        self.schreibe(ordner / "agents/openai.yaml",
                      "interface:\n  short_description: >-\n    Doku zu Codex\n    und den Modellen\n  icon_small: x.svg\n")
        self.assertEqual(codex.yaml_beschreibung(ordner), "Doku zu Codex und den Modellen")
        self.schreibe(ordner / "agents/openai.yaml", "interface:\n  short_description: |\n    Erste Zeile\n\n    Zweite\n")
        self.assertEqual(codex.yaml_beschreibung(ordner), "Erste Zeile\nZweite")
        self.schreibe(ordner / "agents/openai.yaml", "interface:\n  short_description:\n    erst in der nächsten Zeile\n")
        self.assertEqual(codex.yaml_beschreibung(ordner), "erst in der nächsten Zeile")

    def test_openai_yaml_mit_bom(self):
        """Mit BOM stand vor `policy:` ein unsichtbares Zeichen, der Block galt als fehlend."""
        ordner = self.home / ".agents/skills/x"
        (ordner / "agents").mkdir(parents=True)
        (ordner / "agents/openai.yaml").write_text("\ufeffpolicy:\n  allow_implicit_invocation: false\n", encoding="utf-8")
        self.assertTrue(codex.nur_manuell(ordner))

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

    def test_kommentar_am_zeilenende(self):
        """`false  # nur per $` und `policy: # x` sind gültiges YAML, zählten aber nicht, weil das Muster mit `\\s*$` endete."""
        self.assertTrue(codex.nur_manuell(self.yaml("policy:\n  allow_implicit_invocation: false  # nur per $\n")))
        self.assertTrue(codex.nur_manuell(self.yaml("policy: # x\n  allow_implicit_invocation: false\n")))

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

    def test_datei_mit_bom(self):
        """Ein Editor unter Windows schreibt gern ein BOM davor. tomllib lehnt es ab: Die Datei galt als kaputt,
        und alle MCP-Server, Plugins und Projekte verschwanden aus dem Inventar."""
        datei = self.home / ".codex/config.toml"
        datei.parent.mkdir(parents=True)
        datei.write_text('\ufeff[mcp_servers.node_repl]\ncommand = "n"\n', encoding="utf-8")
        self.assertTrue(codex.config_lesbar())
        self.assertEqual(list(codex.lies_toml(datei).get("mcp_servers", {})), ["node_repl"])


class Nutzung(CodexOrdner):
    def zeile(self, tag, payload):
        return json.dumps({"timestamp": f"{tag}T10:00:00Z", "type": "event_msg", "payload": payload})

    def test_skills_je_sitzung_mcp_je_aufruf(self):
        skill = self.skill(self.home / ".agents/skills", "archify")
        lesen = {"type": "item_completed", "item": {"type": "CommandExecution", "command": ["/bin/zsh", "-lc", f"sed -n '1,240p' {skill}"]}}
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
        lesen = {"type": "item_completed", "item": {"type": "CommandExecution", "command": ["/bin/zsh", "-lc", f"cat {alt}"]}}
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

    def test_pfade_mit_leerzeichen_relativ_und_mit_tilde(self):
        """Die alte Regex fand nur absolute Pfade ohne Leerzeichen: Aus '/…/Mein Projekt/.agents/skills/a/SKILL.md'
        wurde '/.agents/skills/a', aus `cat .agents/skills/b/SKILL.md` wurde '/skills/b'. Projekt-Skills in Ordnern
        mit Leerzeichen und relative Lesezugriffe zählten so nie. Codex protokolliert den Befehl als argv-Liste und
        das Arbeitsverzeichnis als file://-Adresse."""
        projekt = self.tmp / "Mein Projekt"
        a = self.skill(projekt / ".agents/skills", "a")
        b = self.skill(projekt / ".agents/skills", "b")
        c = self.skill(self.home / ".agents/skills", "c")
        def befehl(tag, skript, cwd=None):
            item = {"type": "CommandExecution", "command": ["/bin/zsh", "-lc", skript]}
            if cwd:
                item["cwd"] = cwd
            return self.zeile(tag, {"type": "item_completed", "item": item})
        meta = json.dumps({"type": "session_meta", "payload": {"cwd": str(projekt)}})
        eins = self.schreibe(self.home / ".codex/sessions/2026/09/01/rollout-1.jsonl", "\n".join([
            befehl("2026-09-01", f"sed -n '1,200p' '{a}'", projekt.as_uri()),
            befehl("2026-09-01", "cat .agents/skills/b/SKILL.md; ls", projekt.as_uri()),
            befehl("2026-09-01", "cat ~/.agents/skills/c/SKILL.md")]))
        # ohne cwd am Befehl gilt das der Sitzung
        zwei = self.schreibe(self.home / ".codex/sessions/2026/09/02/rollout-2.jsonl",
                             "\n".join([meta, befehl("2026-09-02", "cat .agents/skills/b/SKILL.md")]))
        skills, _ = codex.nutzung_zaehlen([eins, zwei])
        self.assertEqual([codex.nutzung(skills.get(codex.nutzungsschluessel(s))) for s in (a, b, c)],
                         [{"anzahl": 1, "zuletzt": "2026-09-01"}, {"anzahl": 2, "zuletzt": "2026-09-02"}, {"anzahl": 1, "zuletzt": "2026-09-01"}])

    def test_unvollstaendige_anfuehrungszeichen_zaehlen_wie_bisher(self):
        """Ein Skript, das shlex nicht zerlegen kann (offene Anführungszeichen, etwa in einem Heredoc), zählt absolute Pfade wie bisher."""
        skill = self.skill(self.home / ".agents/skills", "archify")
        lesen = {"type": "item_completed", "item": {"type": "CommandExecution", "command": ["/bin/zsh", "-lc", f"cat {skill}; echo \"offen"]}}
        eins = self.schreibe(self.home / ".codex/sessions/2026/09/01/rollout-1.jsonl", self.zeile("2026-09-01", lesen))
        skills, _ = codex.nutzung_zaehlen([eins])
        self.assertEqual(codex.nutzung(skills.get(codex.nutzungsschluessel(skill))), {"anzahl": 1, "zuletzt": "2026-09-01"})

    def test_sitzung_die_den_inventur_skill_waehlt_zaehlt_nicht(self):
        """Interaktive Läufe von $3zero-harness-inventur lesen selbst viele SKILL.md; das darf nicht als Nutzung zählen."""
        skill = self.skill(self.home / ".agents/skills", "archify")
        gewaehlt = {"type": "message", "role": "user", "content": [{"type": "input_text",
                    "text": f"<skill>\n<name>3zero-harness-inventur</name>\n<path>{self.home}/.codex/skills/3zero-harness-inventur/SKILL.md</path>\n---"}],
                    "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["skills.selected_skill_instructions"]}}
        lesen = {"type": "item_completed", "item": {"type": "CommandExecution", "command": ["/bin/zsh", "-lc", f"sed -n '1,240p' {skill}"]}}
        mcp = {"type": "item_completed", "item": {"type": "McpToolCall", "server": "node_repl", "tool": "js"}}
        eins = self.schreibe(self.home / ".codex/sessions/2026/09/01/rollout-1.jsonl",
                             "\n".join([self.zeile("2026-09-01", gewaehlt), self.zeile("2026-09-01", lesen), self.zeile("2026-09-01", mcp)]))
        skills, server = codex.nutzung_zaehlen([eins])
        self.assertEqual((skills, server), ({}, {}))

    def test_sitzung_im_projektordner_zaehlt_nicht(self):
        """session_meta mit payload.cwd = Projektordner heißt: der Lauf war eine Bewertung, keine echte Nutzung."""
        skill = self.skill(self.home / ".agents/skills", "archify")
        meta = json.dumps({"type": "session_meta", "payload": {"cwd": str(harness.PROJEKT)}})
        lesen = self.zeile("2026-09-02", {"type": "item_completed", "item": {"type": "CommandExecution",
                           "command": ["/bin/zsh", "-lc", f"sed -n '1,240p' {skill}"]}})
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

    def test_remote_policy_wird_aus_katalog_zugeordnet(self):
        wurzel = self.plugin("openai-curated-remote", "work-pets", "1", {"name": "work-pets"}, remote=True)
        self.schreibe(wurzel.parent / ".codex-remote-plugin-install.json", '{"remote_plugin_id":"pets-id"}')
        self.skill(wurzel / "skills", "pets")
        katalog = self.home / ".codex/cache/remote_plugin_catalog/test.json"
        for policy in ("INSTALLED_BY_DEFAULT", "AVAILABLE"):
            self.schreibe(katalog, json.dumps({"plugins": [{"id": "pets-id", "installation_policy": policy}]}))
            liste, teile, _ = codex.plugins({}, (set(), set()), {})
            self.assertEqual(liste[0]["installation_policy"], policy)
            self.assertEqual("deinstallieren" in codex.aktionen(liste[0]), policy == "AVAILABLE")
            self.assertEqual("codex plugin remove" in codex.befehl(teile[0], None), policy == "AVAILABLE")
            self.assertIn("skills.config", codex.befehl(teile[0], "abschalten"))

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

    def test_plugin_mcp_im_inventar_nur_mit_namen(self):
        """args und url eines Plugin-Servers gingen roh ins Inventar; verdeckt stehen sie nur im Ziel unter `mcp`."""
        wurzel = self.plugin("m", "docs", "1", {"name": "docs"})
        self.schreibe(wurzel / ".mcp.json", json.dumps({"mcpServers": {"srv": {"command": "node", "args": ["--token", GEHEIM]}}}))
        with mock.patch.object(codex, "updates_markieren"):
            daten = codex.inventar()
        self.assertEqual(daten["plugins"][0]["mcp"], ["srv"])
        self.assertEqual([m["name"] for m in daten["mcp"]], ["plugin:docs:srv"])
        self.assertNotIn(GEHEIM, json.dumps(daten))

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

    def test_kaputtes_plugin_kostet_nur_sich(self):
        self.plugin("m", "gut", "1.0", {"name": "gut"})
        self.plugin("m", "kaputt", "1.0", {"name": "kaputt"})
        echt = codex.manifest

        def lesen(wurzel):
            if wurzel.parent.name == "kaputt":
                raise RuntimeError("unerwartet")
            return echt(wurzel)
        codex.LESEFEHLER.clear()
        self.addCleanup(codex.LESEFEHLER.clear)
        with mock.patch.object(codex, "manifest", side_effect=lesen):
            liste, _, _ = codex.plugins({}, (set(), set()), {})
        self.assertEqual([p["id"] for p in liste], ["plugin:gut@m"])
        self.assertEqual([f["quelle"] for f in codex.LESEFEHLER], ["Plugin kaputt@m"])

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

    def test_plugin_in_zwei_marktplaetzen_behaelt_beide_server(self):
        """Sonst hießen beide mcp:plugin:docs:srv, und „deaktivieren“ beim zweiten schriebe in plugins."docs@m1"."""
        plugin_liste = [{"name": "docs", "marktplatz": m, "status": "aktiv", "mcp": {"srv": {"command": "x"}}} for m in ("m1", "m2")]
        plugin_liste.append({"name": "pdf", "marktplatz": "m1", "status": "aktiv", "mcp": {"srv": {"command": "y"}}})
        server = {m["id"]: m for m in codex.mcp_server({}, plugin_liste, {})}
        self.assertEqual(sorted(server), ["mcp:plugin:docs@m1:srv", "mcp:plugin:docs@m2:srv", "mcp:plugin:pdf:srv"])
        tabelle = codex.aktionen(server["mcp:plugin:docs@m2:srv"])["deaktivieren"]["toml"]["tabelle"]
        self.assertEqual(tabelle, 'plugins."docs@m2".mcp_servers.srv')

    def test_werkzeuge_und_kontext_bleiben_ungemessen(self):
        # Codex protokolliert weder die Werkzeugliste eines Servers noch seinen Anweisungsblock
        (server,) = codex.mcp_server({"mcp_servers": {"node_repl": {"command": "n"}}}, [], {})
        self.assertEqual([server["werkzeuge"], server["tokens"]], [None, None])

    def test_http_server_direkt_gemessen(self):
        """Codex protokolliert nichts davon; HTTP-Server fragt die Inventur deshalb selbst. Abgeschaltete nicht."""
        cfg = {"mcp_servers": {"arnie": {"url": "https://a.example/mcp"}, "aus": {"url": "https://b.example/mcp", "enabled": False}}}
        gefragt = []

        def direkt(c):
            gefragt.append(c["url"])
            return {"werkzeuge": 2, "anweisungen": 100, "namen_zeichen": 11, "schemata": 90, "am": "2026-09-28"}
        server = {m["name"]: m for m in codex.mcp_server(cfg, [], {}, direkt)}
        self.assertEqual({k: server["arnie"][k] for k in ("werkzeuge", "tokens", "schemata", "messung")},
                         {"werkzeuge": 2, "tokens": 100 + round((11 + 2 * len("mcp__arnie__")) / 4), "schemata": 90, "messung": "direkt"})
        self.assertEqual([server["aus"]["werkzeuge"], server["aus"]["tokens"]], [None, None])
        self.assertEqual(gefragt, ["https://a.example/mcp"])
        self.assertEqual((server["arnie"]["netz"], server["aus"]["netz"]),
                         ({"domain": "a.example", "anmeldung": "keine"}, {"domain": "b.example", "anmeldung": "unbekannt"}))

    def test_kaputte_mcp_servers_bricht_nicht_ab(self):
        self.assertEqual(codex.mcp_server({"mcp_servers": ["x"]}, [], {}), [])


class Befehle(unittest.TestCase):
    def test_jeder_befehl_zum_kopieren_behandelt_namen_als_text(self):
        """Wie in claude.py: Namen kommen von außen und dürfen beim Einfügen ins Terminal nichts ausführen."""
        import subprocess
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        boese = f"$(touch {tmp}/entstanden) `id` x"
        faelle = [
            ({"id": "skill:x", "name": "x", "pfad": f"/h/{boese}/x/SKILL.md", "ordner": f"/h/{boese}/x", "quelle": {"art": "lokal"}}, ["mv", f"/h/{boese}/x", "/h/.Trash/"]),
            ({"id": "skill:x", "name": boese, "pfad": "/h/x/SKILL.md", "quelle": {"art": "skills-CLI"}}, ["npx", "-y", CLI, "remove", "--global", boese]),
            ({"id": "skill:x", "name": "x", "pfad": "/h/x/SKILL.md", "plugin": f"{boese}@m", "quelle": {"art": "Plugin"}}, ["codex", "plugin", "remove", f"{boese}@m"]),
            ({"id": "plugin:x", "name": boese, "marktplatz": "m"}, ["codex", "plugin", "remove", f"{boese}@m"]),
            ({"id": "hook:x", "plugin": f"{boese}@m"}, ["codex", "plugin", "remove", f"{boese}@m"]),
            ({"id": "mcp:x", "name": "x", "art": "Plugin", "quelle": f"{boese}@m"}, ["codex", "plugin", "remove", f"{boese}@m"]),
            ({"id": "mcp:x", "name": boese, "art": "User", "quelle": "~/.codex/config.toml"}, ["codex", "mcp", "remove", boese]),
        ]
        with mock.patch("sys.platform", "darwin"):
            for e, erwartet in faelle:
                with self.subTest(e["id"], erwartet=erwartet[:3]):
                    r = subprocess.run(["sh", "-c", f"set -- {codex.befehl(e, 'entfernen')}; printf '%s\\n' \"$@\""],
                                       capture_output=True, text=True, env={"HOME": "/h"})
                    self.assertEqual(r.stdout.split("\n")[:-1], erwartet)
        self.assertFalse((tmp / "entstanden").exists())

    def test_texte_je_art(self):
        skill = {"id": "skill:grilling", "name": "grilling", "pfad": "/h/.agents/skills/grilling/SKILL.md", "ordner": "/h/.agents/skills/grilling", "quelle": {"art": "skills-CLI"}}
        self.assertEqual(codex.befehl(skill, "entfernen"), f"npx -y {CLI} remove --global grilling")
        self.assertEqual(codex.befehl(skill, "nur manuell"), "/h/.agents/skills/grilling/agents/openai.yaml → policy: allow_implicit_invocation: false")
        self.assertEqual(codex.befehl(skill, "abschalten"), '~/.codex/config.toml → [[skills.config]] path = "/h/.agents/skills/grilling/SKILL.md", enabled = false')
        with mock.patch("sys.platform", "darwin"):
            self.assertEqual(codex.befehl({**skill, "quelle": {"art": "lokal"}}, "entfernen"), 'mv /h/.agents/skills/grilling ~/.Trash/')
        with mock.patch("sys.platform", "linux"):
            self.assertEqual(codex.befehl({**skill, "quelle": {"art": "lokal"}}, "entfernen"), 'gio trash /h/.agents/skills/grilling')
        self.assertEqual(codex.befehl({**skill, "projekt": "/x/Kit"}, None), f'cd /x/Kit && npx -y {CLI} remove grilling')
        self.assertEqual(codex.befehl({**skill, "plugin": "documents@m"}, "entfernen"), "codex plugin remove documents@m")
        self.assertEqual(codex.befehl({"id": "plugin:documents@m", "name": "documents", "marktplatz": "m"}, None), "codex plugin remove documents@m")
        self.assertEqual(codex.befehl({"id": "mcp:node_repl", "name": "node_repl", "art": "User"}, None), "codex mcp remove node_repl")
        self.assertEqual(codex.befehl({"id": "mcp:plugin:a:x", "name": "plugin:a:x", "art": "Plugin", "quelle": "a@m"}, None), "codex plugin remove a@m")
        self.assertEqual(codex.befehl({"id": "mcp:Kit/q", "name": "q", "projekt": "/x/Kit", "quelle": "/x/Kit/.codex/config.toml"}, None),
                         "/x/Kit/.codex/config.toml → [mcp_servers.q] entfernen")
        self.assertEqual(codex.befehl({"id": "hook:config:notify", "ereignis": "notify", "pfad": "/h/.codex/config.toml"}, None),
                         '/h/.codex/config.toml → "notify" entfernen')
        self.assertEqual(codex.befehl({"id": "hook:b@m:Stop", "ereignis": "Stop", "pfad": "/c", "plugin": "b@m"}, None), "codex plugin remove b@m")


class Verwaist(CodexOrdner):
    def test_bloecke_fuer_pfade_die_es_nicht_mehr_gibt(self):
        """Nach einem Plugin-Update zeigt ein Block auf den alten Versionsordner: Der Skill ist wieder an, der Block wirkt nicht."""
        da = self.skill(self.home / ".agents/skills", "da")
        cfg = {"skills": {"config": [{"path": str(da), "enabled": False}, {"path": "/weg/1.0/skills/x/SKILL.md", "enabled": False},
                                     {"path": "~/auch-weg/SKILL.md", "enabled": True}, {"name": "nach-namen", "enabled": False}, "kaputt"]}}
        self.assertEqual(codex.verwaiste_pfade(cfg), ["/weg/1.0/skills/x/SKILL.md", "~/auch-weg/SKILL.md"])


class Inventar(CodexOrdner):
    def test_ganzer_bestand(self):
        self.skill(self.home / ".agents/skills", "grilling")
        self.skill(self.home / ".codex/skills/.system", "imagegen")
        self.schreibe(self.home / ".codex/config.toml", '[mcp_servers.node_repl]\ncommand = "n"\n')
        self.schreibe(self.tmp / "bewertung-codex.json", json.dumps({"stand": "2026-09-15", "eintraege": {
            "skill:grilling": {"was": "Fragt nach.", "empfehlung": "behalten"}, "skill:weg": {"was": "alt"}}}))
        with mock.patch.object(codex, "updates_markieren"):
            daten = codex.inventar()
        self.assertEqual(sorted(daten), ["bewertung_stand", "config_lesbar", "eingebaut", "entfallen", "gruppen", "hinweise", "hooks",
                                         "live_status", "mcp", "mess_tage", "overrides_verwaist", "plugins", "projekt_hooks", "projekt_mcp", "projekt_skills", "skills", "unbewertet"])
        self.assertEqual((daten["unbewertet"], daten["entfallen"], daten["live_status"], daten["bewertung_stand"]),
                         (["mcp:node_repl"], ["skill:weg"], None, "2026-09-15"))
        g = daten["skills"][0]
        self.assertEqual(g["bewertung"]["was"], "Fragt nach.")
        self.assertEqual(sorted(g["aktionen"]), ["deaktivieren", "deinstallieren", "nur manuell"])
        self.assertEqual(g["befehl"], codex.papierkorb_befehl(str(self.home / ".agents/skills/grilling")))  # ~/.Trash nur auf dem Mac
        self.assertEqual([b["name"] for b in daten["eingebaut"]], ["imagegen"])


class Agenten(CodexOrdner):
    def test_eigene_agenten_global_und_im_projekt(self):
        """Eigene Agenten sind TOML-Dateien in ~/.codex/agents und <Projekt>/.codex/agents; den Namen trägt das Feld `name`,
        nicht die Datei. Ob Codex ihre Beschreibung in jede Sitzung lädt, sagt die Doku nicht: keine Tokens, bis es belegt ist."""
        self.schreibe(self.home / ".codex/agents/pruefer.toml", 'name = "code-pruefer"\ndescription = "prüft Code"\ndeveloper_instructions = "Prüfe."\n')
        self.schreibe(self.home / ".codex/agents/kaputt.toml", "name = \n")  # lädt Codex nicht, also kein Eintrag
        kit = self.tmp / "Kit"
        self.schreibe(kit / ".codex/agents/tester.toml", 'name = "tester"\ndescription = "testet"\ndeveloper_instructions = "Teste."\n')
        self.skill(kit / ".codex/skills", "tester")
        with mock.patch.object(codex, "updates_markieren"):
            (g,) = [e for e in codex.inventar()["skills"] if e["typ"] == "Agent"]
        pfad = str(self.home / ".codex/agents/pruefer.toml")
        self.assertEqual((g["id"], g["name"], g["beschreibung"], g["aufruf"], g["tokens"], g["quelle"]["art"]),
                         ("agent:code-pruefer", "code-pruefer", "prüft Code", "als Subagent", 0, "lokal"))
        self.assertEqual(codex.aktionen(g), {"deinstallieren": {"papierkorb": pfad}})
        self.assertEqual(codex.befehl(g, "abschalten"), codex.papierkorb_befehl(codex.shellwort(pfad)))
        skills, _ = codex.projekte({"projects": {str(kit): {"trust_level": "trusted"}}}, (set(), set()), {})
        eintraege = {e["id"]: e for e in skills}
        self.assertEqual(sorted(eintraege), ["agent:Kit/tester", "skill:Kit/tester"])
        self.assertEqual(codex.aktionen(eintraege["agent:Kit/tester"]), {"deinstallieren": {"papierkorb": str(kit / ".codex/agents/tester.toml")}})


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

    def test_kaputtes_projekt_kostet_nur_sich(self):
        """Wie in claude.py: Ein unerwarteter Fehler kostet nur dieses Projekt und steht im Dashboard."""
        for name in ("A", "B"):
            self.skill(self.tmp / name / ".agents/skills", "x")
        echt = codex.skill_eintrag

        def eintrag(name, datei, *rest):
            if "/A/" in str(datei):
                raise RuntimeError("unerwartet")
            return echt(name, datei, *rest)
        cfg = {"projects": {str(self.tmp / n): {} for n in ("A", "B")}}
        codex.LESEFEHLER.clear()
        self.addCleanup(codex.LESEFEHLER.clear)
        with mock.patch.object(codex, "skill_eintrag", side_effect=eintrag):
            skills, _ = codex.projekte(cfg, (set(), set()), {})
        self.assertEqual([s["id"] for s in skills], ["skill:B/x"])
        self.assertEqual([f["quelle"] for f in codex.LESEFEHLER], [f"Projekt {self.tmp / 'A'}"])

    def test_projekt_hooks_aus_beiden_dateien(self):
        """Codex lädt Hooks auch aus <Projekt>/.codex/hooks.json und [hooks] in <Projekt>/.codex/config.toml
        (codex-rs/hooks/src/engine/discovery.rs), aber nur in vertrauten Projekten."""
        kit, fremd = self.tmp / "Kit", self.tmp / "Fremd"
        self.schreibe(kit / ".codex/config.toml", '[[hooks.PreToolUse]]\nmatcher = "Bash"\n[[hooks.PreToolUse.hooks]]\ntype = "command"\ncommand = "pruefe.sh"\n'
                                                  '[hooks.state.k]\nenabled = false\n')
        self.schreibe(kit / ".codex/hooks.json", json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "mcp_tool", "server": "q", "tool": "melde"}]}]}}))
        self.schreibe(fremd / ".codex/hooks.json", json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "x.sh"}]}]}}))
        self.schreibe(self.home / ".codex/hooks.json", json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "global.sh"}]}]}}))
        cfg = {"projects": {str(kit): {"trust_level": "trusted"}, str(fremd): {"trust_level": "untrusted"},
                            str(self.home): {"trust_level": "trusted"}, str(self.tmp / "weg"): {"trust_level": "trusted"}}}
        codex.LESEFEHLER.clear()
        self.addCleanup(codex.LESEFEHLER.clear)
        hooks = codex.projekt_hooks(cfg)
        self.assertEqual([(h["id"], h["status"], h["projekt"]) for h in hooks], [
            (f"hook:{fremd}/.codex/hooks.json:Stop", "nicht vertraut", str(fremd)),
            (f"hook:{kit}/.codex/config.toml:PreToolUse", "aktiv", str(kit)),
            (f"hook:{kit}/.codex/hooks.json:Stop", "aktiv", str(kit))])
        self.assertEqual([h["befehle"][0]["befehl"] for h in hooks], ["x.sh", "pruefe.sh", "MCP q.melde"])
        self.assertEqual((hooks[1]["quelle"]["autor"], hooks[1]["befehl"]), ("config.toml", f'{kit}/.codex/config.toml → "PreToolUse" entfernen'))
        self.assertTrue(all("aktionen" not in h for h in hooks))
        self.assertEqual(codex.LESEFEHLER, [])

    def test_projekt_hooks_in_fremder_form(self):
        a, b = self.tmp / "A", self.tmp / "B"
        self.schreibe(a / ".codex/hooks.json", json.dumps([1]))
        self.schreibe(a / ".codex/config.toml", 'hooks = "x"\n')
        self.schreibe(b / ".codex/config.toml", "kein toml [")
        self.assertEqual(codex.projekt_hooks({"projects": {str(a): {}, str(b): "x"}}), [])
        self.assertEqual(codex.projekt_hooks({"projects": ["x"]}), [])

    def test_kaputtes_projekt_kostet_nur_seine_hooks(self):
        for name in ("A", "B"):
            self.schreibe(self.tmp / name / ".codex/hooks.json", json.dumps({"hooks": {"Stop": [{"hooks": [{"command": "x"}]}]}}))
        echt = codex.hook_eintraege

        def eintraege(roh, schluessel, *rest):
            if "/A/" in str(schluessel):
                raise RuntimeError("unerwartet")
            return echt(roh, schluessel, *rest)
        codex.LESEFEHLER.clear()
        self.addCleanup(codex.LESEFEHLER.clear)
        with mock.patch.object(codex, "hook_eintraege", side_effect=eintraege):
            hooks = codex.projekt_hooks({"projects": {str(self.tmp / n): {} for n in ("A", "B")}})
        self.assertEqual([h["projekt"] for h in hooks], [str(self.tmp / "B")])
        self.assertEqual([f["quelle"] for f in codex.LESEFEHLER], [f"Projekt {self.tmp / 'A'}"])

    def test_gleichnamige_projektordner_bekommen_eigene_ids(self):
        """Wie in claude.py: Zwei Projekte namens „app“ teilten sich sonst eine ID, und eine Aktion träfe das erste."""
        a, b = self.tmp / "a/app", self.tmp / "b/app"
        for projekt in (a, b):
            self.skill(projekt / ".agents/skills", "x")
            self.schreibe(projekt / ".codex/config.toml", '[mcp_servers.q]\ncommand = "bun"\n')
        with mock.patch.object(codex, "HOME", self.home):
            skills, mcp = codex.projekte({"projects": {str(a): {}, str(b): {}}}, (set(), set()), {})
        self.assertEqual(sorted(s["id"] for s in skills), sorted([f"skill:{a}/x", f"skill:{b}/x"]))
        self.assertEqual(sorted(m["id"] for m in mcp), sorted([f"mcp:{a}/q", f"mcp:{b}/q"]))

    def test_gleicher_skill_in_beiden_projektordnern_zaehlt_einmal(self):
        """Wie bei den eigenen Skills zählt der erste: Zwei Einträge trügen sonst dieselbe ID."""
        kit = self.tmp / "Kit"
        self.skill(kit / ".agents/skills", "x")
        self.skill(kit / ".codex/skills", "x")
        with mock.patch.object(codex, "HOME", self.home):
            skills, _ = codex.projekte({"projects": {str(kit): {}}}, (set(), set()), {})
        self.assertEqual([(s["id"], s["pfad"]) for s in skills], [("skill:Kit/x", str(kit / ".agents/skills/x/SKILL.md"))])

    def test_lock_ohne_objekt_bricht_nicht_ab(self):
        """Gültiges JSON, aber kein Objekt (`[]`, `{"skills": []}`, ein Eintrag als Text) warf AttributeError
        bei `.get` — build.py stürzte ab, und ohne gelungenen Lauf startet der Server nicht."""
        kit = self.tmp / "Kit"
        self.skill(kit / ".agents/skills", "x")
        self.skill(self.home / ".agents/skills", "y")
        with mock.patch.object(codex, "HOME", self.home), mock.patch.object(codex, "updates_markieren"):
            for lock in ([], {"skills": []}, {"skills": {"x": "kaputt", "y": "kaputt"}}):
                with self.subTest(lock=lock):
                    self.schreibe(kit / "skills-lock.json", json.dumps(lock))
                    self.schreibe(self.home / ".agents/.skill-lock.json", json.dumps(lock))
                    skills, _ = codex.projekte({"projects": {str(kit): {}}}, (set(), set()), {})
                    self.assertEqual([s["quelle"]["art"] for s in skills], ["lokal"])
                    self.assertEqual([s["quelle"]["art"] for s in codex.inventar()["skills"]], ["lokal"])

    def test_kaputte_projects_bricht_nicht_ab(self):
        self.assertEqual(codex.projekte({"mcp_servers": ["x"], "projects": ["y"]}, (set(), set()), {}), ([], []))


class Aktionen(unittest.TestCase):
    def test_plugins(self):
        p = {"id": "plugin:documents@openai-primary-runtime", "name": "documents", "marktplatz": "openai-primary-runtime", "status": "aktiv"}
        self.assertEqual(codex.aktionen(p), {
            "deinstallieren": {"argv": ["codex", "plugin", "remove", "documents@openai-primary-runtime"]},
            "deaktivieren": {"toml": {"tabelle": 'plugins."documents@openai-primary-runtime"', "schluessel": "enabled", "wert": "false"}}})
        self.assertEqual(codex.aktionen({**p, "status": "deaktiviert"}), {
            "deinstallieren": {"argv": ["codex", "plugin", "remove", "documents@openai-primary-runtime"]},
            "einschalten": {"toml": {"tabelle": 'plugins."documents@openai-primary-runtime"', "schluessel": "enabled", "wert": "true"}}})
        # ohne Eintrag in config.toml: Wie Codex ein Plugin dort anlegt, legt `codex plugin add` fest, nicht der Server
        self.assertEqual(sorted(codex.aktionen({**p, "status": "nicht aktiviert"})), ["deinstallieren"])
        konto = {"id": "plugin:sites@openai-curated-remote", "name": "sites", "marktplatz": "openai-curated-remote", "status": "im Konto"}
        self.assertEqual(codex.aktionen(konto), {})
        self.assertEqual(codex.aktionen({"id": 'plugin:a"b@m', "name": 'a"b', "marktplatz": "m", "status": "aktiv"})["deaktivieren"],
                         {"toml": {"tabelle": 'plugins."a\\"b@m"', "schluessel": "enabled", "wert": "false"}})

    def test_remote_installationssperre_und_unbekannte_policy(self):
        p = {"id": "plugin:work-pets@openai-curated-remote", "name": "work-pets",
             "marktplatz": "openai-curated-remote", "status": "im Konto"}
        for policy in (None, "INSTALLED_BY_DEFAULT", "NOT_AVAILABLE"):
            e = {**p, "installation_policy": policy}
            self.assertNotIn("deinstallieren", codex.aktionen(e))
            self.assertNotIn("codex plugin remove", codex.befehl(e, "entfernen"))
        erlaubt = {**p, "installation_policy": "AVAILABLE"}
        self.assertIn("deinstallieren", codex.aktionen(erlaubt))

    def test_mcp_server(self):
        eigen = {"id": "mcp:node_repl", "name": "node_repl", "art": "User", "status": "aktiv"}
        self.assertEqual(codex.aktionen(eigen), {
            "deinstallieren": {"argv": ["codex", "mcp", "remove", "node_repl"]},
            "deaktivieren": {"toml": {"tabelle": "mcp_servers.node_repl", "schluessel": "enabled", "wert": "false"}}})
        self.assertEqual(codex.aktionen({**eigen, "status": "deaktiviert"}), {
            "deinstallieren": {"argv": ["codex", "mcp", "remove", "node_repl"]},
            "einschalten": {"toml": {"tabelle": "mcp_servers.node_repl", "schluessel": "enabled", "wert": "true"}}})
        self.assertEqual(codex.aktionen({"id": "mcp:computer use", "name": "computer use", "art": "User", "status": "aktiv"})["deaktivieren"],
                         {"toml": {"tabelle": 'mcp_servers."computer use"', "schluessel": "enabled", "wert": "false"}})
        plugin = {"id": "mcp:plugin:documents:docs", "name": "plugin:documents:docs", "art": "Plugin",
                  "quelle": "documents@openai-primary-runtime", "status": "aktiv"}
        self.assertEqual(codex.aktionen(plugin), {"deaktivieren": {"toml": {
            "tabelle": 'plugins."documents@openai-primary-runtime".mcp_servers.docs', "schluessel": "enabled", "wert": "false"}}})
        self.assertEqual(codex.aktionen({**plugin, "status": "deaktiviert"}), {"einschalten": {"toml": {
            "tabelle": 'plugins."documents@openai-primary-runtime".mcp_servers.docs', "schluessel": "enabled", "wert": "true"}}})
        self.assertEqual(codex.aktionen({**plugin, "status": "inaktiv (Plugin aus)"}), {})
        self.assertEqual(codex.aktionen({"id": "mcp:Kit/q", "name": "q", "art": ".codex/config.toml", "projekt": "/x/Kit", "status": "eingetragen"}), {})

    def test_skills(self):
        cli = {"id": "skill:grilling", "name": "grilling", "typ": "Skill", "status": "aktiv", "aufruf": "automatisch + $",
               "pfad": "/h/.agents/skills/grilling/SKILL.md", "ordner": "/h/.agents/skills/grilling", "quelle": {"art": "skills-CLI"}}
        self.assertEqual(codex.aktionen({**cli, "update": True}), {
            "aktualisieren": {"argv": ["npx", "-y", CLI, "update", "grilling", "-g", "-y"]},
            "deinstallieren": {"argv": ["npx", "-y", CLI, "remove", "--global", "-y", "grilling"], "gemeinsam": True},
            "deaktivieren": {"skill_aus": "/h/.agents/skills/grilling/SKILL.md"},
            "nur manuell": {"nur_manuell": "/h/.agents/skills/grilling"}})
        eigen = {**cli, "pfad": "/h/.codex/skills/pdf/SKILL.md", "ordner": "/h/.codex/skills/pdf", "name": "pdf", "quelle": {"art": "lokal"}}
        self.assertEqual(codex.aktionen(eigen)["deinstallieren"],
                         {"papierkorb": "/h/.codex/skills/pdf"})
        aus = {**cli, "status": "aus", "aufruf": "nur $", "abgeschaltet": True}
        self.assertEqual({a: s for a, s in codex.aktionen(aus).items() if a != "deinstallieren"}, {
            "einschalten": {"skill_an": "/h/.agents/skills/grilling/SKILL.md"},
            "wieder automatisch": {"automatisch": "/h/.agents/skills/grilling"}})
        # aus, weil sein Plugin nicht lädt oder weil config.toml ihn nach Namen abschaltet: dafür gibt es keinen Pfad-Block
        self.assertEqual(sorted(codex.aktionen({**cli, "status": "aus"})), ["deinstallieren", "nur manuell"])
        teil = {**cli, "id": "skill:documents:documents", "name": "documents:documents", "plugin": "documents@openai-primary-runtime",
                "pfad": "/h/.codex/plugins/cache/x/documents/1/skills/documents/SKILL.md"}
        self.assertEqual(codex.aktionen(teil), {"deaktivieren": {"skill_aus": teil["pfad"]}})
        projekt = {**cli, "id": "skill:Kit/seo", "name": "seo", "projekt": "/x/Kit"}
        self.assertEqual(codex.aktionen(projekt), {"deinstallieren": {"argv": ["npx", "-y", CLI, "remove", "-y", "seo"], "cwd": "/x/Kit"}})

    def test_deinstallieren_markiert_gemeinsame_skills(self):
        """gemeinsam=True gilt bei npx skills remove immer und bei mv nur unter ~/.agents/skills."""
        with mock.patch.object(codex, "AGENTS", Path("/x/agents")):
            agents_skill = {"id": "skill:kopie", "name": "kopie", "typ": "Skill", "status": "aktiv", "aufruf": "automatisch + $",
                            "pfad": "/x/agents/skills/kopie/SKILL.md", "ordner": "/x/agents/skills/kopie", "quelle": {"art": "lokal"}}
            self.assertTrue(codex.aktionen(agents_skill)["deinstallieren"]["gemeinsam"])
            eigen = {**agents_skill, "pfad": "/h/.codex/skills/pdf/SKILL.md", "ordner": "/h/.codex/skills/pdf", "quelle": {"art": "Symlink"}}
            self.assertNotIn("gemeinsam", codex.aktionen(eigen)["deinstallieren"])

    def test_hooks_ohne_aktionen(self):
        self.assertEqual(codex.aktionen({"id": "hook:config:notify", "typ": "Hook"}), {})


class Umgesetzt(unittest.TestCase):
    def test_mit_den_merkmalen_von_codex(self):
        """Wie claude.umgesetzt, aber: „aus“ zählt als abgeschaltet, ein Plugin lädt auch „im Konto“, nur manuell heißt „nur $“."""
        e = lambda id, empfehlung, **felder: {"id": id, "bewertung": {"empfehlung": empfehlung}, "override": None, "gesperrt": False, **felder}
        faelle = [
            (e("skill:a", "abschalten", status="aus"), True),
            (e("mcp:a", "abschalten", status="deaktiviert"), True),
            (e("skill:b", "abschalten", status="aktiv"), False),
            (e("plugin:p@m", "abschalten", status="im Konto"), False),
            (e("plugin:p@m", "abschalten", status="aktiv"), False),
            (e("plugin:p@m", "abschalten", status="bei Bedarf"), True),
            (e("skill:c", "nur manuell", aufruf="nur $"), True),
            (e("skill:d", "nur manuell", aufruf="nur /"), False),
            (e("skill:e", "entfernen", status="aus"), False),
            ({"id": "skill:f", "bewertung": None, "status": "aus"}, False),
        ]
        for eintrag, erwartet in faelle:
            with self.subTest(eintrag=eintrag):
                self.assertIs(codex.umgesetzt(eintrag), erwartet)
