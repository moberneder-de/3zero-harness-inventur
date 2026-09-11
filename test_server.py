import http.client
import json
import shutil
import tempfile
import threading
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
        shutil.rmtree(self.tmp)

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

    def test_kaputte_settings_bleiben_unangetastet(self):
        self.settings.write_text("{ kaputt")
        self.schreibe_inventar([], skills=[
            {"id": "skill:grilling", "name": "grilling", "aktionen": {"deaktivieren": {"override": "off"}}}])
        status, antwort = self.ausfuehren("skill:grilling", "deaktivieren")
        self.assertEqual((status, antwort["ok"]), (200, False))
        self.assertEqual(self.settings.read_text(), "{ kaputt")

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
