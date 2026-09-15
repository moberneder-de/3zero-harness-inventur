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
