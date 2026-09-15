#!/usr/bin/env python3
"""Lokaler Server für die Harness-Inventur.

Liest den Bestand neu ein (build.py), zeigt das Dashboard auf http://127.0.0.1:8765
und führt die dort gewählten Aktionen aus. Ausgeführt wird nur, was build.py in
inventar.json als Aktion eines Eintrags hinterlegt hat; der Browser schickt nur
Eintrags-ID und Aktionsname, zusammen mit einem Token, das bei jedem Start neu entsteht.
Installieren geht nur für Kandidaten der letzten Suche: Der Browser schickt deren Nummer,
den Befehl baut der Server aus den geprüften Feldern repo, skill, plugin und marktplatz.

Aufruf: python3 server.py   (beenden mit Ctrl+C)
"""

import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HIER = Path(__file__).resolve().parent
PORT = 8765
TOKEN_PLATZHALTER = '<script id="token" type="application/json">null</script>'

# Bewertung nachziehen: Claude Code ohne Oberfläche, nur mit den Rechten, die der Skill braucht
BEWERTEN = [
    "claude", "-p", "/3zero-harness-inventur", "--model", "opus", "--output-format", "json",
    "--allowedTools", "Read", "Edit", "Write", "Glob", "Grep", "Bash(python3 *)",
    "--append-system-prompt", "Dieser Lauf kommt aus dem Dashboard-Server. Das Dashboard ist bereits "
    "geöffnet: baue es in Schritt 5 nur neu, öffne es nicht. Ändere nichts außer bewertung.json.",
]

# Suchen liest fremde Inhalte aus dem Netz, deshalb nur Lese- und Suchrechte: kein Schreiben, kein python3, kein gh.
# Installiert wird nicht von Claude, sondern über /api/installieren mit Befehlen, die der Server selbst baut.
SUCHEN_WERKZEUGE = [
    "Read", "Glob", "Grep", "WebSearch", "WebFetch",
    "Bash(npx skills find *)", "Bash(npx -y skills find *)", "Bash(npx skills add * -l)", "Bash(npx -y skills add * -l)",
    "Bash(claude plugin list *)", "Bash(jq *)",
]
SUCHEN_HINWEIS = (
    "Dieser Lauf kommt aus dem Dashboard-Server. Der Bestand ist frisch eingelesen, führe build.py nicht aus. "
    "Stelle keine Rückfragen: Ist der Bedarf vage, lege die naheliegende Deutung fest und nenne sie im Bericht; "
    "deckt der Bestand ihn ab, sage das. Installiere nichts und ändere keine Dateien, das übernimmt das Dashboard. "
    "GitHub-Daten (Sterne, letzter Push, Lizenz) holst du per WebFetch von https://api.github.com/repos/<owner>/<repo>, nicht mit gh. "
    "bericht ist dein Bericht aus S1 bis S5 auf Deutsch in Markdown mit Überschriften (##), Absätzen, Listen, **fett** und `code`, ohne Tabellen. "
    "kandidaten sind höchstens die drei aus S5: art skill mit repo (owner/repo) und skill (Name im Repo), "
    "art plugin mit plugin und marktplatz, art mcp nur mit befehl aus der Doku des Anbieters."
)
SUCHEN_SCHEMA = {
    "type": "object",
    "properties": {
        "bericht": {"type": "string"},
        "kandidaten": {"type": "array", "maxItems": 3, "items": {
            "type": "object",
            "properties": {
                "art": {"type": "string", "enum": ["skill", "plugin", "mcp"]},
                "name": {"type": "string"}, "was": {"type": "string"}, "befehl": {"type": "string"},
                "repo": {"type": "string"}, "skill": {"type": "string"},
                "plugin": {"type": "string"}, "marktplatz": {"type": "string"},
            },
            "required": ["art", "name", "was", "befehl"],
        }},
    },
    "required": ["bericht", "kandidaten"],
}
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")


def claude_json(ausgabe):
    """Das JSON-Objekt aus `claude --output-format json`, auch wenn Warnungen davor stehen; None, wenn keins darin ist."""
    try:
        return json.loads(ausgabe[ausgabe.find("{"):ausgabe.rfind("}") + 1])
    except ValueError:
        return None


