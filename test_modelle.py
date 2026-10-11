import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import modelle as m

GRENZE = "2026-09-20T00:00:00"


class Basis(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        self.verwaltet = mock.patch.object(m, "VERWALTET", [self.home / "managed-settings.json"])
        self.verwaltet.start()
        self.addCleanup(self.verwaltet.stop)

    def schreibe(self, pfad, daten):
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(json.dumps(daten))

    def protokoll(self, name, zeilen):
        datei = self.home / name
        datei.write_text("\n".join(z if isinstance(z, str) else json.dumps(z) for z in zeilen))
        return datei

    def werte(self, zeilen):
        return {(z["bereich"], z["name"], z["wert"]): z["marken"] for z in zeilen}


class ClaudeEingestellt(Basis):
    def test_global_projekt_und_umgebung(self):
        projekt = str(self.home / "P")
        self.schreibe(self.home / ".claude/settings.json", {
            "model": "opus[1m]", "effortLevel": "xhigh", "availableModels": ["opus", "sonnet"], "permissions": {"allow": ["Bash"]},
            "env": {"ANTHROPIC_MODEL": "claude-opus-5-5", "PATH": "/x"}})
        self.schreibe(self.home / "P/.claude/settings.json", {"model": "opus[1m]", "alwaysThinkingEnabled": True})
        self.schreibe(self.home / "P/.claude/settings.local.json", {"model": "sonnet", "effortLevel": "low"})
        zeilen = m.claude(self.home, [projekt], [], [], GRENZE)["eingestellt"]
        self.assertEqual(self.werte(zeilen), {
            ("global", "model", "opus[1m]"): [], ("global", "effortLevel", "xhigh"): [],
            ("global", "availableModels", '["opus", "sonnet"]'): [], ("global", "env.ANTHROPIC_MODEL", "claude-opus-5-5"): [],
            (projekt, "model", "opus[1m]"): [], (projekt, "alwaysThinkingEnabled", "true"): [],
            (projekt, "model", "sonnet"): ["überschreibt"], (projekt, "effortLevel", "low"): ["überschreibt"]})
        self.assertEqual({z["quelle"] for z in zeilen}, {"~/.claude/settings.json", "~/P/.claude/settings.json", "~/P/.claude/settings.local.json"})

    def test_ohne_globalen_wert_ueberschreibt_das_projekt_den_standard(self):
        projekt = str(self.home / "P")
        self.schreibe(self.home / "P/.claude/settings.json", {"model": "haiku"})
        self.assertEqual(self.werte(m.claude(self.home, [projekt], [], [], GRENZE)["eingestellt"]), {(projekt, "model", "haiku"): ["überschreibt"]})

    def test_verwaltet_und_home_als_projekt(self):
        self.schreibe(self.home / "managed-settings.json", {"model": "sonnet"})
        self.schreibe(self.home / ".claude/settings.json", {"model": "opus"})
        zeilen = m.claude(self.home, [str(self.home)], [], [], GRENZE)["eingestellt"]
        self.assertEqual([(z["bereich"], z["wert"], z.get("verwaltet", False)) for z in zeilen],
                         [("global", "sonnet", True), ("global", "opus", False)])

    def test_kaputte_datei_und_falsche_typen_fallen_weg(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_text("{kaputt")
        self.schreibe(self.home / "P/.claude/settings.json", ["keine", "tabelle"])
        self.schreibe(self.home / "Q/.claude/settings.json", {"model": "opus", "env": "kein Objekt"})
        zeilen = m.claude(self.home, [str(self.home / "P"), str(self.home / "Q")], [], [], GRENZE)["eingestellt"]
        self.assertEqual([z["wert"] for z in zeilen], ["opus"])

    def test_kopf_von_agenten_und_skills(self):
        self.schreibe(self.home / ".claude/settings.json", {"model": "opus", "effortLevel": "high"})
        projekt = str(self.home / "P")
        dateien = [
            {"bereich": "global", "eintrag": "Agent pruefer", "pfad": str(self.home / ".claude/agents/pruefer.md"), "kopf": {"model": "haiku", "effort": "high"}},
            {"bereich": "global", "eintrag": "Agent erbe", "pfad": str(self.home / ".claude/agents/erbe.md"), "kopf": {"model": "inherit"}},
            {"bereich": projekt, "eintrag": "Skill bauen", "pfad": projekt + "/.claude/skills/bauen/SKILL.md", "kopf": {"model": "opus", "effort": "low"}},
            {"bereich": "global", "eintrag": "Skill ohne", "pfad": str(self.home / "x.md"), "kopf": {"description": "nichts", "model": {}}},
        ]
        zeilen = m.claude(self.home, [], dateien, [], GRENZE)["eingestellt"][2:]
        self.assertEqual([(z["bereich"], z["eintrag"], z["name"], z["wert"], z["marken"]) for z in zeilen], [
            ("global", "Agent pruefer", "model", "haiku", ["überschreibt"]), ("global", "Agent pruefer", "effort", "high", []),
            ("global", "Agent erbe", "model", "inherit", []),
            (projekt, "Skill bauen", "model", "opus", []), (projekt, "Skill bauen", "effort", "low", ["überschreibt"])])
        self.assertEqual(zeilen[0]["quelle"], "~/.claude/agents/pruefer.md")


class ClaudeGemessen(Basis):
    def antwort(self, mid, modell, tokens, zeit="2026-09-25T10:00:00.000Z", **extra):
        return {"type": "assistant", "timestamp": zeit, "cwd": str(self.home / "P"), "isSidechain": False, "effort": "high",
                "message": {"id": mid, "model": modell, "usage": {"output_tokens": tokens, "input_tokens": 3, "cache_creation_input_tokens": 100,
                                                                     "cache_read_input_tokens": 1000}}, **extra}

    def test_je_projekt_modell_und_subagent(self):
        a = self.protokoll("a.jsonl", [
            self.antwort("m1", "claude-opus-5-5", 10),
            self.antwort("m1", "claude-opus-5-5", 40),  # dieselbe Antwort, je Inhaltsblock eine Zeile: zählt einmal, mit dem letzten Stand
            self.antwort("m2", "claude-opus-5-5", 5, effort="xhigh"),
            self.antwort("m3", "claude-haiku-4-5", 7, isSidechain=True, effort=None),
            self.antwort("m4", "claude-opus-5-5", 99, zeit="2026-09-01T10:00:00.000Z"),  # vor der Grenze
            self.antwort("m5", "<synthetic>", 0),
            {"type": "user", "timestamp": "2026-09-25T10:00:00.000Z", "message": {"model": "claude-opus-5-5"}},
            '{"type": "assistant", "message": {"model": kaputt',
        ])
        b = self.protokoll("b.jsonl", [self.antwort("m2", "claude-opus-5-5", 5),  # fortgesetzte Sitzung: dieselbe Antwort in zwei Dateien
                                       self.antwort("m6", "claude-opus-5-5", 1, cwd="/woanders")])
        self.assertEqual(m.claude(self.home, [], [], [a, b, self.home / "fehlt.jsonl"], GRENZE)["gemessen"], [
            {"projekt": "/woanders", "modell": "claude-opus-5-5", "subagent": False, "antworten": 1, "tokens": 1, "eingabe": 3, "cache_schreiben": 100, "cache_lesen": 1000,
             "aufwand": ["high"]},
            {"projekt": "~/P", "modell": "claude-opus-5-5", "subagent": False, "antworten": 2, "tokens": 45, "eingabe": 6, "cache_schreiben": 200, "cache_lesen": 2000,
             "aufwand": ["high", "xhigh"]},
            {"projekt": "~/P", "modell": "claude-haiku-4-5", "subagent": True, "antworten": 1, "tokens": 7, "eingabe": 3, "cache_schreiben": 100, "cache_lesen": 1000,
             "aufwand": []}])

    def test_arbeitsordner_und_unterordner_zaehlen_zum_projekt(self):
        p, home = str(self.home / "P"), str(self.home)
        a = self.protokoll("a.jsonl", [
            self.antwort("m1", "opus", 1),
            self.antwort("m2", "opus", 2, cwd=p + "/.claude/worktrees/agent-a1", isSidechain=True),
            self.antwort("m3", "opus", 4, cwd=p + "/docs/bilder"),
            self.antwort("m4", "opus", 8, cwd="/x/Q/.claude/worktrees/agent-b2/src"),  # Projekt nicht eingetragen: der Ordner vor dem Arbeitsordner
            self.antwort("m5", "opus", 16, cwd=home + "/Anders"),  # ~ ist auch ein Projekt, schluckt aber nicht alles darunter
            self.antwort("m6", "opus", 32, cwd=home),
            self.antwort("m7", "opus", 64, cwd=p + "-2"),  # gleicher Anfang, anderer Ordner
            self.antwort("m8", "opus", 128, cwd="/private/var/folders/43/k3/T/tmpekw5h8ur"),  # Läufe in Temp-Ordnern: je Lauf ein neuer Name
            self.antwort("m9", "opus", 256, cwd="/private/tmp/claude-501/x/scratchpad/wt"),
        ])
        zeilen = m.claude(self.home, [home, p], [], [a], GRENZE)["gemessen"]
        # Ohne Projektordner legt die Desktop-App einen Arbeitsordner an und Claude Code trägt ihn als Projekt ein:
        # Er stand im Tab „Projekte“ wie ein eigenes Projekt. Er liegt unter ~ und ist eingetragen, zählt aber als temporär.
        scratch = home + "/Library/Application Support/Claude/scratch-workspaces/30/fa/scratch-2026-10-10-0636ad"
        b = self.protokoll("b.jsonl", [self.antwort("s1", "opus", 512, cwd=scratch), self.antwort("s2", "opus", 1024, cwd=scratch + "/src")])
        self.assertEqual([(z["projekt"], z["tokens"]) for z in m.claude(self.home, [home, p, scratch], [], [b], GRENZE)["gemessen"]],
                         [("temporäre Ordner", 1536)])
        self.assertEqual([(z["projekt"], z["subagent"], z["tokens"]) for z in zeilen], [
            ("/x/Q", False, 8), ("temporäre Ordner", False, 384), ("~", False, 32), ("~/Anders", False, 16), ("~/P", False, 5), ("~/P", True, 2), ("~/P-2", False, 64)])


class Codex(Basis):
    def test_eingestellt_mit_profilen(self):
        cfg = {"model": "gpt-6-astra", "model_reasoning_effort": "medium", "review_model": "gpt-6", "approval_policy": "never",
               "model_providers": {"x": {"name": "X"}},
               "profiles": {"schnell": {"model": "gpt-6-mini", "model_reasoning_effort": "medium"}, "kaputt": "text"}}
        zeilen = m.codex(cfg, [], GRENZE, self.home)["eingestellt"]
        self.assertEqual([(z["bereich"], z["quelle"], z["name"], z["wert"], z["marken"]) for z in zeilen], [
            ("global", "~/.codex/config.toml", "model", "gpt-6-astra", []),
            ("global", "~/.codex/config.toml", "model_reasoning_effort", "medium", []),
            ("global", "~/.codex/config.toml", "review_model", "gpt-6", []),
            ("global", "~/.codex/config.toml → profiles.schnell", "model", "gpt-6-mini", ["überschreibt"]),
            ("global", "~/.codex/config.toml → profiles.schnell", "model_reasoning_effort", "medium", [])])

    def test_gemessen_je_ordner_und_modell(self):
        def turn(modell, zeit="2026-09-25T10:00:00.000Z", aufwand="low"):
            return {"type": "turn_context", "timestamp": zeit, "payload": {"cwd": str(self.home / "C"), "model": modell, "effort": aufwand}}

        def tokens(n, zeit="2026-09-25T10:00:01.000Z"):
            return {"type": "event_msg", "timestamp": zeit, "payload": {"type": "token_count", "info": {"last_token_usage": {
                "output_tokens": n, "input_tokens": 50, "cached_input_tokens": 40, "cache_write_input_tokens": 5}}}}
        s = self.protokoll("s.jsonl", [
            tokens(500),  # vor dem ersten Turn: gehört zu keinem Modell
            turn("gpt-6-astra"), tokens(100), tokens(20),
            turn("gpt-6-astra", aufwand="high"), {"type": "event_msg", "timestamp": "2026-09-25T10:00:01.000Z", "payload": {"type": "token_count", "info": None}},
            turn("codex-auto-review"), tokens(3),
            turn("gpt-6-astra", zeit="2026-09-01T10:00:00.000Z"), tokens(999, zeit="2026-09-01T10:00:01.000Z"),
            "kein json",
        ])
        self.assertEqual(m.codex({}, [s, self.home / "fehlt.jsonl"], GRENZE, self.home)["gemessen"], [
            {"projekt": "~/C", "modell": "gpt-6-astra", "subagent": False, "antworten": 2, "tokens": 120, "eingabe": 20, "cache_schreiben": 10, "cache_lesen": 80,
             "aufwand": ["high", "low"]},
            {"projekt": "~/C", "modell": "codex-auto-review", "subagent": False, "antworten": 1, "tokens": 3, "eingabe": 10, "cache_schreiben": 5, "cache_lesen": 40,
             "aufwand": ["low"]}])


if __name__ == "__main__":
    unittest.main()
