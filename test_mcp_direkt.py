import json
import shutil
import tempfile
import threading
import time
import unittest
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mcp_direkt
import werkzeuge

WERKZEUGE = [
    {"name": "suche", "description": "Sucht.", "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
    {"name": "lies", "description": "Liest.", "inputSchema": {"type": "object"}},
]


class FakeServer(BaseHTTPRequestHandler):
    """Ein MCP-Server über HTTP, so knapp wie möglich. Verhalten per Klassenattribut am Testfall."""
    fall = None

    def log_message(self, *a):
        pass

    def antworten(self, status, nachricht=None, kopf=None):
        f = self.fall
        self.send_response(status)
        for k, v in (kopf or {}).items():
            self.send_header(k, v)
        if nachricht is None:
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if f.sse:
            roh = f"event: message\ndata: {json.dumps(nachricht)}\n\n".encode()
            self.send_header("Content-Type", "text/event-stream")
        else:
            roh = json.dumps(nachricht).encode()
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(roh)))
        self.end_headers()
        self.wfile.write(roh)

    def do_DELETE(self):
        self.fall.anfragen.append(("DELETE", None, dict(self.headers)))
        self.antworten(200)

    def do_POST(self):
        f = self.fall
        n = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        f.anfragen.append(("POST", n, dict(self.headers)))
        if f.status:
            return self.antworten(f.status, None, f.status_kopf)
        if f.warten:
            time.sleep(f.warten)
        if n["method"] == "initialize":
            ergebnis = {"protocolVersion": "2025-06-18", "capabilities": f.faehigkeiten, "serverInfo": {"name": "fake"}}
            if f.anweisungen is not None:
                ergebnis["instructions"] = f.anweisungen
            return self.antworten(200, {"jsonrpc": "2.0", "id": n["id"], "result": ergebnis}, {"Mcp-Session-Id": "s-1"})
        if self.headers.get("Mcp-Session-Id") != "s-1":
            return self.antworten(400, {"jsonrpc": "2.0", "id": n.get("id"), "error": {"code": -32000, "message": "Sitzung fehlt"}})
        if n["method"] == "notifications/initialized":
            return self.antworten(202)
        if n["method"] == "tools/list":
            seiten = f.seiten
            nummer = int((n.get("params") or {}).get("cursor") or 0)
            ergebnis = {"tools": seiten[nummer]}
            if nummer + 1 < len(seiten):
                ergebnis["nextCursor"] = str(nummer + 1)
            return self.antworten(200, {"jsonrpc": "2.0", "id": n["id"], "result": ergebnis})
        self.antworten(400)


class Direkt(unittest.TestCase):
    sse = False
    status = None
    status_kopf = None
    warten = 0
    anweisungen = "x" * 400
    faehigkeiten = {"tools": {}}
    seiten = [WERKZEUGE]

    def setUp(self):
        self.anfragen = []
        handler = type("H", (FakeServer,), {"fall": self})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"

    def methoden(self):
        return [n["method"] if n else art for art, n, _ in self.anfragen]

    def test_anweisungen_werkzeuge_und_schemata(self):
        schemata = sum(len(json.dumps(w, ensure_ascii=False)) for w in WERKZEUGE)
        self.assertEqual(mcp_direkt.fragen(self.url), {
            "werkzeuge": 2, "anweisungen": 100, "namen_zeichen": len("suche") + 1 + len("lies") + 1,
            "schemata": round(schemata / 4)})

    def test_sse_und_sitzung(self):
        """Die Antwort kommt als Event-Stream; ab initialize trägt jede Anfrage die Sitzung, am Ende wird sie beendet."""
        self.sse = True
        self.assertEqual(mcp_direkt.fragen(self.url)["werkzeuge"], 2)
        self.assertEqual(self.methoden(), ["initialize", "notifications/initialized", "tools/list", "DELETE"])
        self.assertTrue(all(k.get("Mcp-Session-Id") == "s-1" for _, _, k in self.anfragen[1:]))

    def test_mehrere_seiten(self):
        self.seiten = [WERKZEUGE[:1], WERKZEUGE[1:]]
        self.assertEqual(mcp_direkt.fragen(self.url)["werkzeuge"], 2)

    def test_ohne_werkzeuge_und_ohne_anweisungen(self):
        """Ohne `tools` in den Fähigkeiten fragt die Inventur nicht nach Werkzeugen: Das wäre ein Fehler, keine Null."""
        self.faehigkeiten, self.anweisungen = {}, None
        self.assertEqual(mcp_direkt.fragen(self.url), {"werkzeuge": 0, "anweisungen": 0, "namen_zeichen": 0, "schemata": 0})
        self.assertNotIn("tools/list", self.methoden())

    def test_anmeldung_noetig(self):
        self.status, self.status_kopf = 401, {"WWW-Authenticate": 'Bearer resource_metadata="x"'}
        self.assertIsNone(mcp_direkt.fragen(self.url))

    def test_umleitung_wird_nicht_verfolgt(self):
        """Eine Umleitung könnte die Anfrage an ein anderes Ziel schicken als das, das in der Konfiguration steht."""
        self.status, self.status_kopf = 307, {"Location": "http://127.0.0.1:1/woanders"}
        self.assertIsNone(mcp_direkt.fragen(self.url))
        self.assertEqual(len(self.anfragen), 1)

    def test_zeitgrenze(self):
        self.warten = 0.5
        self.assertIsNone(mcp_direkt.fragen(self.url, zeitgrenze=0.1))

    def test_kein_server(self):
        self.assertIsNone(mcp_direkt.fragen("http://127.0.0.1:1/mcp"))

    def test_kaputte_antwort(self):
        self.seiten = [[{"name": 3}], ["x"]]
        self.assertIsNone(mcp_direkt.fragen(self.url))