def installierbar(k):
    """Nur Kandidaten, deren Angaben der Server selbst in einen Befehl einsetzen kann, ohne Shell und ohne Optionen."""
    passt = lambda muster, wert: isinstance(wert, str) and muster.fullmatch(wert) is not None
    if k.get("art") == "skill":
        return passt(REPO, k.get("repo")) and passt(NAME, k.get("skill"))
    if k.get("art") == "plugin":
        return passt(NAME, k.get("plugin")) and passt(NAME, k.get("marktplatz"))
    return False


def claude_text(ausgabe):
    """Aus `claude --output-format json` die Schlussmeldung holen, sonst die Rohausgabe."""
    for text in (ausgabe, ausgabe[ausgabe.find("{"):ausgabe.rfind("}") + 1]):
        try:
            return json.loads(text).get("result") or ausgabe
        except (ValueError, AttributeError):
            continue
    return ausgabe


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
        self.kandidaten = []  # aus der letzten Suche; nur sie lassen sich installieren
        self.suche = None  # Stand der letzten Suche; sie läuft im Hintergrund, der Browser fragt ihn ab

    def schritt(self, eid, aktion):
        """(Name, Schritt) aus inventar.json, oder None, wenn der Eintrag diese Aktion nicht anbietet."""
        inventar = json.loads((self.ordner / "inventar.json").read_text(encoding="utf-8"))
        for e in inventar["mcp"] + inventar["plugins"] + inventar["skills"] + inventar.get("projekt_skills", []) + inventar.get("projekt_mcp", []):
            if e["id"] == eid and aktion in e.get("aktionen", {}):
                return e["name"], e["aktionen"][aktion]
        return None

    def settings_aendern(self, aendern):
        text = self.settings.read_text(encoding="utf-8")
        daten = json.loads(text)
        if not self.gesichert:
            self.settings.with_name(self.settings.name + ".bak").write_text(text, encoding="utf-8")
            self.gesichert = True
        aendern(daten)
        neu = self.settings.with_name(self.settings.name + ".neu")
        neu.write_text(json.dumps(daten, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(neu, self.settings)

    def override_setzen(self, name, wert):
        def aendern(daten):
            daten.setdefault("skillOverrides", {})[name] = wert
        self.settings_aendern(aendern)
        return {"ok": True, "ausgabe": f'skillOverrides: "{name}": "{wert}"'}

    def mcp_sperren(self, eintrag):
        def aendern(daten):
            liste = daten.setdefault("deniedMcpServers", [])
            if eintrag not in liste:
                liste.append(eintrag)
        self.settings_aendern(aendern)
        return {"ok": True, "ausgabe": "deniedMcpServers: " + json.dumps(eintrag, ensure_ascii=False)}

    def ausfuehren(self, name, schritt):
        with self.sperre:
            if "override" in schritt:
                return self.override_setzen(name, schritt["override"])
            if "sperren" in schritt:
                return self.mcp_sperren(schritt["sperren"])
            return lauf(schritt["argv"], schritt.get("cwd"), 120)

    def neu_einlesen(self):
        with self.sperre:
            return lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)

    def bewerten(self):
        with self.sperre:
            r = lauf(BEWERTEN, self.ordner, 1200)
        return {"ok": r["ok"], "ausgabe": claude_text(r["ausgabe"])}

    def suchen(self, bedarf):
        argv = ["claude", "-p", f"/3zero-harness-inventur suche {bedarf}", "--model", "opus", "--output-format", "json",
                "--json-schema", json.dumps(SUCHEN_SCHEMA), "--allowedTools", *SUCHEN_WERKZEUGE, "--append-system-prompt", SUCHEN_HINWEIS]
        with self.sperre:
            r = lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)
        if not r["ok"]:
            return {"ok": False, "ausgabe": "Bestand einlesen fehlgeschlagen:\n\n" + r["ausgabe"]}
        # Claude ändert nichts, also ohne Sperre: Aktionen im Dashboard warten nicht minutenlang auf die Suche
        r = lauf(argv, tempfile.gettempdir(), 1200)  # die Suchbefehle laufen außerhalb von ~
        ergebnis = (claude_json(r["ausgabe"]) or {}).get("structured_output")
        if not r["ok"] or not isinstance(ergebnis, dict):
            return {"ok": False, "ausgabe": claude_text(r["ausgabe"])}
        self.kandidaten = [{**k, "installierbar": installierbar(k)} for k in ergebnis.get("kandidaten", []) if isinstance(k, dict)]
        return {"ok": True, "ausgabe": str(ergebnis.get("bericht", "")), "kandidaten": self.kandidaten}

    def suche_starten(self, bedarf):
        """Eine Suche dauert Minuten; eine so lange offene Anfrage reißt ab, wenn der Browser neu lädt oder aufgibt."""
        with self.sperre:
            if self.suche and self.suche["laeuft"]:
                return {"ok": False, "ausgabe": "Es läuft schon eine Suche."}
            self.suche, self.kandidaten = {"laeuft": True, "bedarf": bedarf, "beginn": time.time()}, []

        def im_hintergrund():
            try:
                ergebnis = self.suchen(bedarf)
            except Exception as fehler:  # sonst bliebe die Suche für immer „läuft“ und sperrte jede weitere
                ergebnis = {"ok": False, "ausgabe": f"{type(fehler).__name__}: {fehler}"}
            self.suche = {"laeuft": False, "bedarf": bedarf, **ergebnis}
        threading.Thread(target=im_hintergrund, daemon=True).start()
        return {"ok": True, "laeuft": True}

    def suche_stand(self, verwerfen=False):
        if verwerfen and self.suche and not self.suche["laeuft"]:
            self.suche, self.kandidaten = None, []
        if not self.suche:
            return {"laeuft": False}
        if self.suche["laeuft"]:
            return {"laeuft": True, "bedarf": self.suche["bedarf"], "sekunden": round(time.time() - self.suche["beginn"])}
        return self.suche

    def installieren(self, nr, nur_manuell):
        k = self.kandidaten[nr]
        if k["art"] == "skill":
            argv = ["npx", "-y", "skills", "add", k["repo"], "--skill", k["skill"], "-g", "-y", "--agent", "claude-code"]
        else:
            argv = ["claude", "plugin", "install", f"{k['plugin']}@{k['marktplatz']}"]
        with self.sperre:
            r = lauf(argv, tempfile.gettempdir(), 300)
        if r["ok"] and nur_manuell and k["art"] == "skill":
            with self.sperre:
                r["ausgabe"] += "\n" + self.override_setzen(k["skill"], "user-invocable-only")["ausgabe"]
        if r["ok"]:
            k["installiert"] = True
        return r


