#!/usr/bin/env python3
"""Lokaler Server für das Tool-Inventar.

Liest den Bestand neu ein (build.py), zeigt das Dashboard auf http://127.0.0.1:8765
und führt die dort gewählten Aktionen aus. Ausgeführt wird nur, was build.py in
inventar.json als Aktion eines Eintrags hinterlegt hat; der Browser schickt nur
Eintrags-ID und Aktionsname, zusammen mit einem Token, das bei jedem Start neu entsteht.

Aufruf: python3 server.py   (beenden mit Ctrl+C)
"""

import json
import os
import secrets
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HIER = Path(__file__).resolve().parent
PORT = 8765
TOKEN_PLATZHALTER = '<script id="token" type="application/json">null</script>'


def lauf(argv, cwd, timeout):
    try:
        r = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as fehler:
        return {"ok": False, "ausgabe": str(fehler)}
    return {"ok": r.returncode == 0, "ausgabe": (r.stdout + r.stderr).strip()}


class Inventarserver(ThreadingHTTPServer):
    def __init__(self, port, ordner, settings, token):
        super().__init__(("127.0.0.1", port), Handler)
        self.ordner, self.settings, self.token = Path(ordner), Path(settings), token
        self.hosts = {f"{h}:{self.server_address[1]}" for h in ("127.0.0.1", "localhost")}
        self.sperre = threading.Lock()  # Aktionen schreiben teils dieselbe settings.json, also nacheinander
        self.gesichert = False

    def schritt(self, eid, aktion):
        """(Name, Schritt) aus inventar.json, oder None, wenn der Eintrag diese Aktion nicht anbietet."""
        inventar = json.loads((self.ordner / "inventar.json").read_text(encoding="utf-8"))
        for e in inventar["mcp"] + inventar["plugins"] + inventar["skills"]:
            if e["id"] == eid and aktion in e.get("aktionen", {}):
                return e["name"], e["aktionen"][aktion]
        return None

    def override_setzen(self, name, wert):
        text = self.settings.read_text(encoding="utf-8")
        daten = json.loads(text)
        if not self.gesichert:
            self.settings.with_name(self.settings.name + ".bak").write_text(text, encoding="utf-8")
            self.gesichert = True
        daten.setdefault("skillOverrides", {})[name] = wert
        neu = self.settings.with_name(self.settings.name + ".neu")
        neu.write_text(json.dumps(daten, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(neu, self.settings)
        return {"ok": True, "ausgabe": f'skillOverrides: "{name}": "{wert}"'}

    def ausfuehren(self, name, schritt):
        with self.sperre:
            if "override" in schritt:
                return self.override_setzen(name, schritt["override"])
            return lauf(schritt["argv"], schritt.get("cwd"), 120)

    def neu_einlesen(self):
        with self.sperre:
            return lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)


class Handler(BaseHTTPRequestHandler):
    def antworte(self, status, inhalt, typ="application/json"):
        daten = (inhalt if isinstance(inhalt, str) else json.dumps(inhalt, ensure_ascii=False)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", typ + "; charset=utf-8")
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)

    def do_GET(self):
        # Host-Prüfung gegen DNS-Rebinding: sonst könnte eine fremde Seite das Token mitlesen
        if self.headers.get("Host") not in self.server.hosts:
            return self.antworte(403, "fremder Host", "text/plain")
        if self.path != "/":
            return self.antworte(404, "nicht gefunden", "text/plain")
        html = (self.server.ordner / "dashboard.html").read_text(encoding="utf-8")
        html = html.replace(TOKEN_PLATZHALTER, TOKEN_PLATZHALTER.replace("null", json.dumps(self.server.token)), 1)
        self.antworte(200, html, "text/html")

    def do_POST(self):
        token = self.headers.get("X-Token", "").encode()
        if self.headers.get("Host") not in self.server.hosts or not secrets.compare_digest(token, self.server.token.encode()):
            return self.antworte(403, {"ok": False, "ausgabe": "nicht erlaubt"})
        if self.path == "/api/neu":
            return self.antworte(200, self.server.neu_einlesen())
        if self.path != "/api/ausfuehren":
            return self.antworte(404, {"ok": False, "ausgabe": "nicht gefunden"})
        try:
            anfrage = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            treffer = self.server.schritt(anfrage["id"], anfrage["aktion"])
        except (ValueError, KeyError, TypeError):
            treffer = None
        if not treffer:
            return self.antworte(400, {"ok": False, "ausgabe": "Diese Aktion bietet der Eintrag nicht an."})
        try:
            ergebnis = self.server.ausfuehren(*treffer)
        except (OSError, ValueError) as fehler:
            ergebnis = {"ok": False, "ausgabe": f"{type(fehler).__name__}: {fehler}"}
        self.antworte(200, ergebnis)

    def log_message(self, *args):
        pass


def main():
    try:
        srv = Inventarserver(PORT, HIER, Path.home() / ".claude" / "settings.json", secrets.token_hex(16))
    except OSError:
        sys.exit(f"Port {PORT} ist belegt. Läuft der Server schon? Dann http://127.0.0.1:{PORT}/ öffnen.")
    print("Lese den Bestand ein …")
    ergebnis = srv.neu_einlesen()
    print(ergebnis["ausgabe"])
    if not ergebnis["ok"]:
        sys.exit(1)
    url = f"http://127.0.0.1:{PORT}/"
    print(f"Dashboard: {url}  (beenden mit Ctrl+C)")
    webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
