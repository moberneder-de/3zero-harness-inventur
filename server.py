#!/usr/bin/env python3
"""Lokaler Server für die Harness-Inventur.

Liest den Bestand neu ein (build.py), zeigt das Dashboard auf http://127.0.0.1:8765
und führt die dort gewählten Aktionen aus. Ausgeführt wird nur, was build.py in
inventar.json als Aktion eines Eintrags hinterlegt hat; der Browser schickt nur
Eintrags-ID und Aktionsname, zusammen mit einem Token, das bei jedem Start neu entsteht.
Die Seite selbst gibt es nur mit diesem Token als Schlüssel in der Adresse (?k=…), weil jeder Benutzer des Rechners
127.0.0.1 erreicht; der Browser bekommt dafür eine Startdatei, die nur der Benutzer lesen kann.
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
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import harness

HIER = Path(__file__).resolve().parent
PORT = 8765
TOKEN_PLATZHALTER = '<script id="token" type="application/json">null</script>'

# Bewertung nachziehen: Claude Code ohne Oberfläche. Der Lauf liest fremde Skill-Texte und Hook-Skripte, die
# eine Anweisung an ihn enthalten können, deshalb bekommt er weder Bash noch Write: kein Befehl, keine neue Datei.
# `Edit` bleibt, ist aber wirkungslos außerhalb des Arbeitsordners, weil BEWERTEN_SPERREN das Home sperrt —
# pfadgebundene Regeln in --allowedTools greifen nicht, `deny` in einer eigenen Einstellungsdatei greift (nachgemessen).
# build.py führt der Server davor und danach selbst aus, wie beim Codex-Lauf.
BEWERTEN_SPERREN = {"permissions": {"deny": ["Edit(~/**)", "Write(~/**)"]}}
BEWERTEN_HINWEIS = (
    "Dieser Lauf kommt aus dem Dashboard-Server. Der Server hat build.py gerade ausgeführt, die Ausgabe steht unten, "
    "und führt es nach deinem Lauf erneut aus: Überspringe die Schritte 1 und 5, führe build.py nicht aus und öffne nichts. "
    "Du arbeitest in einer Arbeitskopie ({arbeitsordner}), nicht im Datenordner ({daten}): Ändere nur "
    "{arbeitsordner}/bewertung-claude-code.json. Das Inventar liegt in {daten}, die Skill-Dateien im Projektordner; beides nur lesen. "
    "Du hast keine Befehle zur Verfügung: Prüfe die Datei am Ende durch Lesen, nicht mit python3. "
    "Stelle keine Rückfragen. "
    "Deine Schlussnachricht ist der Bericht für das Dashboard: höchstens zehn Zeilen Markdown mit den drei Punkten aus Schritt 6."
)


def bewerten_argv(arbeitsordner, daten, einstellungen, build_ausgabe):
    return ["claude", "-p", f"/3zero-harness-inventur\n\nAusgabe von build.py:\n{build_ausgabe}",
            "--model", "opus", "--output-format", "json", "--settings", str(einstellungen),
            "--allowedTools", "Read", "Edit", "Glob", "Grep",
            "--append-system-prompt", BEWERTEN_HINWEIS.format(arbeitsordner=arbeitsordner, daten=daten)]

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
    "{arbeitsordner}/bewertung-codex.json. Das Inventar liegt in {daten}, die Skill-Dateien im Projektordner; beides nur lesen. "
    "Stelle keine Rückfragen. "
    "Deine Schlussnachricht ist der Bericht für das Dashboard: höchstens zehn Zeilen Markdown mit den drei Punkten aus Schritt 6 "
    "(was neu ist, was weggefallen ist, welche Empfehlungen sich geändert haben), keine Tabellen, keine Aufzählung aller Einträge."
)
# Codex kennt keine Befehlsliste wie --allowedTools. Die Grenze zieht die Sandbox: nur lesen, schreiben nur in den
# npm-Zwischenspeicher, Zugangsdaten gesperrt, Netz nur über den Codex-Proxy zu diesen Domains und nur lesend (limited).
SUCHEN_DOMAINS = ("skills.sh", "registry.npmjs.org", "github.com", "api.github.com", "raw.githubusercontent.com", "codeload.github.com")
GESPERRT_LESEN = (".ssh", ".gnupg", ".aws", ".netrc", ".config/gh", ".codex/auth.json", ".claude/.credentials.json",
                   ".npmrc", ".pypirc", ".claude.json", ".codex/config.toml", ".docker/config.json", ".kube/config", ".git-credentials",
                   ".codex/sessions", ".codex/archived_sessions", ".codex/history.jsonl", ".claude/projects")
# Die Claude-Suche liest fremde Webseiten und erreicht mit WebFetch jede Adresse. Dieselben Pfade wie bei Codex,
# je als Datei und als Ordner. Wirksam ist wie beim Bewerten nur `deny` in einer eigenen Einstellungsdatei; es gilt
# für Read, Grep und Glob (nachgemessen). `jq` liest Dateien außerhalb des Arbeitsordners ohnehin nicht, nur Pipes.
SUCHEN_SPERREN = {"permissions": {"deny": [regel for p in GESPERRT_LESEN for regel in (f"Read(~/{p})", f"Read(~/{p}/**)")]}}
CODEX_SUCHEN_HINWEIS = (
    "Dieser Lauf kommt aus dem Dashboard-Server und läuft in Codex (Harness codex). Der Bestand ist frisch eingelesen "
    "(inventar-codex.json), führe build.py nicht aus. Stelle keine Rückfragen: Ist der Bedarf vage, lege die naheliegende Deutung fest "
    "und nenne sie im Bericht; deckt der Bestand ihn ab, sage das. Installiere nichts in die Konfiguration des Benutzers und "
    "ändere keine Dateien. Dass npx sein eigenes Paket in den Zwischenspeicher lädt, gehört zum Suchen und ist kein Installieren: "
    "Rufe es als `npx -y skills find …` auf, damit es ohne Rückfrage nachlädt. "
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


def toml_pfad(tabelle):
    """Zerlegt einen Tabellenkopf wie plugins."a@b".mcp_servers.c in seine Schlüssel."""
    return [t.strip('"') for t in re.findall(r'"[^"]*"|[^.]+', tabelle)]


def toml_text_setzen(text, tabelle, schluessel, wert):
    """Setzt `schluessel = wert` in [tabelle]: vorhandene Zeile ersetzen, sonst in die Tabelle einfügen,
    sonst die Tabelle anhängen. Alles andere bleibt Zeichen für Zeichen stehen."""
    zeilen = text.splitlines()
    kopf = f"[{tabelle}]"
    ist_kopf = re.compile(rf"\s*{re.escape(kopf)}\s*(#.*)?")  # ein Kommentar hinter dem Kopf ist gültiges TOML
    try:
        start = next(i for i, z in enumerate(zeilen) if ist_kopf.fullmatch(z))
    except StopIteration:
        rumpf = text if text.endswith("\n") or not text else text + "\n"
        return f"{rumpf}\n{kopf}\n{schluessel} = {wert}\n"
    ende = next((i for i in range(start + 1, len(zeilen)) if zeilen[i].lstrip().startswith("[")), len(zeilen))
    muster = re.compile(rf"^\s*{re.escape(schluessel)}\s*=")
    for i in range(start + 1, ende):
        if muster.match(zeilen[i]):
            zeilen[i] = f"{schluessel} = {wert}"
            break
    else:
        zeilen.insert(start + 1, f"{schluessel} = {wert}")
    return "\n".join(zeilen) + "\n"


def codex_bewerten(ordner, daten, arbeitsordner, ergebnis, build_ausgabe, modell):
    hinweis = CODEX_BEWERTEN_HINWEIS.format(ordner=ordner, daten=daten, arbeitsordner=arbeitsordner)
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


def nur_manuell_gesetzt(text):
    """Steht `allow_implicit_invocation: false` im policy-Block? Nur dort zählt es. Ein gleichnamiger Schlüssel
    unter defaults: oder interface: ließe sonst einen fehlgeschlagenen Schreibvorgang als Erfolg durchgehen.
    Dieselbe Prüfung macht `nur_manuell` in codex.py beim Einlesen — die beiden gehören zusammen."""
    # Ein Kommentar oder eine Leerzeile auf Spalte 0 beendet den Block nicht, eine andere Zeile schon
    block = re.search(r"^policy:[^\S\n]*\n((?:(?:[ \t]+.*|[ \t]*#.*|[ \t]*)\n?)*)", text, re.M)
    return bool(block) and re.search(r"^\s+allow_implicit_invocation:\s*false\s*$", block.group(1), re.M) is not None


def gueltige_bewertung(kopie):
    """Die Arbeitskopie, wenn sie ein JSON-Objekt ist; sonst None. Ein Symlink zählt nicht: er könnte auf eine Datei außerhalb zeigen."""
    if not kopie.is_file() or kopie.is_symlink():
        return None
    try:
        bewertung = json.loads(kopie.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return bewertung if isinstance(bewertung, dict) else None


def zurueckschreiben(bewertung, ziel):
    ziel = ziel.resolve()  # einem Symlink folgen: os.replace ersetzte sonst den Link selbst durch eine normale Datei
    neu = ziel.with_name(ziel.name + ".neu")
    neu.write_text(json.dumps(bewertung, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(neu, ziel)


def in_den_papierkorb(ziel):
    """Verschiebt in den Papierkorb des Systems, unter einem freien Namen: `mv` scheiterte an einem gleichnamigen
    Ordner dort und überschrieb eine gleichnamige Datei still. Mac: ~/.Trash. Linux: der XDG-Papierkorb samt
    .trashinfo, ohne die der Dateimanager den Eintrag nicht zeigt und den alten Ort nicht kennt."""
    ziel = Path(ziel)
    if not (ziel.exists() or ziel.is_symlink()):
        return {"ok": False, "ausgabe": f"{ziel} gibt es nicht"}
    if sys.platform == "darwin":
        ablage, info = Path.home() / ".Trash", None
    elif sys.platform.startswith("linux"):
        korb = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
        ablage, info = korb / "files", korb / "info"
    else:
        return {"ok": False, "ausgabe": f"Den Papierkorb dieses Systems kennt der Server nicht. Bitte von Hand löschen: {ziel}"}
    for ordner in filter(None, (ablage, info)):
        ordner.mkdir(parents=True, exist_ok=True)
    name, nr = ziel.name, 1
    while (ablage / name).exists() or (ablage / name).is_symlink():
        nr += 1
        name = f"{ziel.stem} {nr}{ziel.suffix}"
    if info:
        (info / f"{name}.trashinfo").write_text(f"[Trash Info]\nPath={urllib.parse.quote(str(ziel.absolute()))}\n"
                                                f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n", encoding="utf-8")
    shutil.move(ziel, ablage / name)
    return {"ok": True, "ausgabe": f"{ziel} → {ablage / name}"}


# MCP-Server installiert der Server nicht selbst; sein Befehl kommt aus einem Lauf, der fremde Webseiten liest,
# und wird dem Benutzer zum Einfügen ins Terminal angeboten. Angezeigt wird er nur, wenn er mit einem bekannten
# Installationsbefehl beginnt und keine Shell-Metazeichen enthält — sonst könnte eine präparierte Seite dem
# Benutzer etwas diktieren, das wie eine Anbieter-Doku aussieht. Sonst bleibt das Feld leer.
BEFEHL_ANFANG = ("claude mcp add ", "codex mcp add ", "npx skills add ", "npx -y skills add ",
                 "claude plugin install ", "codex plugin add ")
BEFEHL_ZEICHEN = re.compile(r"[A-Za-z0-9 @%+=:,./_-]+")


def sicherer_befehl(befehl):
    return isinstance(befehl, str) and befehl.startswith(BEFEHL_ANFANG) and BEFEHL_ZEICHEN.fullmatch(befehl) is not None


def geprueft(k):
    """Ein Kandidat mit dem, was der Server verantworten kann: geprüfter Befehl, geprüfte Installierbarkeit."""
    return {**k, "befehl": k.get("befehl") if sicherer_befehl(k.get("befehl")) else "", "installierbar": installierbar(k)}


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


def beenden(prozess):
    """Beendet den Prozess samt Kindern: claude und codex starten ihre Werkzeuge als eigene Prozesse."""
    try:
        os.killpg(prozess.pid, signal.SIGKILL)  # eigene Prozessgruppe, siehe start_new_session in lauf()
    except ProcessLookupError:
        pass


def lauf(argv, cwd, timeout, gestartet=None):
    """`gestartet` bekommt den laufenden Prozess, damit ihn jemand abbrechen kann."""
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    except OSError as fehler:
        return {"ok": False, "ausgabe": str(fehler), "stdout": ""}
    if gestartet:
        gestartet(p)
    try:
        stdout, stderr = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as fehler:
        beenden(p)
        p.communicate()
        return {"ok": False, "ausgabe": str(fehler), "stdout": ""}
    # `ausgabe` ist für den Benutzer und enthält beides; `stdout` bleibt getrennt, weil eine geschweifte Klammer
    # in einer Warnung auf stderr sonst den JSON-Ausschnitt zerreißt und ein gelungener Lauf als Fehler gälte.
    return {"ok": p.returncode == 0, "ausgabe": (stdout + stderr).strip(), "stdout": stdout}


class Inventarserver(ThreadingHTTPServer):
    def __init__(self, port, ordner, settings, token, daten):
        """`ordner` ist das Werkzeug (build.py, template.html), `daten` sind die Dateien des Benutzers."""
        super().__init__(("127.0.0.1", port), Handler)
        self.ordner, self.settings, self.token = Path(ordner), Path(settings), token
        self.daten = Path(daten)
        self.hosts = {f"{h}:{self.server_address[1]}" for h in ("127.0.0.1", "localhost")}
        self.sperre = threading.Lock()  # Aktionen schreiben teils dieselbe settings.json, also nacheinander
        self.gesichert = set()
        self.kandidaten = []  # aus der letzten Suche; nur sie lassen sich installieren
        # Stand der Claude-Läufe „suche“ und „bewertung“: Sie laufen im Hintergrund, der Browser fragt ihn ab.
        # Eigene Sperre, weil self.sperre während einer Bewertung minutenlang belegt ist.
        self.laeufe, self.laeufe_sperre = {}, threading.Lock()
        self.prozesse = {}  # der laufende claude- oder codex-Prozess je Art, damit abbrechen() ihn beenden kann
        self.codex_config = Path.home() / ".codex" / "config.toml"
        self.codex_skills = Path.home() / ".agents" / "skills"  # dorthin legt npx skills die Skills

    def harness(self):
        return harness.aktuell(self.daten)

    def datei(self, art):
        """inventar oder dashboard des Harness, in dem der Skill zuletzt gestartet wurde."""
        return self.daten / harness.HARNESSE[self.harness()][art]

    def schritt(self, eid, aktion):
        """(Name, Schritt) aus dem Inventar des aktuellen Harness, oder None, wenn der Eintrag diese Aktion nicht anbietet."""
        inventar = json.loads(self.datei("inventar").read_text(encoding="utf-8"))
        for e in (inventar["mcp"] + inventar["plugins"] + inventar["skills"] + inventar.get("projekt_skills", [])
                  + inventar.get("projekt_mcp", []) + inventar.get("hooks", [])):
            if e["id"] == eid and aktion in e.get("aktionen", {}):
                return e["name"], e["aktionen"][aktion]
        return None

    def sichern(self, datei):
        """Einmal je Datei und Serverlauf eine Sicherung <datei>.bak-<Zeitstempel>, bevor der Server sie zum ersten Mal
        ändert. Mit festem Namen überschrieb der nächste Lauf sie mit dem Stand, den der Server selbst schon geändert hatte."""
        if datei in self.gesichert:
            return
        shutil.copy2(datei, datei.with_name(f"{datei.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))  # übernimmt Rechte und Zeitstempel
        self.gesichert.add(datei)

    def schreiben(self, datei, text):
        """Schreibt atomar über <datei>.neu + os.replace, ohne die Datei zwischendurch mit weiteren
        Rechten als nötig auf der Platte liegen zu haben: die echte config.toml kann Zugangsdaten enthalten."""
        # einem Symlink folgen (Dotfiles-Repo): os.replace ersetzte sonst den Link selbst durch eine normale Datei
        datei = datei.resolve()
        neu = datei.with_name(datei.name + ".neu")
        fd = os.open(neu, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        modus = datei.stat().st_mode & 0o777 if datei.exists() else 0o600
        os.chmod(neu, modus)
        os.replace(neu, datei)

    def settings_aendern(self, aendern):
        text = self.settings.read_text(encoding="utf-8")
        daten = json.loads(text)
        self.sichern(self.settings)
        aendern(daten)
        self.schreiben(self.settings, json.dumps(daten, indent=2, ensure_ascii=False) + "\n")

    def config_aendern(self, neuer_text, pruefen):
        """Schreibt config.toml und nimmt die Änderung zurück, wenn sie die Datei zerstört oder nicht wirkt."""
        alt = self.codex_config.read_text(encoding="utf-8")
        self.sichern(self.codex_config)
        self.schreiben(self.codex_config, neuer_text)
        try:
            daten = tomllib.loads(self.codex_config.read_text(encoding="utf-8"))
            if not pruefen(daten):
                raise ValueError("Die Einstellung steht nach dem Schreiben nicht in der Datei.")
        except Exception as fehler:  # jeder Fehler nach dem Schreiben muss zurückrollen, nicht nur ValueError/OSError
            self.schreiben(self.codex_config, alt)
            return {"ok": False, "ausgabe": f"{self.codex_config} nicht geändert: {fehler}"}
        return None

    def toml_setzen(self, tabelle, schluessel, wert):
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        alt = self.codex_config.read_text(encoding="utf-8")
        pfad = toml_pfad(tabelle) + [schluessel]

        def pruefen(daten):
            # wert ist ein TOML-Literal; der Vergleich über json.dumps setzt voraus, dass seine JSON- und
            # TOML-Schreibweise übereinstimmen. Gilt für "false" (heute das Einzige, was der Server erzeugt).
            for teil in pfad[:-1]:
                daten = daten.get(teil) if isinstance(daten, dict) else None
                if daten is None:
                    return False
            return json.dumps(daten.get(pfad[-1])) == wert

        fehler = self.config_aendern(toml_text_setzen(alt, tabelle, schluessel, wert), pruefen)
        return fehler or {"ok": True, "ausgabe": f"~/.codex/config.toml → [{tabelle}] {schluessel} = {wert}"}

    def skill_abschalten(self, pfad):
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        alt = self.codex_config.read_text(encoding="utf-8")
        eintrag = {"path": pfad, "enabled": False}
        pruefen = lambda daten: eintrag in (daten.get("skills") or {}).get("config", [])
        if pruefen(tomllib.loads(alt)):
            return {"ok": True, "ausgabe": f"steht schon in ~/.codex/config.toml: [[skills.config]] path = {json.dumps(pfad)}"}
        block = f'\n[[skills.config]]\npath = {json.dumps(pfad, ensure_ascii=False)}\nenabled = false\n'
        fehler = self.config_aendern((alt if alt.endswith("\n") else alt + "\n") + block, pruefen)
        return fehler or {"ok": True, "ausgabe": f"~/.codex/config.toml → [[skills.config]] path = {json.dumps(pfad)}, enabled = false"}

    def nur_manuell_datei(self, ordner):
        """agents/openai.yaml im Skill-Ordner: policy.allow_implicit_invocation = false, damit Codex den Skill
        nur noch per $name lädt. Ändert nur innerhalb des policy:-Blocks (Einrückung 0 bis zur nächsten nicht
        leeren Zeile mit Einrückung 0) und übernimmt dessen Einrückung; alles andere bleibt erhalten. Schreibt
        bei fehlgeschlagener Prüfung den ursprünglichen Stand zurück, wie config_aendern es für config.toml tut."""
        if not (Path(ordner) / "SKILL.md").is_file():
            return {"ok": False, "ausgabe": f"{ordner} enthält keine SKILL.md"}
        datei = Path(ordner) / "agents" / "openai.yaml"
        alt = datei.read_text(encoding="utf-8") if datei.is_file() else None
        if alt is not None:
            zeilen = alt.splitlines()
            start = next((i for i, z in enumerate(zeilen) if re.match(r"^policy:\s*", z)), None)
            if start is not None and not re.fullmatch(r"policy:\s*", zeilen[start]):
                art = "eine Inline-Notation" if "{" in zeilen[start] else "einen Wert"
                return {"ok": False, "ausgabe": f"{datei} nutzt {art} für policy, bitte von Hand ändern"}
            if start is None:
                zeilen += ["policy:", "  allow_implicit_invocation: false"]
            else:
                # Leerzeilen und Kommentare auf Spalte 0 beenden den Block nicht, nur eine echte neue Zeile auf Spalte 0
                ende = next((i for i in range(start + 1, len(zeilen))
                             if zeilen[i].strip() and not zeilen[i].lstrip().startswith("#")
                             and len(zeilen[i]) == len(zeilen[i].lstrip())), len(zeilen))
                muster = re.compile(r"^\s+allow_implicit_invocation\s*:")
                for i in range(start + 1, ende):
                    if muster.match(zeilen[i]):
                        einrueckung = zeilen[i][:len(zeilen[i]) - len(zeilen[i].lstrip())]
                        zeilen[i] = f"{einrueckung}allow_implicit_invocation: false"
                        break
                else:
                    folge = next((z for z in zeilen[start + 1:ende] if z.strip()), None)
                    einrueckung = folge[:len(folge) - len(folge.lstrip())] if folge else "  "
                    zeilen.insert(start + 1, f"{einrueckung}allow_implicit_invocation: false")
            text = "\n".join(zeilen) + "\n"
            self.sichern(datei)
        else:
            datei.parent.mkdir(parents=True, exist_ok=True)
            text = "policy:\n  allow_implicit_invocation: false\n"
        self.schreiben(datei, text)
        if not nur_manuell_gesetzt(datei.read_text(encoding="utf-8")):
            if alt is not None:
                self.schreiben(datei, alt)
            else:
                datei.unlink()
            return {"ok": False, "ausgabe": f"{datei} nicht geändert"}
        return {"ok": True, "ausgabe": f"{datei} → policy: allow_implicit_invocation: false"}

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

    def hook_entfernen(self, schluessel):
        """Nimmt den Block eines Ereignisses (oder die Statuszeile) aus settings.json; zurück führt die Sicherung."""
        ort = lambda daten: daten if schluessel == "statusLine" else daten.get("hooks")
        vorhanden = ort(json.loads(self.settings.read_text(encoding="utf-8")))
        if not isinstance(vorhanden, dict) or schluessel not in vorhanden:
            return {"ok": False, "ausgabe": f"{schluessel} steht nicht in {self.settings}"}  # ohne die Datei anzufassen

        def aendern(daten):
            del ort(daten)[schluessel]
            if daten.get("hooks") == {}:
                del daten["hooks"]
        self.settings_aendern(aendern)
        return {"ok": True, "ausgabe": f"{self.settings} → {schluessel} entfernt"}

    def ausfuehren(self, name, schritt):
        with self.sperre:
            if "override" in schritt:
                return self.override_setzen(name, schritt["override"])
            if "sperren" in schritt:
                return self.mcp_sperren(schritt["sperren"])
            if "toml" in schritt:
                return self.toml_setzen(**schritt["toml"])
            if "skill_aus" in schritt:
                return self.skill_abschalten(schritt["skill_aus"])
            if "nur_manuell" in schritt:
                return self.nur_manuell_datei(schritt["nur_manuell"])
            if "papierkorb" in schritt:
                return in_den_papierkorb(schritt["papierkorb"])
            if "hook_entfernen" in schritt:
                return self.hook_entfernen(schritt["hook_entfernen"])
            return lauf(schritt["argv"], schritt.get("cwd"), 120)

    def neu_einlesen(self):
        with self.sperre:
            return lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)

    def bewerten(self):
        """Wie bei Codex: Der Lauf bekommt kein Bash und kein Write und arbeitet auf einer Kopie der Bewertung
        in einem Temp-Ordner; nur ein gültiges JSON-Objekt geht zurück. build.py führt der Server davor und danach."""
        if self.harness() == "codex":
            return self.bewerten_codex()
        with self.sperre:
            r = lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)
            if not r["ok"]:
                return {"ok": False, "ausgabe": "Bestand einlesen fehlgeschlagen:\n\n" + r["ausgabe"]}
            bewertung_datei = self.daten / harness.HARNESSE["claude-code"]["bewertung"]
            with tempfile.TemporaryDirectory() as tmp:
                arbeitsordner = Path(tmp)
                kopie = arbeitsordner / bewertung_datei.name
                kopie.write_text(bewertung_datei.read_text(encoding="utf-8") if bewertung_datei.is_file() else "{}", encoding="utf-8")
                einstellungen = arbeitsordner / "settings.json"
                einstellungen.write_text(json.dumps(BEWERTEN_SPERREN), encoding="utf-8")
                c = self.langer_lauf("bewertung", bewerten_argv(arbeitsordner, self.daten, einstellungen, r["ausgabe"]), arbeitsordner)
                text = claude_text(c["ausgabe"])
                if not c["ok"]:
                    return {"ok": False, "ausgabe": text}
                bewertung = gueltige_bewertung(kopie)
                if bewertung is None:
                    return {"ok": False, "ausgabe": "Der Lauf hat keine gültige bewertung-claude-code.json hinterlassen: " + text}
                zurueckschreiben(bewertung, bewertung_datei)
            n = lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)
        return {"ok": n["ok"], "ausgabe": text if n["ok"] else text + "\n\nNeu bauen fehlgeschlagen:\n\n" + n["ausgabe"]}

    def bewerten_codex(self):
        """build.py liest mit Netz ein (claude mcp list, gh); Codex läuft ohne Netz, deshalb baut der Server davor und danach.
        Codex bekommt keinen Schreibzugriff auf den Projektordner (die Bewertung liest Skill-Texte, die Prompt-Injection enthalten
        könnten), sondern arbeitet auf einer Arbeitskopie von bewertung-codex.json; nur ein gültiges JSON-Objekt geht zurück."""
        with self.sperre:
            r = lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)
            if not r["ok"]:
                return {"ok": False, "ausgabe": "Bestand einlesen fehlgeschlagen:\n\n" + r["ausgabe"]}
            bewertung_datei = self.daten / harness.HARNESSE["codex"]["bewertung"]
            with tempfile.TemporaryDirectory() as tmp:
                arbeitsordner = Path(tmp)
                (arbeitsordner / "bewertung-codex.json").write_text(
                    bewertung_datei.read_text(encoding="utf-8") if bewertung_datei.is_file() else "{}", encoding="utf-8")
                ergebnis = arbeitsordner / "ergebnis.md"
                c = self.langer_lauf("bewertung", codex_bewerten(self.ordner, self.daten, arbeitsordner, ergebnis, r["ausgabe"],
                                                                 codex_modell(self.codex_config)), arbeitsordner)
                text = ergebnis.read_text(encoding="utf-8").strip() if c["ok"] and ergebnis.is_file() else c["ausgabe"]
                if not c["ok"]:
                    return {"ok": False, "ausgabe": text}
                bewertung = gueltige_bewertung(arbeitsordner / "bewertung-codex.json")
                if bewertung is None:
                    return {"ok": False, "ausgabe": "Codex hat keine gültige bewertung-codex.json hinterlassen: " + text}
                zurueckschreiben(bewertung, bewertung_datei)
            n = lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)
        return {"ok": n["ok"], "ausgabe": text if n["ok"] else text + "\n\nNeu bauen fehlgeschlagen:\n\n" + n["ausgabe"]}

    def suchen(self, bedarf):
        # Beide brauchen die Sperre für build.py, und die Bewertung hält sie bis zu 20 Minuten. Ohne diese
        # Auskunft stünde die Suche so lange auf „läuft“, ohne dass ein Suchlauf existierte.
        if (self.laeufe.get("bewertung") or {}).get("laeuft"):
            return {"ok": False, "ausgabe": "Die Bewertung läuft gerade und liest den Bestand neu ein. "
                                            "Warte, bis sie fertig ist, dann noch einmal suchen."}
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
            einstellungen = Path(tmp) / "settings.json"
            einstellungen.write_text(json.dumps(SUCHEN_SPERREN), encoding="utf-8")
            r = self.langer_lauf("suche", [*argv, "--settings", str(einstellungen)], tmp)
        ergebnis = (claude_json(r["stdout"]) or {}).get("structured_output")
        if not r["ok"] or not isinstance(ergebnis, dict):
            return {"ok": False, "ausgabe": claude_text(r["ausgabe"])}
        self.kandidaten = [geprueft(k) for k in ergebnis.get("kandidaten", []) if isinstance(k, dict)]
        return {"ok": True, "ausgabe": str(ergebnis.get("bericht", "")), "kandidaten": self.kandidaten}

    def suchen_codex(self, bedarf):
        with tempfile.TemporaryDirectory() as tmp:  # außerhalb von ~, wie bei Claude
            schema, ergebnis = Path(tmp) / "schema.json", Path(tmp) / "ergebnis.json"
            schema.write_text(json.dumps(SUCHEN_SCHEMA_CODEX), encoding="utf-8")
            (Path(tmp) / "npm-cache").mkdir()
            r = self.langer_lauf("suche", codex_suchen(bedarf, tmp, schema, ergebnis, codex_modell(self.codex_config)), tmp)
            try:
                daten = json.loads(ergebnis.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                daten = None
        if not r["ok"] or not isinstance(daten, dict):
            return {"ok": False, "ausgabe": r["ausgabe"]}
        kandidaten = [{f: w for f, w in k.items() if w is not None} for k in daten.get("kandidaten", []) if isinstance(k, dict)][:3]
        self.kandidaten = [geprueft(k) for k in kandidaten]
        return {"ok": True, "ausgabe": str(daten.get("bericht", "")), "kandidaten": self.kandidaten}

    def im_hintergrund(self, art, arbeit, **angaben):
        """Startet einen minutenlangen Claude-Lauf. Eine so lange offene Anfrage riss ab, wenn der Browser neu lud,
        und das Ergebnis war verloren; jetzt bleibt es in self.laeufe, bis der Browser es verwirft."""
        with self.laeufe_sperre:
            if (self.laeufe.get(art) or {}).get("laeuft"):
                return {"ok": False, "ausgabe": "Das läuft schon."}
            self.laeufe[art] = {"laeuft": True, "beginn": time.time(), **angaben}
            self.prozesse.pop(art, None)

        def los():
            try:
                ergebnis = arbeit()
            except Exception as fehler:  # sonst bliebe der Lauf für immer „läuft“ und sperrte jeden weiteren
                ergebnis = {"ok": False, "ausgabe": f"{type(fehler).__name__}: {fehler}"}
            with self.laeufe_sperre:
                if self.laeufe[art].get("abgebrochen"):
                    ergebnis = {"ok": False, "abgebrochen": True, "ausgabe": "Abgebrochen."}
                self.laeufe[art] = {"laeuft": False, **angaben, **ergebnis}
        threading.Thread(target=los, daemon=True).start()
        return {"ok": True, "laeuft": True}

    def langer_lauf(self, art, argv, cwd):
        """Ein claude- oder codex-Lauf, den abbrechen() beenden kann."""
        def gestartet(prozess):
            with self.laeufe_sperre:
                self.prozesse[art] = prozess
                if (self.laeufe.get(art) or {}).get("abgebrochen"):  # der Abbruch kam, während build.py davor noch lief
                    beenden(prozess)
        return lauf(argv, cwd, 1200, gestartet)

    def abbrechen(self, art):
        """Ein Lauf hält die Sperre bis zu 20 Minuten; ohne Abbruch half nur, den Server zu beenden."""
        with self.laeufe_sperre:
            lauf_ = self.laeufe.get(art)
            if not lauf_ or not lauf_["laeuft"]:
                return {"ok": False, "ausgabe": "Da läuft nichts."}
            prozess = self.prozesse.get(art)
            if prozess and prozess.poll() is not None:
                # claude ist schon durch und die Bewertung zurückgeschrieben; „abgebrochen“ wäre jetzt falsch
                return {"ok": False, "ausgabe": "Der Lauf ist schon durch, das Ergebnis kommt gleich."}
            lauf_["abgebrochen"] = True
            if prozess:
                beenden(prozess)
        return {"ok": True}

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

    def installieren(self, k, nur_manuell):
        """`k` ist der Kandidat selbst, nicht seine Nummer: Zwischen Prüfung und Zugriff konnte eine neue Suche
        die Liste austauschen, und der Zugriff lief ins Leere."""
        codex = self.harness() == "codex"
        if k["art"] == "skill":
            argv = ["npx", "-y", "skills", "add", k["repo"], "--skill", k["skill"], "-g", "-y",
                    "--agent", "codex" if codex else "claude-code"]
        elif codex:
            argv = ["codex", "plugin", "add", f"{k['plugin']}@{k['marktplatz']}"]
        else:
            argv = ["claude", "plugin", "install", f"{k['plugin']}@{k['marktplatz']}"]
        with self.sperre:
            with tempfile.TemporaryDirectory() as tmp:  # frischer Ordner statt $TMPDIR direkt, siehe suchen()
                r = lauf(argv, tmp, 300)
        if r["ok"] and nur_manuell and k["art"] == "skill":
            with self.sperre:
                nachher = (self.nur_manuell_datei(self.codex_skills / k["skill"]) if codex
                           else self.override_setzen(k["skill"], "user-invocable-only"))
            if nachher["ok"]:
                r["ausgabe"] += "\n" + nachher["ausgabe"]
            else:
                r["ausgabe"] += "\ninstalliert, aber nur manuell nicht gesetzt: " + nachher["ausgabe"]
        if r["ok"]:
            k["installiert"] = True
        return r


class Handler(BaseHTTPRequestHandler):
    def antworte(self, status, inhalt, typ="application/json"):
        daten = (inhalt if isinstance(inhalt, str) else json.dumps(inhalt, ensure_ascii=False)).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", typ + "; charset=utf-8")
        self.send_header("Content-Length", str(len(daten)))
        # Die Host-Prüfung hält fremde Seiten vom Lesen ab, nicht vom Einbetten: In einem unsichtbaren Rahmen
        # ließe sich ein Klick des Benutzers auf „Bewertung neu erstellen“ oder eine Aktion umleiten.
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")  # die Adresse trägt den Schlüssel; Links nach draußen nehmen sie nicht mit
        try:
            self.end_headers()
            self.wfile.write(daten)
        except (BrokenPipeError, ConnectionResetError):
            pass  # der Browser hat die Seite neu geladen oder geschlossen

    def do_GET(self):
        # Host-Prüfung gegen DNS-Rebinding: sonst könnte eine fremde Seite das Token mitlesen
        if self.headers.get("Host") not in self.server.hosts:
            return self.antworte(403, "fremder Host", "text/plain")
        adresse = urllib.parse.urlsplit(self.path)
        if adresse.path != "/":
            return self.antworte(404, "nicht gefunden", "text/plain")
        # 127.0.0.1 erreicht jeder Benutzer des Rechners, nicht nur der, der den Server gestartet hat. Die Seite mit
        # dem Token bekommt deshalb nur, wer den Schlüssel aus der Startdatei oder dem Terminal in der Adresse trägt.
        schluessel = urllib.parse.parse_qs(adresse.query).get("k", [""])[0]
        if not secrets.compare_digest(schluessel.encode(), self.server.token.encode()):
            return self.antworte(403, "Das Dashboard öffnet sich nur mit dem Schlüssel des laufenden Servers: "
                                      f"{self.server.daten / 'start.html'} öffnen oder die Adresse aus dem Terminal nehmen, "
                                      "in dem server.py läuft.", "text/plain")
        html = self.server.datei("dashboard").read_text(encoding="utf-8")
        html = html.replace(TOKEN_PLATZHALTER, TOKEN_PLATZHALTER.replace("null", json.dumps(self.server.token)), 1)
        self.antworte(200, html, "text/html")

    def do_POST(self):
        token = self.headers.get("X-Token", "").encode()
        if self.headers.get("Host") not in self.server.hosts or not secrets.compare_digest(token, self.server.token.encode()):
            return self.antworte(403, {"ok": False, "ausgabe": "nicht erlaubt"})
        # Beide Inventare führen dieselben IDs mit denselben Aktionsnamen. Startet der Skill im anderen Harness,
        # während die Seite offen ist, träfe ein Klick sonst dessen Konfiguration und meldete „erledigt“.
        aktuell = self.server.harness()
        if self.headers.get("X-Harness") != aktuell:
            return self.antworte(409, {"ok": False, "ausgabe": f"Der Server steht inzwischen auf {harness.HARNESSE[aktuell]['name']}, "
                                                               "diese Seite zeigt einen anderen Harness. Lade die Seite neu."})
        if self.path == "/api/neu":
            return self.antworte(200, self.server.neu_einlesen())
        if self.path not in ("/api/ausfuehren", "/api/bewerten", "/api/bewertung", "/api/suchen", "/api/suche", "/api/installieren", "/api/abbrechen"):
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
        if self.path == "/api/abbrechen":
            art = anfrage.get("art") if isinstance(anfrage, dict) else None
            if art not in ("bewertung", "suche"):
                return self.antworte(400, {"ok": False, "ausgabe": "Abbrechen lassen sich nur Bewertung und Suche."})
            return self.antworte(200, self.server.abbrechen(art))
        if self.path in ("/api/suche", "/api/bewertung"):
            art = self.path.removeprefix("/api/")
            return self.antworte(200, self.server.stand(art, isinstance(anfrage, dict) and anfrage.get("verwerfen") is True))
        if self.path == "/api/installieren":
            nr = anfrage.get("nr") if isinstance(anfrage, dict) else None
            kandidaten = self.server.kandidaten  # einmal festhalten: eine neue Suche tauscht die Liste aus
            if type(nr) is not int or not 0 <= nr < len(kandidaten) or not kandidaten[nr]["installierbar"]:
                return self.antworte(400, {"ok": False, "ausgabe": "Diesen Kandidaten kann das Dashboard nicht installieren."})
            try:
                ergebnis = self.server.installieren(kandidaten[nr], anfrage.get("nur_manuell") is True)
            except (OSError, ValueError, AttributeError, TypeError) as fehler:
                ergebnis = {"ok": False, "ausgabe": f"{type(fehler).__name__}: {fehler}"}
            return self.antworte(200, ergebnis)
        try:
            treffer = self.server.schritt(anfrage["id"], anfrage["aktion"])
        except (ValueError, KeyError, TypeError):
            treffer = None
        if not treffer:
            return self.antworte(400, {"ok": False, "ausgabe": "Diese Aktion bietet der Eintrag nicht an."})
        try:
            ergebnis = self.server.ausfuehren(*treffer)
        except (OSError, ValueError, AttributeError, TypeError) as fehler:
            ergebnis = {"ok": False, "ausgabe": f"{type(fehler).__name__}: {fehler}"}
        self.antworte(200, ergebnis)

    def log_message(self, *args):
        pass


def startdatei(daten, url):
    """Eine Seite, die zum Dashboard weiterleitet, lesbar nur für den Benutzer. Der Browser bekommt sie statt der
    Adresse: Die trägt den Schlüssel, und Argumente eines Befehls sieht jeder Benutzer des Rechners in `ps`."""
    datei = Path(daten) / "start.html"
    fd = os.open(datei, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url={url}">'
                f'<a href="{url}">Dashboard öffnen</a>\n')
    os.chmod(datei, 0o600)  # gab es die Datei schon, behielte sie sonst ihre alten Rechte
    return datei


STANDARDBROWSER = ('use framework "AppKit"',
                   "return ((current application's NSWorkspace's sharedWorkspace)'s URLForApplicationToOpenURL:"
                   "(current application's NSURL's URLWithString:\"https://python.org\"))'s |path|() as text")


def browser_oeffnen(datei):
    """Öffnet die Startdatei im Browser. Pythons webbrowser schickt file:-Adressen unter macOS per AppleScript an den
    Browser, holt ihn aber nicht nach vorn (nachgemessen, mit http-Adressen schon): Das Dashboard ging im Hintergrund
    auf, und es sah aus, als geschähe nichts. `open -a` holt ihn nach vorn — mit dem Standardbrowser statt nur der
    Datei, weil .html auch einem Editor gehören kann. Wer BROWSER setzt, bekommt weiter Pythons Weg."""
    if sys.platform == "darwin" and not os.environ.get("BROWSER"):
        try:
            r = subprocess.run(["osascript", *[x for zeile in STANDARDBROWSER for x in ("-e", zeile)]], capture_output=True, text=True, timeout=30)
            if r.returncode == 0 and r.stdout.strip() and subprocess.run(["open", "-a", r.stdout.strip(), str(datei)]).returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    webbrowser.open(Path(datei).as_uri())


def main():
    try:
        srv = Inventarserver(PORT, HIER, Path.home() / ".claude" / "settings.json", secrets.token_hex(16), harness.DATEN)
    except OSError:
        sys.exit(f"Port {PORT} ist belegt. Läuft der Server schon? Dann {harness.DATEN / 'start.html'} öffnen.")
    print("Lese den Bestand ein …")
    ergebnis = srv.neu_einlesen()
    print(ergebnis["ausgabe"])
    if not ergebnis["ok"]:
        sys.exit(1)
    url = f"http://127.0.0.1:{PORT}/?k={srv.token}"
    start = startdatei(harness.DATEN, url)
    print(f"Dashboard: {url}  (beenden mit Ctrl+C)")
    browser_oeffnen(start)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        start.unlink(missing_ok=True)  # der Schlüssel gilt nur für diesen Lauf
        for prozess in srv.prozesse.values():  # eigene Prozessgruppe: Ctrl+C im Terminal erreicht sie nicht mehr
            beenden(prozess)


if __name__ == "__main__":
    main()