class Adresse(unittest.TestCase):
    def test_nur_http_ohne_zugangsdaten(self):
        a = mcp_direkt.adresse
        self.assertEqual(a({"type": "http", "url": "https://mcp.example.org/mcp"}), "https://mcp.example.org/mcp")
        self.assertEqual(a({"url": "http://127.0.0.1:9/mcp"}), "http://127.0.0.1:9/mcp")  # Codex kennt kein `type`
        for c in ({"command": "npx"}, {"type": "sse", "url": "https://x/sse"}, {"type": "http", "url": "file:///etc/passwd"},
                  {"type": "http", "url": "https://x/${TOKEN}/mcp"}, {"type": "http", "url": "https://x", "headers": {"A": "b"}},
                  {"type": "http", "url": "https://x", "headersHelper": "h"}, {"type": "http", "url": "https://x", "oauth": {}},
                  {"url": "https://x", "bearer_token_env_var": "T"}, {"url": "https://x", "http_headers": {}},
                  {"url": "https://x", "env_http_headers": {}}, {"url": "https://x", "bearer_token": "T"}, {"type": "http", "url": 3}, {}):
            self.assertIsNone(a(c), c)


class Gemessen(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_einmal_am_tag_und_ohne_url_im_speicher(self):
        """Die URL kann ein Geheimnis in der Query tragen; im Zwischenspeicher steht deshalb nur ein Hash."""
        url = "https://mcp.example.org/mcp?key=geheim"
        aufrufe = []
        antwort = {"werkzeuge": 3, "anweisungen": 5, "namen_zeichen": 12, "schemata": 90}
        mcp_direkt.fragen, alt = (lambda u: aufrufe.append(u) or antwort), mcp_direkt.fragen
        self.addCleanup(setattr, mcp_direkt, "fragen", alt)
        heute = date(2026, 9, 28)
        speicher = werkzeuge.Zwischenspeicher(self.tmp, heute)
        erwartet = {**antwort, "am": "2026-09-28"}
        self.assertEqual(mcp_direkt.gemessen({"type": "http", "url": url}, speicher), erwartet)
        self.assertEqual(mcp_direkt.gemessen({"type": "http", "url": url}, speicher), erwartet)
        self.assertIsNone(mcp_direkt.gemessen({"command": "npx"}, speicher))
        speicher.sichern()
        self.assertEqual(aufrufe, [url])
        self.assertNotIn("geheim", (self.tmp / "werkzeuge.json").read_text())
        mcp_direkt.fragen = lambda u: None  # am nächsten Tag ohne Netz gilt der letzte Stand
        self.assertEqual(mcp_direkt.gemessen({"type": "http", "url": url}, werkzeuge.Zwischenspeicher(self.tmp, date(2026, 9, 29))), erwartet)

    def test_felder_fuers_inventar(self):
        """Kontext je Sitzung: Anweisungen plus die Namen, wie sie in der Liste der Werkzeuge stehen (`mcp__<Server>__<Name>`).
        Die Schemata lädt der Harness erst bei Bedarf; sie stehen daneben und zählen nicht."""
        d = {"werkzeuge": 2, "anweisungen": 100, "namen_zeichen": 11, "schemata": 90, "am": "2026-09-28"}
        self.assertEqual(mcp_direkt.felder(d, "srv"), {
            "werkzeuge": 2, "tokens": 100 + round((11 + 2 * len("mcp__srv__")) / 4), "schemata": 90,
            "messung": "direkt", "gemessen_am": "2026-09-28"})


if __name__ == "__main__":
    unittest.main()
