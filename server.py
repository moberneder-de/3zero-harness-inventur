#!/usr/bin/env python3
"""Lokaler Server für die Harness-Inventur.

Liest den Bestand neu ein (build.py), zeigt das Dashboard auf http://127.0.0.1:8765
und führt die dort gewählten Aktionen aus. Ausgeführt wird nur, was build.py in
inventar.json als Aktion eines Eintrags hinterlegt hat; der Browser schickt nur
Eintrags-ID und Aktionsname, zusammen mit einem Token, das bei jedem Start neu entsteht.
Installieren geht nur für Kandidaten der letzten Suche: Der Browser schickt deren Nummer,
den Befehl baut der Server aus den geprüften Feldern repo, skill, plugin und marktplatz.

Aufruf: python3 server.py   (beenden mit Ctrl+C)

Welcher Harness gilt (Claude Code oder Codex), steht in harness.json: Der Skill setzt ihn beim Start, der
letzte Start zählt. Danach richten sich Dashboard, Inventar und die Läufe für Bewertung und Suche.
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
import tomllib
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import harness

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

# Codex-Läufe ohne eigene Plugins, MCP-Server, Regeln, Apps, Browser- und Computer-Steuerung aus ~/.codex.
# Sitzungsprotokolle schreibt der Lauf nicht (--ephemeral), damit er die Nutzungszahlen nicht verfälscht.
CODEX_BASIS = ["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral", "--skip-git-repo-check",
               *[x for f in ("apps", "plugins", "remote_plugin", "computer_use", "browser_use", "hooks") for x in ("--disable", f)]]
CODEX_BEWERTEN_HINWEIS = (
    "Dieser Lauf kommt aus dem Dashboard-Server und läuft in Codex (Harness codex). Der Server hat build.py gerade ausgeführt, "
    "die Ausgabe steht unten, und führt es nach deinem Lauf erneut aus: Überspringe die Schritte 1 und 5, führe build.py nicht aus "
    "und öffne nichts. Du arbeitest in einer Arbeitskopie ({arbeitsordner}), nicht im Projektordner ({ordner}): Ändere nur "
    "{arbeitsordner}/bewertung-codex.json, lies alles andere (inventar-codex.json, Skill-Dateien) aus {ordner}. Stelle keine Rückfragen. "
    "Deine Schlussnachricht ist der Bericht für das Dashboard: höchstens zehn Zeilen Markdown mit den drei Punkten aus Schritt 6 "
    "(was neu ist, was weggefallen ist, welche Empfehlungen sich geändert haben), keine Tabellen, keine Aufzählung aller Einträge."
)
# Codex kennt keine Befehlsliste wie --allowedTools. Die Grenze zieht die Sandbox: nur lesen, schreiben nur in den
# npm-Zwischenspeicher, Zugangsdaten gesperrt, Netz nur über den Codex-Proxy zu diesen Domains und nur lesend (limited).
SUCHEN_DOMAINS = ("skills.sh", "registry.npmjs.org", "github.com", "api.github.com", "raw.githubusercontent.com", "codeload.github.com")
GESPERRT_LESEN = (".ssh", ".gnupg", ".aws", ".netrc", ".config/gh", ".codex/auth.json", ".claude/.credentials.json",
                   ".npmrc", ".pypirc", ".claude.json", ".codex/config.toml", ".docker/config.json", ".kube/config", ".git-credentials",
                   ".codex/sessions", ".codex/archived_sessions", ".codex/history.jsonl", ".claude/projects")
CODEX_SUCHEN_HINWEIS = (
    "Dieser Lauf kommt aus dem Dashboard-Server und läuft in Codex (Harness codex). Der Bestand ist frisch eingelesen "
    "(inventar-codex.json), führe build.py nicht aus. Stelle keine Rückfragen: Ist der Bedarf vage, lege die naheliegende Deutung fest "
    "und nenne sie im Bericht; deckt der Bestand ihn ab, sage das. Installiere nichts und ändere keine Dateien. "
    "Shell-Befehle erreichen nur skills.sh, npm und GitHub, und nur lesend. Scheitert npx skills am Netz, suche Skills über die "
    "Websuche (skills.sh, GitHub) und schreibe in den Bericht, dass npx skills nicht erreichbar war. "
    "GitHub-Daten (Sterne, letzter Push) holst du mit curl -s https://api.github.com/repos/<owner>/<repo>. "
    "bericht ist dein Bericht aus S1 bis S5 auf Deutsch in Markdown mit Überschriften (##), Absätzen, Listen, **fett** und `code`, ohne Tabellen. "
    "kandidaten sind höchstens die drei aus S5: art skill mit repo (owner/repo) und skill (Name im Repo), "
    "art plugin mit plugin und marktplatz, art mcp nur mit befehl aus der Doku des Anbieters; nicht zutreffende Felder sind null."
)
# Codex prüft Schemas strikt: jedes Feld required, additionalProperties false, Optionales als null
SUCHEN_SCHEMA_CODEX = {
    "type": "object",
    "additionalProperties": False,
    "required": ["bericht", "kandidaten"],
    "properties": {
        "bericht": {"type": "string"},
        "kandidaten": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["art", "name", "was", "befehl", "repo", "skill", "plugin", "marktplatz"],
            "properties": {
                "art": {"type": "string", "enum": ["skill", "plugin", "mcp"]},
                "name": {"type": "string"}, "was": {"type": "string"}, "befehl": {"type": "string"},
                **{f: {"type": ["string", "null"]} for f in ("repo", "skill", "plugin", "marktplatz")},
            },
        }},
    },
}


def codex_modell(config):
    """Modell und Denktiefe aus ~/.codex/config.toml: --ignore-user-config ließe sie sonst weg."""
    try:
        cfg = tomllib.loads(Path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    argv = ["-m", cfg["model"]] if isinstance(cfg.get("model"), str) else []
    if isinstance(cfg.get("model_reasoning_effort"), str):
        argv += ["-c", f"model_reasoning_effort={json.dumps(cfg['model_reasoning_effort'])}"]
    return argv


def codex_bewerten(ordner, arbeitsordner, ergebnis, build_ausgabe, modell):
    hinweis = CODEX_BEWERTEN_HINWEIS.format(ordner=ordner, arbeitsordner=arbeitsordner)
    # workspace-write macht sonst auch $TMPDIR und /tmp beschreibbar; ein injizierter Lauf könnte dort etwas
    # ablegen, das installieren() oder die Claude-Suche später ohne Sandbox ausführen.
    return [*CODEX_BASIS, "-s", "workspace-write", "-C", str(arbeitsordner), "-c", 'web_search="disabled"',
            "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true", "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
            *modell, "-o", str(ergebnis),
            f"$3zero-harness-inventur\n\n{hinweis}\n\nAusgabe von build.py:\n{build_ausgabe}"]


def codex_suchen(bedarf, arbeitsordner, schema, ergebnis, modell):
    toml = lambda d: "{" + ",".join(f"{json.dumps(k, ensure_ascii=False)}={json.dumps(v, ensure_ascii=False)}" for k, v in d.items()) + "}"
    cache = Path(arbeitsordner) / "npm-cache"
    dateisystem = {str(cache): "write", **{str(Path.home() / p): "deny" for p in GESPERRT_LESEN if (Path.home() / p).exists()}}
    return [*CODEX_BASIS, "-C", str(arbeitsordner), *modell,
            "-c", 'web_search="live"',
            "-c", "features.network_proxy=true",
            "-c", 'permissions.suche.extends=":read-only"',
            "-c", f"permissions.suche.filesystem={toml(dateisystem)}",
            "-c", "permissions.suche.network.enabled=true",
            "-c", 'permissions.suche.network.mode="limited"',
            "-c", f"permissions.suche.network.domains={toml({d: 'allow' for d in SUCHEN_DOMAINS})}",
            "-c", 'default_permissions="suche"',
            "-c", f"shell_environment_policy.set.npm_config_cache={json.dumps(str(cache))}",
            # ~/.npmrc steht in GESPERRT_LESEN; ohne eigenen Pfad würde npm dort scheitern statt die Datei nur zu vermissen.
            "-c", f"shell_environment_policy.set.npm_config_userconfig={json.dumps(str(Path(arbeitsordner) / 'npmrc'))}",
            "--output-schema", str(schema), "-o", str(ergebnis),
            f"$3zero-harness-inventur suche {bedarf}\n\n{CODEX_SUCHEN_HINWEIS}"]


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
        # Stand der Claude-Läufe „suche“ und „bewertung“: Sie laufen im Hintergrund, der Browser fragt ihn ab.
        # Eigene Sperre, weil self.sperre während einer Bewertung minutenlang belegt ist.
        self.laeufe, self.laeufe_sperre = {}, threading.Lock()
        self.codex_config = Path.home() / ".codex" / "config.toml"

    def harness(self):
        return harness.aktuell(self.ordner)

    def datei(self, art):
        """inventar oder dashboard des Harness, in dem der Skill zuletzt gestartet wurde."""
        return self.ordner / harness.HARNESSE[self.harness()][art]

    def schritt(self, eid, aktion):
        """(Name, Schritt) aus dem Inventar des aktuellen Harness, oder None, wenn der Eintrag diese Aktion nicht anbietet."""
        inventar = json.loads(self.datei("inventar").read_text(encoding="utf-8"))
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
        if self.harness() == "codex":
            return self.bewerten_codex()
        with self.sperre:
            r = lauf(BEWERTEN, self.ordner, 1200)
        return {"ok": r["ok"], "ausgabe": claude_text(r["ausgabe"])}

    def bewerten_codex(self):
        """build.py liest mit Netz ein (claude mcp list, gh); Codex läuft ohne Netz, deshalb baut der Server davor und danach.
        Codex bekommt keinen Schreibzugriff auf den Projektordner (die Bewertung liest Skill-Texte, die Prompt-Injection enthalten
        könnten), sondern arbeitet auf einer Arbeitskopie von bewertung-codex.json; nur ein gültiges JSON-Objekt geht zurück."""
        with self.sperre:
            r = lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)
            if not r["ok"]:
                return {"ok": False, "ausgabe": "Bestand einlesen fehlgeschlagen:\n\n" + r["ausgabe"]}
            bewertung_datei = self.ordner / harness.HARNESSE["codex"]["bewertung"]
            with tempfile.TemporaryDirectory() as tmp:
                arbeitsordner = Path(tmp)
                (arbeitsordner / "bewertung-codex.json").write_text(
                    bewertung_datei.read_text(encoding="utf-8") if bewertung_datei.is_file() else "{}", encoding="utf-8")
                ergebnis = arbeitsordner / "ergebnis.md"
                c = lauf(codex_bewerten(self.ordner, arbeitsordner, ergebnis, r["ausgabe"], codex_modell(self.codex_config)),
                         arbeitsordner, 1200)
                text = ergebnis.read_text(encoding="utf-8").strip() if c["ok"] and ergebnis.is_file() else c["ausgabe"]
                if not c["ok"]:
                    return {"ok": False, "ausgabe": text}
                kopie = arbeitsordner / "bewertung-codex.json"
                bewertung = None
                if kopie.is_file() and not kopie.is_symlink():  # ein Symlink könnte auf eine Datei außerhalb zeigen
                    try:
                        bewertung = json.loads(kopie.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        bewertung = None
                if not isinstance(bewertung, dict):
                    return {"ok": False, "ausgabe": "Codex hat keine gültige bewertung-codex.json hinterlassen: " + text}
                neu = bewertung_datei.with_name(bewertung_datei.name + ".neu")
                neu.write_text(json.dumps(bewertung, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                os.replace(neu, bewertung_datei)
            n = lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)
        return {"ok": n["ok"], "ausgabe": text if n["ok"] else text + "\n\nNeu bauen fehlgeschlagen:\n\n" + n["ausgabe"]}

    def suchen(self, bedarf):
        self.kandidaten = []
        argv = ["claude", "-p", f"/3zero-harness-inventur suche {bedarf}", "--model", "opus", "--output-format", "json",
                "--json-schema", json.dumps(SUCHEN_SCHEMA), "--allowedTools", *SUCHEN_WERKZEUGE, "--append-system-prompt", SUCHEN_HINWEIS]
        with self.sperre:
            r = lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)
        if not r["ok"]:
            return {"ok": False, "ausgabe": "Bestand einlesen fehlgeschlagen:\n\n" + r["ausgabe"]}
        if self.harness() == "codex":
            return self.suchen_codex(bedarf)
        # Claude ändert nichts, also ohne Sperre: Aktionen im Dashboard warten nicht minutenlang auf die Suche.
        # Ein frischer Ordner statt direkt $TMPDIR: eine Codex-Bewertung darf $TMPDIR beschreiben, ein injizierter
        # Lauf könnte dort also etwas ablegen, das dieser ungesicherte Claude-Lauf sonst mit ausführen würde.
        with tempfile.TemporaryDirectory() as tmp:
            r = lauf(argv, tmp, 1200)
        ergebnis = (claude_json(r["ausgabe"]) or {}).get("structured_output")
        if not r["ok"] or not isinstance(ergebnis, dict):
            return {"ok": False, "ausgabe": claude_text(r["ausgabe"])}
        self.kandidaten = [{**k, "installierbar": installierbar(k)} for k in ergebnis.get("kandidaten", []) if isinstance(k, dict)]
        return {"ok": True, "ausgabe": str(ergebnis.get("bericht", "")), "kandidaten": self.kandidaten}

    def suchen_codex(self, bedarf):
        with tempfile.TemporaryDirectory() as tmp:  # außerhalb von ~, wie bei Claude
            schema, ergebnis = Path(tmp) / "schema.json", Path(tmp) / "ergebnis.json"
            schema.write_text(json.dumps(SUCHEN_SCHEMA_CODEX), encoding="utf-8")
            (Path(tmp) / "npm-cache").mkdir()
            r = lauf(codex_suchen(bedarf, tmp, schema, ergebnis, codex_modell(self.codex_config)), tmp, 1200)
            try:
                daten = json.loads(ergebnis.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                daten = None
        if not r["ok"] or not isinstance(daten, dict):
            return {"ok": False, "ausgabe": r["ausgabe"]}
        kandidaten = [{f: w for f, w in k.items() if w is not None} for k in daten.get("kandidaten", []) if isinstance(k, dict)][:3]
        # Installieren unter Codex kommt erst mit Teil B; bis dahin nur zum Kopieren
        self.kandidaten = [{**k, "installierbar": False} for k in kandidaten]
        return {"ok": True, "ausgabe": str(daten.get("bericht", "")), "kandidaten": self.kandidaten}

    def im_hintergrund(self, art, arbeit, **angaben):
        """Startet einen minutenlangen Claude-Lauf. Eine so lange offene Anfrage riss ab, wenn der Browser neu lud,
        und das Ergebnis war verloren; jetzt bleibt es in self.laeufe, bis der Browser es verwirft."""
        with self.laeufe_sperre:
            if (self.laeufe.get(art) or {}).get("laeuft"):
                return {"ok": False, "ausgabe": "Das läuft schon."}
            self.laeufe[art] = {"laeuft": True, "beginn": time.time(), **angaben}

        def los():
            try:
                ergebnis = arbeit()
            except Exception as fehler:  # sonst bliebe der Lauf für immer „läuft“ und sperrte jeden weiteren
                ergebnis = {"ok": False, "ausgabe": f"{type(fehler).__name__}: {fehler}"}
            self.laeufe[art] = {"laeuft": False, **angaben, **ergebnis}
        threading.Thread(target=los, daemon=True).start()
        return {"ok": True, "laeuft": True}

    def stand(self, art, verwerfen=False):
        with self.laeufe_sperre:
            lauf_ = self.laeufe.get(art)
            if verwerfen and lauf_ and not lauf_["laeuft"]:
                lauf_ = self.laeufe[art] = None
                if art == "suche":
                    self.kandidaten = []
        if not lauf_:
            return {"laeuft": False}
        if lauf_["laeuft"]:
            return {**{k: v for k, v in lauf_.items() if k != "beginn"}, "sekunden": round(time.time() - lauf_["beginn"])}
        return lauf_

    def installieren(self, nr, nur_manuell):
        k = self.kandidaten[nr]
        if k["art"] == "skill":
            argv = ["npx", "-y", "skills", "add", k["repo"], "--skill", k["skill"], "-g", "-y", "--agent", "claude-code"]
        else:
            argv = ["claude", "plugin", "install", f"{k['plugin']}@{k['marktplatz']}"]
        with self.sperre:
            with tempfile.TemporaryDirectory() as tmp:  # frischer Ordner statt $TMPDIR direkt, siehe suchen()
                r = lauf(argv, tmp, 300)
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
        html = self.server.datei("dashboard").read_text(encoding="utf-8")
        html = html.replace(TOKEN_PLATZHALTER, TOKEN_PLATZHALTER.replace("null", json.dumps(self.server.token)), 1)
        self.antworte(200, html, "text/html")

    def do_POST(self):
        token = self.headers.get("X-Token", "").encode()
        if self.headers.get("Host") not in self.server.hosts or not secrets.compare_digest(token, self.server.token.encode()):
            return self.antworte(403, {"ok": False, "ausgabe": "nicht erlaubt"})
        if self.path == "/api/neu":
            return self.antworte(200, self.server.neu_einlesen())
        if self.path not in ("/api/ausfuehren", "/api/bewerten", "/api/bewertung", "/api/suchen", "/api/suche", "/api/installieren"):
            return self.antworte(404, {"ok": False, "ausgabe": "nicht gefunden"})
        try:
            anfrage = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except ValueError:
            anfrage = None
        if self.path == "/api/bewerten":
            return self.antworte(200, self.server.im_hintergrund("bewertung", self.server.bewerten))
        if self.path == "/api/suchen":
            bedarf = anfrage.get("bedarf") if isinstance(anfrage, dict) else None
            if not isinstance(bedarf, str) or not 0 < len(bedarf.strip()) <= 300:
                return self.antworte(400, {"ok": False, "ausgabe": "Bedarf fehlt oder ist länger als 300 Zeichen."})
            bedarf = bedarf.strip()
            return self.antworte(200, self.server.im_hintergrund("suche", lambda: self.server.suchen(bedarf), bedarf=bedarf))
        if self.path in ("/api/suche", "/api/bewertung"):
            art = self.path.removeprefix("/api/")
            return self.antworte(200, self.server.stand(art, isinstance(anfrage, dict) and anfrage.get("verwerfen") is True))
        if self.path == "/api/installieren":
            nr = anfrage.get("nr") if isinstance(anfrage, dict) else None
            # Installieren gibt es erst mit Teil B; unter Codex kämen sonst noch Kandidaten aus einer Claude-Suche vor dem Wechsel durch.
            if (self.server.harness() == "codex" or type(nr) is not int or not 0 <= nr < len(self.server.kandidaten)
                    or not self.server.kandidaten[nr]["installierbar"]):
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
