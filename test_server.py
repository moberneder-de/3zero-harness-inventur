import http.client
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import server

SETTINGS_VORHER = '{\n  "env": {\n    "A": "1"\n  },\n  "skillOverrides": {\n    "alt": "off"\n  }\n}\n'


class Server(unittest.TestCase):
    """Echter Server auf freiem Port, Inventar mit harmlosen Befehlen statt Deinstallationen."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.marker = self.tmp / "gelaufen"
        (self.tmp / "dashboard.html").write_text('<html><script id="token" type="application/json">null</script></html>')
        self.settings = self.tmp / "settings.json"
        self.settings.write_text(SETTINGS_VORHER)
        self.schreibe_inventar([{"id": "plugin:x@y", "name": "x", "aktionen": {
            "deinstallieren": {"argv": ["touch", str(self.marker)]},
            "deaktivieren": {"argv": ["sh", "-c", "echo kaputt >&2; exit 3"]},
        }}])
        self.srv = server.Inventarserver(0, self.tmp, self.settings, "geheim")
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.server_address[1]

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        if self.pfad_vorher is not None:
            os.environ["PATH"] = self.pfad_vorher
        shutil.rmtree(self.tmp)

    pfad_vorher = None

    def claude_ersatz(self, ausgabe, code=0, name="claude"):
        """Legt ein Skript in den PATH, das seine Argumente in <tmp>/argv und seinen Ordner in <tmp>/cwd protokolliert."""
        ordner = self.tmp / "bin"
        ordner.mkdir(exist_ok=True)
        skript = ordner / name
        skript.write_text(f'#!/bin/sh\nfor a in "$@"; do echo "$a"; done > "{self.tmp}/argv"\npwd > "{self.tmp}/cwd"\n'
                          f'cat <<\'ENDE\'\n{ausgabe}\nENDE\nexit {code}\n')
        skript.chmod(0o755)
        if self.pfad_vorher is None:
            self.pfad_vorher = os.environ["PATH"]
            os.environ["PATH"] = f"{ordner}:{self.pfad_vorher}"

    def schreibe_inventar(self, plugins, skills=(), mcp=()):
        inventar = {"mcp": list(mcp), "plugins": plugins, "skills": list(skills)}
        (self.tmp / "inventar.json").write_text(json.dumps(inventar))

    def anfrage(self, methode, pfad, daten=None, token="geheim", host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        kopf = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            kopf["X-Token"] = token
        body = json.dumps(daten) if isinstance(daten, dict) else daten
        conn.request(methode, pfad, body, kopf)
        antwort = conn.getresponse()
        inhalt = antwort.read().decode()
        conn.close()
        return antwort.status, inhalt

    def ausfuehren(self, eid, aktion, **kw):
        status, inhalt = self.anfrage("POST", "/api/ausfuehren", {"id": eid, "aktion": aktion}, **kw)
        return status, (json.loads(inhalt) if status == 200 else inhalt)

    def test_seite_bekommt_token(self):
        status, html = self.anfrage("GET", "/", token=None)
        self.assertEqual(status, 200)
        self.assertIn('<script id="token" type="application/json">"geheim"</script>', html)

    def test_fremder_host_bekommt_weder_seite_noch_ausfuehrung(self):
        status, html = self.anfrage("GET", "/", token=None, host=f"boese.example:{self.port}")
        self.assertEqual(status, 403)
        self.assertNotIn("geheim", html)
        status, _ = self.ausfuehren("plugin:x@y", "deinstallieren", host=f"boese.example:{self.port}")
        self.assertEqual(status, 403)
        self.assertFalse(self.marker.exists())

    def test_ohne_gueltiges_token_laeuft_nichts(self):
        for token in (None, "falsch"):
            with self.subTest(token=token):
                status, _ = self.ausfuehren("plugin:x@y", "deinstallieren", token=token)
                self.assertEqual(status, 403)
        self.assertFalse(self.marker.exists())

    def test_aktion_laeuft(self):
        status, antwort = self.ausfuehren("plugin:x@y", "deinstallieren")
        self.assertEqual((status, antwort["ok"]), (200, True))
        self.assertTrue(self.marker.exists())

    def test_fehler_kommt_mit_ausgabe_zurueck(self):
        status, antwort = self.ausfuehren("plugin:x@y", "deaktivieren")
        self.assertEqual((status, antwort["ok"]), (200, False))
        self.assertIn("kaputt", antwort["ausgabe"])

    def test_befehl_laeuft_im_angegebenen_ordner(self):
        ordner = self.tmp / "projekt"
        ordner.mkdir()
        self.schreibe_inventar([], mcp=[{"id": "mcp:upnote", "name": "upnote", "aktionen": {
            "deinstallieren": {"argv": ["pwd"], "cwd": str(ordner)}}}])
        _, antwort = self.ausfuehren("mcp:upnote", "deinstallieren")
        self.assertEqual(Path(antwort["ausgabe"].strip()).resolve(), ordner.resolve())

    def test_projekt_skills_sind_ausfuehrbar(self):
        zweiter = self.tmp / "zweiter"
        inventar = {"mcp": [], "plugins": [], "skills": [],
                    "projekt_skills": [{"id": "skill:Kit/seo-audit", "name": "seo-audit", "aktionen": {"deinstallieren": {"argv": ["touch", str(self.marker)]}}}],
                    "projekt_mcp": [{"id": "mcp:Q/q", "name": "q", "aktionen": {"deinstallieren": {"argv": ["touch", str(zweiter)]}}}]}
        (self.tmp / "inventar.json").write_text(json.dumps(inventar))
        self.assertTrue(self.ausfuehren("skill:Kit/seo-audit", "deinstallieren")[1]["ok"])
        self.assertTrue(self.ausfuehren("mcp:Q/q", "deinstallieren")[1]["ok"])
        self.assertTrue(self.marker.exists() and zweiter.exists())

    def test_nur_aktionen_aus_dem_inventar(self):
        for eid, aktion in (("plugin:x@y", "nur manuell"), ("plugin:gibtsnicht", "deinstallieren")):
            with self.subTest(eid=eid, aktion=aktion):
                status, _ = self.ausfuehren(eid, aktion)
                self.assertEqual(status, 400)
        status, _ = self.anfrage("POST", "/api/ausfuehren", "kein json")
        self.assertEqual(status, 400)

    def test_override_landet_in_settings_und_original_in_sicherung(self):
        self.schreibe_inventar([], skills=[
            {"id": "skill:grilling", "name": "grilling", "aktionen": {"deaktivieren": {"override": "off"}}},
            {"id": "skill:tdd", "name": "tdd", "aktionen": {"nur manuell": {"override": "user-invocable-only"}}},
        ])
        self.assertTrue(self.ausfuehren("skill:grilling", "deaktivieren")[1]["ok"])
        self.assertTrue(self.ausfuehren("skill:tdd", "nur manuell")[1]["ok"])
        self.assertEqual(self.settings.read_text(), '{\n  "env": {\n    "A": "1"\n  },\n  "skillOverrides": {\n'
                         '    "alt": "off",\n    "grilling": "off",\n    "tdd": "user-invocable-only"\n  }\n}\n')
        self.assertEqual((self.tmp / "settings.json.bak").read_text(), SETTINGS_VORHER)

    def test_override_ohne_bisherige_overrides(self):
        self.settings.write_text("{}\n")
        self.schreibe_inventar([], skills=[
            {"id": "skill:grilling", "name": "grilling", "aktionen": {"deaktivieren": {"override": "off"}}}])
        self.ausfuehren("skill:grilling", "deaktivieren")
        self.assertEqual(json.loads(self.settings.read_text()), {"skillOverrides": {"grilling": "off"}})

    def test_mcp_sperre_landet_einmal_in_settings(self):
        self.settings.write_text('{\n  "deniedMcpServers": [\n    {\n      "serverName": "alt"\n    }\n  ]\n}\n')
        vorher = self.settings.read_text()
        self.schreibe_inventar([], mcp=[
            {"id": "mcp:claude.ai Gmail", "name": "claude.ai Gmail",
             "aktionen": {"deaktivieren": {"sperren": {"serverName": "claude.ai Gmail"}}}}])
        for _ in range(2):
            status, antwort = self.ausfuehren("mcp:claude.ai Gmail", "deaktivieren")
            self.assertEqual((status, antwort["ok"]), (200, True))
        self.assertEqual(json.loads(self.settings.read_text()),
                         {"deniedMcpServers": [{"serverName": "alt"}, {"serverName": "claude.ai Gmail"}]})
        self.assertEqual((self.tmp / "settings.json.bak").read_text(), vorher)

    def test_kaputte_settings_bleiben_unangetastet(self):
        self.settings.write_text("{ kaputt")
        self.schreibe_inventar([], skills=[
            {"id": "skill:grilling", "name": "grilling", "aktionen": {"deaktivieren": {"override": "off"}}}])
        status, antwort = self.ausfuehren("skill:grilling", "deaktivieren")
        self.assertEqual((status, antwort["ok"]), (200, False))
        self.assertEqual(self.settings.read_text(), "{ kaputt")

    def bewerten(self):
        """Startet die Bewertung und wartet, bis der Server sie abgeschlossen hat; liefert (Status beim Start, Stand am Ende)."""
        status, inhalt = self.anfrage("POST", "/api/bewerten")
        if status != 200:
            return status, inhalt
        self.assertEqual(json.loads(inhalt), {"ok": True, "laeuft": True})
        for _ in range(100):
            stand = json.loads(self.anfrage("POST", "/api/bewertung")[1])
            if not stand["laeuft"]:
                return status, stand
            time.sleep(0.1)
        self.fail("Bewertung wurde nicht fertig")

    def test_bewerten_startet_claude_eng_begrenzt(self):
        self.claude_ersatz('{"type":"result","is_error":false,"result":"2 neu, 1 weg."}')
        status, stand = self.bewerten()
        self.assertEqual((status, stand), (200, {"laeuft": False, "ok": True, "ausgabe": "2 neu, 1 weg."}))
        argv = (self.tmp / "argv").read_text().split("\n")
        self.assertEqual(argv[:3], ["-p", "/3zero-harness-inventur", "--model"])
        for erwartet in ("opus", "Read", "Edit", "Write", "Bash(python3 *)"):
            self.assertIn(erwartet, argv)
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertEqual(Path((self.tmp / "cwd").read_text().strip()).resolve(), self.tmp.resolve())

    def test_bewerten_ohne_token_startet_nichts(self):
        self.claude_ersatz('{"result":"egal"}')
        status, _ = self.anfrage("POST", "/api/bewerten", token=None)
        self.assertEqual(status, 403)
        self.assertEqual(self.anfrage("POST", "/api/bewertung", token=None)[0], 403)
        time.sleep(0.3)
        self.assertFalse((self.tmp / "argv").exists())

    def test_bewerten_meldet_fehler_im_klartext(self):
        self.claude_ersatz("Credit balance too low", code=2)
        status, stand = self.bewerten()
        self.assertEqual((status, stand["ok"]), (200, False))
        self.assertIn("Credit balance too low", stand["ausgabe"])

    def test_bewertung_bleibt_bis_zum_verwerfen(self):
        self.claude_ersatz('{"result":"fertig"}')
        self.bewerten()
        self.assertEqual(json.loads(self.anfrage("POST", "/api/bewertung")[1])["ausgabe"], "fertig")
        self.assertEqual(json.loads(self.anfrage("POST", "/api/bewertung", {"verwerfen": True})[1]), {"laeuft": False})
        self.assertEqual(json.loads(self.anfrage("POST", "/api/bewertung")[1]), {"laeuft": False})

    def test_laufende_bewertung_blockiert_weder_start_der_suche_noch_zweiten_start(self):
        (self.tmp / "build.py").write_text("")
        self.claude_ersatz('{"result":"fertig"}')
        skript = self.tmp / "bin" / "claude"
        skript.write_text(skript.read_text().replace("cat <<", "sleep 1\ncat <<"))
        self.assertEqual(self.anfrage("POST", "/api/bewerten")[0], 200)
        time.sleep(0.2)
        beginn = time.time()
        self.assertFalse(json.loads(self.anfrage("POST", "/api/bewerten")[1])["ok"])
        self.assertEqual(self.suchen("Folien", warten=False)[1], {"ok": True, "laeuft": True})
        self.assertLess(time.time() - beginn, 0.5)

    KANDIDATEN = [
        {"art": "skill", "name": "frontend-slides", "was": "HTML-Folien", "repo": "zarazhangrui/frontend-slides", "skill": "frontend-slides", "befehl": "npx skills add …"},
        {"art": "plugin", "name": "slides", "was": "Plugin", "plugin": "slides", "marktplatz": "claude-plugins-official", "befehl": "claude plugin install …"},
        {"art": "mcp", "name": "gamma", "was": "MCP", "befehl": "claude mcp add gamma …"},
        {"art": "skill", "name": "boese", "was": "untergeschoben", "repo": "x/y; rm -rf ~", "skill": "boese", "befehl": "…"},
    ]

    def suchen(self, bedarf="Präsentationen als HTML", warten=True):
        """Startet die Suche und wartet, bis der Server sie abgeschlossen hat; liefert (Status, Stand der Suche)."""
        (self.tmp / "build.py").write_text("")
        antwort = {"type": "result", "result": "…", "structured_output": {"bericht": "## Bedarf\nHTML-Folien", "kandidaten": self.KANDIDATEN}}
        if not (self.tmp / "bin" / "claude").exists():
            self.claude_ersatz("Warning: irgendwas\n" + json.dumps(antwort))
        status, inhalt = self.anfrage("POST", "/api/suchen", {"bedarf": bedarf})
        if status != 200 or not warten:
            return status, (json.loads(inhalt) if status == 200 else inhalt)
        for _ in range(100):
            stand = self.stand()
            if not stand["laeuft"]:
                return status, stand
            time.sleep(0.1)
        self.fail("Suche wurde nicht fertig")

    def stand(self, daten=None):
        return json.loads(self.anfrage("POST", "/api/suche", daten)[1])

    def installieren(self, daten, **kw):
        status, inhalt = self.anfrage("POST", "/api/installieren", daten, **kw)
        return status, (json.loads(inhalt) if status == 200 else inhalt)

    def test_suchen_startet_claude_nur_mit_lese_und_suchrechten(self):
        status, antwort = self.suchen()
        self.assertEqual((status, antwort["ok"], antwort["ausgabe"]), (200, True, "## Bedarf\nHTML-Folien"))
        argv = (self.tmp / "argv").read_text().split("\n")
        self.assertEqual(argv[:2], ["-p", "/3zero-harness-inventur suche Präsentationen als HTML"])
        for erwartet in ("--json-schema", "WebSearch", "WebFetch", "Bash(npx skills find *)"):
            self.assertIn(erwartet, argv)
        for verboten in ("Edit", "Write", "Bash(python3 *)", "Bash(gh api *)", "--dangerously-skip-permissions"):
            self.assertNotIn(verboten, argv)
        self.assertFalse(Path((self.tmp / "cwd").read_text().strip()).resolve().is_relative_to(Path.home()))

    def test_suchen_prueft_die_kandidaten(self):
        _, antwort = self.suchen()
        self.assertEqual([(k["name"], k["installierbar"]) for k in antwort["kandidaten"]],
                         [("frontend-slides", True), ("slides", True), ("gamma", False), ("boese", False)])

    def test_suche_laeuft_im_hintergrund_und_bleibt_abrufbar(self):
        (self.tmp / "build.py").write_text("")
        antwort = {"structured_output": {"bericht": "fertig", "kandidaten": []}}
        self.claude_ersatz(json.dumps(antwort))
        skript = self.tmp / "bin" / "claude"
        skript.write_text(skript.read_text().replace("cat <<", "sleep 1\ncat <<"))
        beginn = time.time()
        status, gestartet = self.suchen("Folien", warten=False)
        self.assertLess(time.time() - beginn, 0.9)
        self.assertEqual((status, gestartet), (200, {"ok": True, "laeuft": True}))
        stand = self.stand()
        self.assertEqual((stand["laeuft"], stand["bedarf"]), (True, "Folien"))
        self.assertFalse(self.suchen("zweite", warten=False)[1]["ok"])
        self.assertEqual(self.ausfuehren("plugin:x@y", "deinstallieren")[1]["ok"], True)  # Aktionen warten nicht auf Claude
        for _ in range(50):
            if not self.stand()["laeuft"]:
                break
            time.sleep(0.1)
        self.assertEqual({k: v for k, v in self.stand().items() if k != "kandidaten"},
                         {"laeuft": False, "bedarf": "Folien", "ok": True, "ausgabe": "fertig"})

    def test_verworfene_suche_ist_weg(self):
        self.suchen()
        self.assertEqual(self.stand({"verwerfen": True}), {"laeuft": False})
        self.assertEqual(self.stand(), {"laeuft": False})
        self.assertEqual(self.installieren({"nr": 0})[0], 400)

    def test_suchen_braucht_bedarf_und_token(self):
        for bedarf in ("", "   ", "x" * 301):
            with self.subTest(bedarf=bedarf[:10]):
                self.assertEqual(self.suchen(bedarf)[0], 400)
        self.assertEqual(self.anfrage("POST", "/api/suchen", "kein json")[0], 400)
        self.assertEqual(self.anfrage("POST", "/api/suchen", {"bedarf": "x"}, token=None)[0], 403)
        self.assertEqual(self.anfrage("POST", "/api/suche", token=None)[0], 403)

    def test_installieren_nur_geprueften_kandidaten_der_letzten_suche(self):
        self.assertEqual(self.installieren({"nr": 0})[0], 400)
        self.suchen()
        for daten in ({"nr": 2}, {"nr": 3}, {"nr": 9}, {"nr": "0"}, {"nr": True}, {}):
            with self.subTest(daten=daten):
                self.assertEqual(self.installieren(daten)[0], 400)
        self.assertEqual(self.installieren({"nr": 0}, token=None)[0], 403)

    def test_installieren_skill_mit_eigenem_befehl_und_nur_manuell(self):
        self.suchen()
        self.claude_ersatz("installiert", name="npx")
        status, antwort = self.installieren({"nr": 0, "nur_manuell": True})
        self.assertEqual((status, antwort["ok"]), (200, True))
        self.assertEqual((self.tmp / "argv").read_text().split("\n")[:-1],
                         ["-y", "skills", "add", "zarazhangrui/frontend-slides", "--skill", "frontend-slides", "-g", "-y", "--agent", "claude-code"])
        self.assertFalse(Path((self.tmp / "cwd").read_text().strip()).resolve().is_relative_to(Path.home()))
        self.assertEqual(json.loads(self.settings.read_text())["skillOverrides"]["frontend-slides"], "user-invocable-only")
        self.assertEqual([k.get("installiert", False) for k in self.stand()["kandidaten"]], [True, False, False, False])

    def test_installieren_plugin(self):
        self.suchen()
        self.claude_ersatz("installiert")
        status, antwort = self.installieren({"nr": 1})
        self.assertEqual((status, antwort["ok"]), (200, True))
        self.assertEqual((self.tmp / "argv").read_text().split("\n")[:-1], ["plugin", "install", "slides@claude-plugins-official"])
        self.assertEqual(self.settings.read_text(), SETTINGS_VORHER)

    def test_fehlgeschlagene_installation_setzt_keinen_override(self):
        self.suchen()
        self.claude_ersatz("Repository not found", code=1, name="npx")
        status, antwort = self.installieren({"nr": 0, "nur_manuell": True})
        self.assertEqual((status, antwort["ok"]), (200, False))
        self.assertIn("Repository not found", antwort["ausgabe"])
        self.assertEqual(self.settings.read_text(), SETTINGS_VORHER)

    def test_neu_einlesen_liest_frisches_inventar(self):
        neu = {"mcp": [], "plugins": [], "skills": [
            {"id": "skill:neu", "name": "neu", "aktionen": {"deinstallieren": {"argv": ["touch", str(self.marker)]}}}]}
        (self.tmp / "build.py").write_text(f"from pathlib import Path\nPath('inventar.json').write_text({json.dumps(json.dumps(neu))})\n")
        status, inhalt = self.anfrage("POST", "/api/neu")
        self.assertEqual((status, json.loads(inhalt)["ok"]), (200, True))
        self.assertTrue(self.ausfuehren("skill:neu", "deinstallieren")[1]["ok"])
        self.assertTrue(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