class Handler(BaseHTTPRequestHandler):
    def antworte(self, status, inhalt, typ="application/json"):
        daten = (inhalt if isinstance(inhalt, str) else json.dumps(inhalt, ensure_ascii=False)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", typ + "; charset=utf-8")
        self.send_header("Content-Length", str(len(daten)))
        try:
            self.end_headers()
            self.wfile.write(daten)
        except (BrokenPipeError, ConnectionResetError):
            pass  # der Browser hat die Seite neu geladen oder geschlossen

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
        if self.path == "/api/bewerten":
            return self.antworte(200, self.server.bewerten())
        if self.path not in ("/api/ausfuehren", "/api/suchen", "/api/suche", "/api/installieren"):
            return self.antworte(404, {"ok": False, "ausgabe": "nicht gefunden"})
        try:
            anfrage = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except ValueError:
            anfrage = None
        if self.path == "/api/suchen":
            bedarf = anfrage.get("bedarf") if isinstance(anfrage, dict) else None
            if not isinstance(bedarf, str) or not 0 < len(bedarf.strip()) <= 300:
                return self.antworte(400, {"ok": False, "ausgabe": "Bedarf fehlt oder ist länger als 300 Zeichen."})
            return self.antworte(200, self.server.suche_starten(bedarf.strip()))
        if self.path == "/api/suche":
            return self.antworte(200, self.server.suche_stand(isinstance(anfrage, dict) and anfrage.get("verwerfen") is True))
        if self.path == "/api/installieren":
            nr = anfrage.get("nr") if isinstance(anfrage, dict) else None
            if type(nr) is not int or not 0 <= nr < len(self.server.kandidaten) or not self.server.kandidaten[nr]["installierbar"]:
                return self.antworte(400, {"ok": False, "ausgabe": "Diesen Kandidaten kann das Dashboard nicht installieren."})
            return self.antworte(200, self.server.installieren(nr, anfrage.get("nur_manuell") is True))
        try:
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
