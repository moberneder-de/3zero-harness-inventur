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

if sys.version_info < (3, 11):  # /usr/bin/python3 ist auf dem Mac 3.9; import tomllib scheiterte dort mit einem Traceback
    sys.exit(f"server.py braucht Python 3.11 oder neuer, gestartet mit {sys.version.split()[0]}.")

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
# --allowedTools genehmigt nur vorab und schränkt nichts ein; mit `defaultMode: auto` in den Einstellungen des
# Benutzers liefen auch andere Werkzeuge. Die Grenze ziehen CLAUDE_GRENZEN: --tools legt fest, welche Werkzeuge es
# gibt, --restricted ignoriert die Einstellungen des Benutzers und begrenzt die Datei-Werkzeuge auf den Arbeitsordner
# und --add-dir, hier das Home zum Lesen. Darin sperrt BEWERTEN_SPERREN `Edit`; beschreibbar bleibt nur der
# Arbeitsordner im Temp-Ordner — ohne --restricted schrieb Edit auch außerhalb des Home, etwa in /opt/homebrew
# (nachgemessen). build.py führt der Server davor und danach selbst aus, wie beim Codex-Lauf.
BEWERTEN_SPERREN = {"permissions": {"deny": ["Edit(~/**)", "Write(~/**)"]}}
# Ohne Sitzungsprotokoll (sonst zählte build.py die Läufe in den Messwerten mit), ohne MCP-Server des Benutzers,
# und was eine Rückfrage bräuchte, lehnt der Lauf ab.
CLAUDE_GRENZEN = ["--restricted", "--strict-mcp-config", "--no-session-persistence", "--permission-prompts", "none",
                  "--add-dir", str(Path.home())]
# --restricted lädt ~/.claude/skills nicht, ein Plugin per --plugin-dir schon (nachgemessen): Der Skill heißt dann so.
SKILL_AUFRUF = "/inventur:3zero-harness-inventur"
BEWERTEN_HINWEIS = (
    "Dieser Lauf kommt aus dem Dashboard-Server. Der Server hat build.py gerade ausgeführt, die Ausgabe steht unten, "
    "und führt es nach deinem Lauf erneut aus: Überspringe die Schritte 1 und 5, führe build.py nicht aus und öffne nichts. "
    "Du arbeitest in einer Arbeitskopie ({arbeitsordner}), nicht im Datenordner ({daten}): Ändere nur "
    "{arbeitsordner}/bewertung-claude-code.json. Das Inventar liegt in {daten}, die Skill-Dateien im Projektordner; beides nur lesen. "
    "Du hast keine Befehle zur Verfügung: Prüfe die Datei am Ende durch Lesen, nicht mit python3. "
    "Stelle keine Rückfragen. "
    "Deine Schlussnachricht ist der Bericht für das Dashboard: höchstens zehn Zeilen Markdown mit den drei Punkten aus Schritt 6."
)


def skill_plugin(skill, arbeitsordner):
    """Der Skill als Plugin im Arbeitsordner, für --plugin-dir. Dort, weil --restricted die Datei-Werkzeuge auf den
    Arbeitsordner begrenzt und der Lauf den Skill sonst nicht lesen könnte; als Kopie, weil Edit über einen Symlink
    den Skill im Projekt ändern könnte."""
    plugin = Path(arbeitsordner) / "inventur-plugin"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "inventur"}), encoding="utf-8")
    shutil.copytree(skill, plugin / "skills" / "3zero-harness-inventur")
    return plugin


def bewerten_argv(arbeitsordner, daten, einstellungen, build_ausgabe, plugin):
    return ["claude", "-p", f"{SKILL_AUFRUF}\n\nAusgabe von build.py:\n{build_ausgabe}",
            "--model", "opus", "--output-format", "json", "--settings", str(einstellungen),
            "--tools", "Read,Edit,Glob,Grep", "--allowedTools", "Read", "Edit", "Glob", "Grep",
            "--permission-mode", "acceptEdits", *CLAUDE_GRENZEN, "--plugin-dir", str(plugin),
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
    "art plugin mit plugin und marktplatz, art mcp nur mit befehl aus der Doku des Anbieters. "
    "belege: die Zahlen aus S4 (installationen, sterne, letzter_push als JJJJ-MM-TT, lizenz), unbekannt null."
)
# Belege aus S4 je Kandidat; unbekannt ist null
BELEGE = {"installationen": "integer", "sterne": "integer", "letzter_push": "string", "lizenz": "string"}
BELEGE_SCHEMA = {"type": "object", "additionalProperties": False, "required": list(BELEGE),
                 "properties": {f: {"type": [typ, "null"]} for f, typ in BELEGE.items()}}
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
                "plugin": {"type": "string"}, "marktplatz": {"type": "string"}, "belege": BELEGE_SCHEMA,
            },
            "required": ["art", "name", "was", "befehl"],
        }},
    },
    "required": ["bericht", "kandidaten"],
}
SICHERUNGEN = 10  # so viele <datei>.bak-<Zeitstempel> bleiben je Datei
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
    "art plugin mit plugin und marktplatz, art mcp nur mit befehl aus der Doku des Anbieters; nicht zutreffende Felder sind null. "
    "belege: die Zahlen aus S4 (installationen, sterne, letzter_push als JJJJ-MM-TT, lizenz), unbekannt null."
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
            "required": ["art", "name", "was", "befehl", "repo", "skill", "plugin", "marktplatz", "belege"],
            "properties": {
                "art": {"type": "string", "enum": ["skill", "plugin", "mcp"]},
                "name": {"type": "string"}, "was": {"type": "string"}, "befehl": {"type": "string"},
                **{f: {"type": ["string", "null"]} for f in ("repo", "skill", "plugin", "marktplatz")},
                "belege": BELEGE_SCHEMA,
            },
        }},
    },
}


def codex_modell(config):
    """Modell und Denktiefe aus ~/.codex/config.toml: --ignore-user-config ließe sie sonst weg."""
    try:
        cfg = tomllib.loads(Path(config).read_text(encoding="utf-8-sig"))  # utf-8-sig: mit BOM scheiterte tomllib
    except (OSError, ValueError):
        return []
    argv = ["-m", cfg["model"]] if isinstance(cfg.get("model"), str) else []
    if isinstance(cfg.get("model_reasoning_effort"), str):
        argv += ["-c", f"model_reasoning_effort={json.dumps(cfg['model_reasoning_effort'])}"]
    return argv


def toml_pfad(tabelle):
    """Zerlegt einen Tabellenkopf wie plugins."a@b".mcp_servers.c in seine Schlüssel."""
    return [t.strip('"') for t in re.findall(r'"[^"]*"|[^.]+', tabelle)]


def text_lesen(datei):
    """Liest ohne Übersetzung der Zeilenenden: read_text() machte aus CRLF stillschweigend LF. Eine BOM bleibt als
    erstes Zeichen stehen, damit die Rücknahme die Datei genau so wiederherstellt."""
    return Path(datei).read_bytes().decode("utf-8")


def zeilen_trennen(text):
    """(Zeilen, Zeilenende): CRLF, wenn die Datei so geschrieben ist. Zusammensetzen mit nl.join(zeilen) + nl."""
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.removesuffix(nl).split(nl), nl


def toml_kopf(zeile):
    """(Schlüssel, Array-Tabelle?) eines Tabellenkopfs wie [ a."b" ] oder [[a.b]], sonst None. Über die Schlüssel
    verglichen findet sich der Kopf auch mit Leerzeichen, Anführungszeichen oder Kommentar, wie ein Mensch ihn schreibt."""
    if not zeile.lstrip().startswith("["):
        return None
    try:
        daten = tomllib.loads(zeile)
    except ValueError:
        return None
    pfad = []
    while isinstance(daten, dict) and daten:
        (schluessel, daten), = daten.items()
        pfad.append(schluessel)
    return pfad, isinstance(daten, list)


# Strings und Kommentare in TOML, von links gelesen; nur so lässt sich sagen, ob eine Zeile in einem mehrzeiligen String liegt
TOML_STRINGS = re.compile(r'"""(?:[^\\]|\\.)*?"""(?!")|\'\'\'.*?\'\'\'(?!\')|"(?:[^"\\\n]|\\.)*"|\'[^\'\n]*\'|#[^\n]*', re.S)


def toml_zeilen(text):
    """(Zeilen, Zeilenende, {Zeilennummer: Kopf}, Zeilen in mehrzeiligen Strings). Eine Zeile in einem mehrzeiligen
    String ist kein Kopf und keine Zuweisung, auch wenn sie mit [ oder `enabled =` beginnt."""
    zeilen, nl = zeilen_trennen(text)
    im_string = set()
    for m in TOML_STRINGS.finditer(text):
        if m.group().startswith(('"""', "'''")):
            erste = text.count(nl, 0, m.start()) + 1
            im_string.update(range(erste, erste + m.group().count(nl)))
    koepfe = {i: k for i, z in enumerate(zeilen) if i not in im_string and (k := toml_kopf(z))}
    return zeilen, nl, koepfe, im_string


def toml_text_setzen(text, tabelle, schluessel, wert):
    """Setzt `schluessel = wert` in [tabelle]: vorhandene Zeile ersetzen, sonst in die Tabelle einfügen,
    sonst die Tabelle anhängen. Alles andere bleibt Zeichen für Zeichen stehen."""
    zeilen, nl, koepfe, im_string = toml_zeilen(text)
    ziel = toml_kopf(f"[{tabelle}]")
    start = next((i for i, kopf in koepfe.items() if kopf == ziel), None)
    if start is None:
        rumpf = text if text.endswith(nl) or not text else text + nl
        return f"{rumpf}{nl}[{tabelle}]{nl}{schluessel} = {wert}{nl}"
    ende = next((i for i in koepfe if i > start), len(zeilen))
    muster = re.compile(rf"^\s*{re.escape(schluessel)}\s*=")
    for i in range(start + 1, ende):
        if i not in im_string and muster.match(zeilen[i]):
            zeilen[i] = f"{schluessel} = {wert}"
            break
    else:
        zeilen.insert(start + 1, f"{schluessel} = {wert}")
    return nl.join(zeilen) + nl


def toml_tabelle_entfernen_text(text, pfad):
    """Nimmt die Tabelle mit diesem Kopf samt ihren Zeilen heraus; liefert (neuer Text, ob es sie gab). Kommentare
    direkt vor dem nächsten Kopf gehören zu dem und bleiben stehen."""
    zeilen, nl, koepfe, im_string = toml_zeilen(text)
    start = next((i for i, kopf in koepfe.items() if kopf == (pfad, False)), None)
    if start is None:
        return text, False
    ende = next((i for i in koepfe if i > start), len(zeilen))
    letzte = ende == len(zeilen)
    while ende - 1 > start and ende - 1 not in im_string and zeilen[ende - 1].lstrip().startswith("#"):
        ende -= 1
    del zeilen[start:ende]
    while letzte and zeilen and not zeilen[-1].strip():  # die Leerzeile vor einer letzten Tabelle bliebe sonst am Ende stehen
        zeilen.pop()
    return (nl.join(zeilen) + nl if zeilen else ""), True


def skills_config_setzen(text, ziel, wert):
    """Setzt `enabled = wert` in jedem [[skills.config]]-Block, dessen path auf `ziel` zeigt; fehlt die Zeile, kommt sie
    unter den Kopf. Liefert (neuer Text, ob es einen solchen Block gab)."""
    zeilen, nl, koepfe, im_string = toml_zeilen(text)
    grenzen = [*koepfe, len(zeilen)]
    gefunden = False
    for start, ende in reversed(list(zip(grenzen, grenzen[1:]))):  # von hinten: eingefügte Zeilen verschieben nichts davor
        if koepfe[start] != (["skills", "config"], True):
            continue
        try:
            block = tomllib.loads(nl.join(zeilen[start + 1:ende]))
        except ValueError:
            continue
        if not (isinstance(block.get("path"), str) and Path(block["path"]).expanduser().resolve() == ziel):
            continue
        gefunden = True
        treffer = [i for i in range(start + 1, ende) if i not in im_string and re.match(r"\s*enabled\s*=", zeilen[i])]
        for i in treffer:
            zeilen[i] = f"enabled = {wert}"
        if not treffer and wert == "false":  # ohne enabled-Zeile ist der Block schon an
            zeilen.insert(start + 1, f"enabled = {wert}")
    return nl.join(zeilen) + nl, gefunden


def skill_eintraege(daten, ziel):
    """Die Einträge in skills.config, deren path auf `ziel` zeigt. Codex vergleicht Pfade aufgelöst, also ohne Symlinks."""
    return [e for e in (daten.get("skills") or {}).get("config") or []
            if isinstance(e, dict) and isinstance(e.get("path"), str) and Path(e["path"]).expanduser().resolve() == ziel]


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
    text = text.removeprefix("\ufeff")  # eine BOM vor `policy:` in der ersten Zeile verdeckte den Block
    # Ein Kommentar oder eine Leerzeile auf Spalte 0 beendet den Block nicht, eine andere Zeile schon
    block = re.search(r"^policy:[^\S\n]*(#.*)?\n((?:(?:[ \t]+.*|[ \t]*#.*|[ \t]*)\n?)*)", text, re.M)
    return bool(block) and re.search(r"^\s+allow_implicit_invocation:\s*false\s*(#.*)?$", block.group(2), re.M) is not None


def gueltige_bewertung(kopie):
    """Die Arbeitskopie, wenn sie ein JSON-Objekt ist; sonst None. Ein Symlink zählt nicht: er könnte auf eine Datei außerhalb zeigen."""
    if not kopie.is_file() or kopie.is_symlink():
        return None
    try:
        bewertung = json.loads(kopie.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return bewertung if isinstance(bewertung, dict) else None


def python_aktualisiert():
    """Homebrew löscht beim Python-Update den Ordner der alten Version. Ein Server, der noch mit ihr läuft, findet dann
    Teile der Standardbibliothek nicht mehr, die er erst bei Bedarf lädt, und scheitert mit Fehlern wie
    „LookupError: unknown encoding: utf-8-sig“. `python3` im PATH gibt es weiter, deshalb zählt der Ordner, aus dem
    der Server seine Standardbibliothek geladen hat."""
    return not Path(os.__file__).exists()


def zurueckschreiben(bewertung, ziel):
    # harness.schreiben folgt einem Symlink und nimmt je Aufruf eine eigene Zwischendatei statt <datei>.neu
    harness.schreiben(ziel, json.dumps(bewertung, indent=2, ensure_ascii=False) + "\n")


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
    """Ein Kandidat mit dem, was der Server verantworten kann: geprüfter Befehl, geprüfte Installierbarkeit, Belege
    nur mit passenden Typen."""
    return {**k, "befehl": k.get("befehl") if sicherer_befehl(k.get("befehl")) else "", "installierbar": installierbar(k),
            "belege": belege(k.get("belege"))}


def belege(roh):
    roh = roh if isinstance(roh, dict) else {}
    passt = {"integer": lambda w: isinstance(w, int) and not isinstance(w, bool) and w >= 0,
             "string": lambda w: isinstance(w, str) and len(w) <= 40}
    return {f: roh.get(f) if passt[typ](roh.get(f)) else None for f, typ in BELEGE.items()}


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
                             text=True, errors="replace", start_new_session=True)
    except OSError as fehler:
        return {"ok": False, "ausgabe": str(fehler), "stdout": ""}
    if gestartet:
        gestartet(p)
    frist, abgelaufen, beendet = time.monotonic() + timeout, False, False
    while True:
        try:
            stdout, stderr = p.communicate(timeout=1)
            break
        except subprocess.TimeoutExpired as fehler:
            if p.poll() is None and time.monotonic() < frist:
                continue
            if p.poll() is None:
                abgelaufen = True
                beenden(p)
                p.wait()
            elif beendet:
                # Der Prozess ist beendet, aber ein Enkel in eigener Session (dem beenden() entkommen) hält die Pipe
                # offen. Darauf zu warten hieße, bis der Enkel endet; so lange hielte eine Bewertung die Sperre.
                p.stdout.close()
                p.stderr.close()
                stdout, stderr = (b.decode(errors="replace") if b else "" for b in (fehler.output, fehler.stderr))
                break
            beendet = True  # noch eine Runde, um zu lesen, was schon in der Pipe steht
    if abgelaufen:
        return {"ok": False, "ausgabe": (f"{subprocess.TimeoutExpired(argv, timeout)}\n\n{stdout}{stderr}").strip(), "stdout": ""}
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
                # schluessel: der Name, unter dem Claude Code den Eintrag führt (bei gleichnamigen Plugins ohne Marktplatz)
                return e.get("schluessel", e["name"]), e["aktionen"][aktion]
        for e in inventar.get("berechtigungen", []):  # Regeln haben keinen Namen, nur ihren Text
            if e.get("id") == eid and aktion in e.get("aktionen", {}):
                return e["regel"], e["aktionen"][aktion]
        for e in inventar.get("anweisungen", []):
            if e.get("id") == eid and aktion in e.get("aktionen", {}):
                return e["name"], e["aktionen"][aktion]
        return None

    def sichern(self, datei):
        """Einmal je Datei und Serverlauf eine Sicherung <datei>.bak-<Zeitstempel>, bevor der Server sie zum ersten Mal
        ändert. Mit festem Namen überschrieb der nächste Lauf sie mit dem Stand, den der Server selbst schon geändert hatte."""
        if datei in self.gesichert:
            return
        shutil.copy2(datei, datei.with_name(f"{datei.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))  # übernimmt Rechte und Zeitstempel
        self.gesichert.add(datei)
        # nur die eigenen, am Zeitstempel erkannt; der Name sortiert sie zugleich nach der Zeit
        eigene = sorted(d for d in datei.parent.glob(f"{datei.name}.bak-*") if re.fullmatch(r"\d{8}-\d{6}", d.name.rsplit(".bak-", 1)[1]))
        for alt in eigene[:-SICHERUNGEN]:
            alt.unlink()

    def schreiben(self, datei, text):
        """Atomar über eine eigene Zwischendatei (0600, die echte config.toml kann Zugangsdaten enthalten), folgt einem
        Symlink (Dotfiles-Repo) und behält die Rechte der Datei; siehe harness.schreiben."""
        harness.schreiben(datei, text)

    def settings_aendern(self, aendern):
        text = self.settings.read_text(encoding="utf-8")
        daten = json.loads(text)
        self.sichern(self.settings)
        aendern(daten)
        self.schreiben(self.settings, json.dumps(daten, indent=2, ensure_ascii=False) + "\n")

    def config_lesen(self):
        """config.toml ohne BOM, mit den Zeilenenden der Datei."""
        return text_lesen(self.codex_config).removeprefix("\ufeff")

    def von_hand(self, was, fehler=""):
        return {"ok": False, "ausgabe": f"{was} steht in {self.codex_config} in einer Form, die der Server nicht ändert "
                                        f"(Inline-Tabelle, gepunktete Schlüssel oder Array statt eigenem Kopf). Bitte von Hand ändern. {fehler}".strip()}

    def config_aendern(self, neuer_text, pruefen, was):
        """Schreibt config.toml und nimmt die Änderung zurück, wenn sie die Datei zerstört oder nicht wirkt.
        `was` nennt die Stelle, wenn sich die Datei so nicht ändern lässt."""
        alt = text_lesen(self.codex_config)
        try:
            tomllib.loads(alt.removeprefix("\ufeff"))
        except ValueError as fehler:
            return {"ok": False, "ausgabe": f"{self.codex_config} ist kein gültiges TOML, nicht geändert: {fehler}"}
        try:
            tomllib.loads(neuer_text)
        except ValueError as fehler:
            # Steht die Stelle schon in anderer Form da, kollidiert ein neuer Kopf damit („Cannot declare …“)
            return self.von_hand(was, f"({fehler})")
        self.sichern(self.codex_config)
        # Eine BOM bleibt stehen: Der Server ändert nur den einen Wert, nicht die Kodierung der Datei
        self.schreiben(self.codex_config, ("\ufeff" if alt.startswith("\ufeff") else "") + neuer_text)
        try:
            daten = tomllib.loads(self.config_lesen())
            if not pruefen(daten):
                raise ValueError("Die Einstellung steht nach dem Schreiben nicht in der Datei.")
        except Exception as fehler:  # jeder Fehler nach dem Schreiben muss zurückrollen, nicht nur ValueError/OSError
            self.schreiben(self.codex_config, alt)
            return {"ok": False, "ausgabe": f"{self.codex_config} nicht geändert: {fehler}"}
        return None

    def toml_setzen(self, tabelle, schluessel, wert):
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        alt = self.config_lesen()
        pfad = toml_pfad(tabelle) + [schluessel]

        def pruefen(daten):
            # wert ist ein TOML-Literal; der Vergleich über json.dumps setzt voraus, dass seine JSON- und
            # TOML-Schreibweise übereinstimmen. Gilt für "false" (heute das Einzige, was der Server erzeugt).
            for teil in pfad[:-1]:
                daten = daten.get(teil) if isinstance(daten, dict) else None
                if daten is None:
                    return False
            return json.dumps(daten.get(pfad[-1])) == wert

        fehler = self.config_aendern(toml_text_setzen(alt, tabelle, schluessel, wert), pruefen, f"[{tabelle}]")
        return fehler or {"ok": True, "ausgabe": f"~/.codex/config.toml → [{tabelle}] {schluessel} = {wert}"}

    def skill_abschalten(self, pfad):
        """Stellt vorhandene [[skills.config]]-Blöcke dieses Pfads auf false und hängt nur ohne einen neuen an. Nur
        anhängen hieß: Nach „einschalten“ (der Block bleibt mit true stehen) kam mit jedem „deaktivieren“ einer dazu."""
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        ziel = Path(pfad).expanduser().resolve()
        alt = self.config_lesen()

        def pruefen(daten):
            eintraege = skill_eintraege(daten, ziel)
            return bool(eintraege) and all(e.get("enabled", True) is False for e in eintraege)
        if pruefen(tomllib.loads(alt)):
            return {"ok": True, "ausgabe": f"steht schon in ~/.codex/config.toml: [[skills.config]] path = {json.dumps(pfad)}"}
        neu, gefunden = skills_config_setzen(alt, ziel, "false")
        if not gefunden:
            _, nl = zeilen_trennen(alt)
            rumpf = alt if alt.endswith(nl) or not alt else alt + nl
            neu = f"{rumpf}{nl}[[skills.config]]{nl}path = {json.dumps(pfad, ensure_ascii=False)}{nl}enabled = false{nl}"
        fehler = self.config_aendern(neu, pruefen, "skills.config")
        return fehler or {"ok": True, "ausgabe": f"~/.codex/config.toml → [[skills.config]] path = {json.dumps(pfad)}, enabled = false"}

    def skill_einschalten(self, pfad):
        """Die Gegen-Aktion zu skill_abschalten(): stellt `enabled` in jedem [[skills.config]]-Block dieses Pfads auf true.
        Der Block bleibt stehen, damit die Datei sonst Zeichen für Zeichen gleich bleibt."""
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        ziel = Path(pfad).expanduser().resolve()
        schaltet_ab = lambda daten: any(e.get("enabled", True) is False for e in skill_eintraege(daten, ziel))
        alt = self.config_lesen()
        if not schaltet_ab(tomllib.loads(alt)):
            return {"ok": False, "ausgabe": f"Kein [[skills.config]]-Block schaltet {pfad} ab"}
        neu, gefunden = skills_config_setzen(alt, ziel, "true")
        if not gefunden:  # der Eintrag steht in einem Array skills.config = [...], nicht als eigener Block
            return self.von_hand("skills.config")
        fehler = self.config_aendern(neu, lambda daten: not schaltet_ab(daten), "skills.config")
        return fehler or {"ok": True, "ausgabe": f"~/.codex/config.toml → [[skills.config]] path = {json.dumps(str(pfad))}, enabled = true"}

    def nur_manuell_datei(self, ordner, manuell=True):
        """agents/openai.yaml im Skill-Ordner: policy.allow_implicit_invocation = false, damit Codex den Skill
        nur noch per $name lädt. Ändert nur innerhalb des policy:-Blocks (Einrückung 0 bis zur nächsten nicht
        leeren Zeile mit Einrückung 0) und übernimmt dessen Einrückung; alles andere bleibt erhalten. Schreibt
        bei fehlgeschlagener Prüfung den ursprünglichen Stand zurück, wie config_aendern es für config.toml tut.
        `manuell=False` ist die Gegen-Aktion „wieder automatisch“ und stellt denselben Schlüssel auf true."""
        if not any((Path(ordner) / f).is_file() for f in ("SKILL.md", "agents/openai.yaml")):
            return {"ok": False, "ausgabe": f"{ordner} enthält weder SKILL.md noch agents/openai.yaml"}
        datei = Path(ordner) / "agents" / "openai.yaml"
        alt = text_lesen(datei) if datei.is_file() else None
        wert = "false" if manuell else "true"
        if not manuell and not (alt is not None and nur_manuell_gesetzt(alt)):
            return {"ok": False, "ausgabe": f"{datei} stellt den Skill nicht auf nur manuell"}
        if alt is not None:
            bom = "\ufeff" if alt.startswith("\ufeff") else ""  # sonst stünde sie vor `policy:`, und der Block fehlte
            zeilen, nl = zeilen_trennen(alt.removeprefix(bom))
            start = next((i for i, z in enumerate(zeilen) if re.match(r"^policy:\s*", z)), None)
            if start is not None and not re.fullmatch(r"policy:\s*(#.*)?", zeilen[start]):
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
                        zeilen[i] = f"{einrueckung}allow_implicit_invocation: {wert}"
                        break
                else:
                    folge = next((z for z in zeilen[start + 1:ende] if z.strip()), None)
                    einrueckung = folge[:len(folge) - len(folge.lstrip())] if folge else "  "
                    zeilen.insert(start + 1, f"{einrueckung}allow_implicit_invocation: false")
            text = bom + nl.join(zeilen) + nl
            self.sichern(datei)
        else:
            datei.parent.mkdir(parents=True, exist_ok=True)
            text = "policy:\n  allow_implicit_invocation: false\n"
        self.schreiben(datei, text)
        if nur_manuell_gesetzt(text_lesen(datei)) != manuell:
            if alt is not None:
                self.schreiben(datei, alt)
            else:
                datei.unlink()
            return {"ok": False, "ausgabe": f"{datei} nicht geändert"}
        return {"ok": True, "ausgabe": f"{datei} → policy: allow_implicit_invocation: {wert}"}

    def override_setzen(self, name, wert):
        """`wert` None nimmt den Override weg: die Gegen-Aktion zu „deaktivieren“ und „nur manuell“."""
        if wert is None:
            return self.settings_eintrag_entfernen("skillOverrides", name)

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

    def settings_eintrag_entfernen(self, feld, eintrag):
        """Nimmt einen Schlüssel aus skillOverrides oder einen Eintrag aus deniedMcpServers oder claudeMdExcludes;
        ein leeres Feld bleibt nicht stehen."""
        vorhanden = json.loads(self.settings.read_text(encoding="utf-8")).get(feld)
        if not isinstance(vorhanden, (dict, list)) or eintrag not in vorhanden:
            return {"ok": False, "ausgabe": f"{json.dumps(eintrag, ensure_ascii=False)} steht nicht in {feld}"}  # ohne die Datei anzufassen

        def aendern(daten):
            if isinstance(daten[feld], dict):
                del daten[feld][eintrag]
            else:
                daten[feld].remove(eintrag)
            if not daten[feld]:
                del daten[feld]
        self.settings_aendern(aendern)
        return {"ok": True, "ausgabe": f"{feld}: {json.dumps(eintrag, ensure_ascii=False)} entfernt"}

    def ausschliessen(self, pfad):
        """Trägt eine CLAUDE.md, Regel oder AGENTS.md in `claudeMdExcludes` ein: Claude Code lädt sie dann nicht mehr,
        die Datei selbst bleibt, wie sie ist. Nur ein ganzer Pfad zu einer .md-Datei: Der Wert kommt aus dem Inventar,
        und Claude Code liest den Eintrag als Muster — mit einem * träfe er mehr als die eine Datei."""
        if not (isinstance(pfad, str) and pfad.startswith("/") and pfad.endswith(".md") and not set(pfad) & set("*?[]{}")):
            return {"ok": False, "ausgabe": "Das ist kein Pfad zu einer Anweisungsdatei."}

        def aendern(daten):
            liste = daten.setdefault("claudeMdExcludes", [])
            if pfad not in liste:
                liste.append(pfad)
        self.settings_aendern(aendern)
        return {"ok": True, "ausgabe": f"claudeMdExcludes: {json.dumps(pfad, ensure_ascii=False)}"}

    def output_style(self, name):
        """Wählt einen Output-Style in ~/.claude/settings.json oder nimmt die Wahl weg (`name` None): Dann gilt der Standard."""
        if name is None:
            if "outputStyle" not in json.loads(self.settings.read_text(encoding="utf-8")):
                return {"ok": False, "ausgabe": f"outputStyle steht nicht in {self.settings}"}  # ohne die Datei anzufassen
            self.settings_aendern(lambda daten: daten.pop("outputStyle"))
            return {"ok": True, "ausgabe": "outputStyle entfernt"}
        if not isinstance(name, str) or not name:
            return {"ok": False, "ausgabe": "Das ist kein Name eines Output-Styles."}
        self.settings_aendern(lambda daten: daten.update(outputStyle=name))
        return {"ok": True, "ausgabe": f"outputStyle: {json.dumps(name, ensure_ascii=False)}"}

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

    def regel_entfernen(self, datei, art, regel):
        """Nimmt eine Regel aus `permissions` einer Einstellungsdatei, auch in einem Projektordner; zurück führt die
        Sicherung. Nur Einstellungsdateien von Claude Code: Der Pfad kommt aus dem Inventar, einer Datei, die auch
        andere schreiben können."""
        datei = Path(datei)
        if datei.parent.name != ".claude" or datei.name not in ("settings.json", "settings.local.json"):
            return {"ok": False, "ausgabe": f"{datei} ist keine Einstellungsdatei von Claude Code"}
        daten = json.loads(datei.read_text(encoding="utf-8"))
        rechte = daten.get("permissions") if isinstance(daten, dict) else None
        regeln = rechte.get(art) if isinstance(rechte, dict) else None
        if not isinstance(regeln, list) or regel not in regeln:
            return {"ok": False, "ausgabe": f"{json.dumps(regel, ensure_ascii=False)} steht nicht in {datei} ({art})"}  # ohne die Datei anzufassen
        self.sichern(datei)
        rechte[art] = [r for r in regeln if r != regel]
        if not rechte[art]:
            del rechte[art]
        if not rechte:
            del daten["permissions"]
        self.schreiben(datei, json.dumps(daten, indent=2, ensure_ascii=False) + "\n")
        return {"ok": True, "ausgabe": f"{datei} → {art}: {json.dumps(regel, ensure_ascii=False)} entfernt"}

    def toml_tabelle_entfernen(self, pfad):
        """Nimmt einen vertrauten Ordner ([projects."<Pfad>"]) aus config.toml."""
        if not (isinstance(pfad, list) and len(pfad) == 2 and pfad[0] == "projects" and isinstance(pfad[1], str)):
            return {"ok": False, "ausgabe": "Diese Tabelle entfernt der Server nicht."}
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        vorhanden = lambda daten: isinstance(daten.get(pfad[0]), dict) and pfad[1] in daten[pfad[0]]
        alt = self.config_lesen()
        was = f'{pfad[0]}."{pfad[1]}"'
        neu, gefunden = toml_tabelle_entfernen_text(alt, pfad)
        if not gefunden:
            return self.von_hand(was) if vorhanden(tomllib.loads(alt)) else {"ok": False, "ausgabe": f"{was} steht nicht in {self.codex_config}"}
        fehler = self.config_aendern(neu, lambda daten: not vorhanden(daten), was)
        return fehler or {"ok": True, "ausgabe": f"{self.codex_config} → [{was}] entfernt"}

    def ausfuehren(self, name, schritt):
        with self.sperre:
            if "regel_entfernen" in schritt:
                return self.regel_entfernen(**schritt["regel_entfernen"])
            if "toml_tabelle_entfernen" in schritt:
                return self.toml_tabelle_entfernen(schritt["toml_tabelle_entfernen"])
            if "override" in schritt:
                return self.override_setzen(name, schritt["override"])
            if "sperren" in schritt:
                return self.mcp_sperren(schritt["sperren"])
            if "entsperren" in schritt:
                return self.settings_eintrag_entfernen("deniedMcpServers", schritt["entsperren"])
            if "toml" in schritt:
                return self.toml_setzen(**schritt["toml"])
            if "skill_aus" in schritt:
                return self.skill_abschalten(schritt["skill_aus"])
            if "skill_an" in schritt:
                return self.skill_einschalten(schritt["skill_an"])
            if "nur_manuell" in schritt:
                return self.nur_manuell_datei(schritt["nur_manuell"])
            if "automatisch" in schritt:
                return self.nur_manuell_datei(schritt["automatisch"], manuell=False)
            if "papierkorb" in schritt:
                return in_den_papierkorb(schritt["papierkorb"])
            if "hook_entfernen" in schritt:
                return self.hook_entfernen(schritt["hook_entfernen"])
            if "ausschliessen" in schritt:
                return self.ausschliessen(schritt["ausschliessen"])
            if "einschliessen" in schritt:
                return self.settings_eintrag_entfernen("claudeMdExcludes", schritt["einschliessen"])
            if "output_style" in schritt:
                return self.output_style(schritt["output_style"])
            if name.startswith("-") and any(str(a).startswith(name) for a in schritt["argv"][1:]):
                return {"ok": False, "ausgabe": f"„{name}“ beginnt mit einem Minus: Der Befehl läse den Namen als Option. "
                                                "Bitte von Hand entfernen."}
            if schritt["argv"][:1] == ["npx"]:
                # Im Projektordner nähme npx sonst ein node_modules/.bin des Projekts und dessen .npmrc (nachgemessen).
                # Mit leerem --prefix nicht; der Befehl selbst läuft weiter im Projektordner.
                with tempfile.TemporaryDirectory() as leer:
                    return lauf(["npx", "--prefix", leer, *schritt["argv"][1:]], schritt.get("cwd"), 120)
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
            plugin = skill_plugin(self.ordner / "skill", arbeitsordner)
            c = self.langer_lauf("bewertung", bewerten_argv(arbeitsordner, self.daten, einstellungen, r["ausgabe"], plugin), arbeitsordner)
            # nur stdout: eine geschweifte Klammer in einer Warnung auf stderr zerrisse den JSON-Ausschnitt
            text = claude_text(c["stdout"]) if claude_json(c["stdout"]) else c["ausgabe"]
            if not c["ok"]:
                return {"ok": False, "ausgabe": text}
            bewertung = gueltige_bewertung(kopie)
            if bewertung is None:
                return {"ok": False, "ausgabe": "Der Lauf hat keine gültige bewertung-claude-code.json hinterlassen: " + text}
            with self.sperre:
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
            with self.sperre:
                zurueckschreiben(bewertung, bewertung_datei)
                n = lauf([sys.executable, str(self.ordner / "build.py")], self.ordner, 300)
        return {"ok": n["ok"], "ausgabe": text if n["ok"] else text + "\n\nNeu bauen fehlgeschlagen:\n\n" + n["ausgabe"]}

    def suchen(self, bedarf):
        self.kandidaten = []
        argv = ["claude", "-p", f"{SKILL_AUFRUF} suche {bedarf}", "--model", "opus", "--output-format", "json",
                "--json-schema", json.dumps(SUCHEN_SCHEMA), "--tools", "Read,Glob,Grep,WebSearch,WebFetch,Bash",
                "--allowedTools", *SUCHEN_WERKZEUGE, *CLAUDE_GRENZEN, "--append-system-prompt", SUCHEN_HINWEIS]
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
            plugin = skill_plugin(self.ordner / "skill", tmp)
            r = self.langer_lauf("suche", [*argv, "--settings", str(einstellungen), "--plugin-dir", str(plugin)], tmp)
        ergebnis = (claude_json(r["stdout"]) or {}).get("structured_output")
        if not r["ok"] or not isinstance(ergebnis, dict):
            return {"ok": False, "ausgabe": claude_text(r["stdout"]) if claude_json(r["stdout"]) else r["ausgabe"]}
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
        """Ein Lauf dauert bis zu 20 Minuten; ohne Abbruch half nur, den Server zu beenden."""
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
            # die Kopie unter der Sperre: abbrechen() fügt „abgebrochen“ hinzu, das Kopieren bräche sonst ab
            if not lauf_:
                return {"laeuft": False}
            if lauf_["laeuft"]:
                return {**{k: v for k, v in lauf_.items() if k != "beginn"}, "sekunden": round(time.time() - lauf_["beginn"])}
            return dict(lauf_)

    def installieren(self, k, nur_manuell):
        """`k` ist der Kandidat selbst, nicht seine Nummer: Zwischen Prüfung und Zugriff konnte eine neue Suche
        die Liste austauschen, und der Zugriff lief ins Leere."""
        codex = self.harness() == "codex"
        if k["art"] == "skill":
            argv = ["npx", "-y", harness.SKILLS_CLI, "add", k["repo"], "--skill", k["skill"], "-g", "-y",
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
        datei = self.server.datei("dashboard")
        try:
            html = datei.read_text(encoding="utf-8")
        except OSError as fehler:
            return self.antworte(500, f"{datei.name} fehlt oder ist nicht lesbar ({fehler}). Beim ersten Lauf oder nach einem "
                                      "abgebrochenen build.py: im Projektordner python3 build.py ausführen, dann neu laden.", "text/plain")
        html = html.replace(TOKEN_PLATZHALTER, TOKEN_PLATZHALTER.replace("null", json.dumps(self.server.token)), 1)
        self.antworte(200, html, "text/html")

    def do_POST(self):
        token = self.headers.get("X-Token", "").encode()
        if self.headers.get("Host") not in self.server.hosts or not secrets.compare_digest(token, self.server.token.encode()):
            return self.antworte(403, {"ok": False, "ausgabe": "nicht erlaubt"})
        if python_aktualisiert():
            return self.antworte(200, {"ok": False, "ausgabe": f"Python wurde aktualisiert, während der Server lief: {Path(os.__file__).parent} "
                                                               "gibt es nicht mehr. Server beenden (Ctrl+C) und mit `python3 server.py` neu starten."})
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
        except OSError as fehler:
            return self.antworte(200, {"ok": False, "ausgabe": f"Das Inventar fehlt oder ist nicht lesbar ({fehler}). "
                                                               "Im Projektordner python3 build.py ausführen, dann neu laden."})
        except (ValueError, KeyError, TypeError):
            treffer = None
        if not treffer:
            return self.antworte(400, {"ok": False, "ausgabe": "Diese Aktion bietet der Eintrag nicht an."})
        try:
            ergebnis = self.server.ausfuehren(*treffer)
        except (OSError, ValueError, AttributeError, TypeError, KeyError) as fehler:  # KeyError: ein Schritt, den dieser Server nicht kennt
            ergebnis = {"ok": False, "ausgabe": f"{type(fehler).__name__}: {fehler}"}
        self.antworte(200, ergebnis)

    def log_message(self, *args):
        pass


def startdatei(daten, url):
    """Eine Seite, die zum Dashboard weiterleitet, lesbar nur für den Benutzer. Der Browser bekommt sie statt der
    Adresse: Die trägt den Schlüssel, und Argumente eines Befehls sieht jeder Benutzer des Rechners in `ps`.
    An ihr erkennt auch der Skill den laufenden Server — per curl ginge das unter Codex nicht, dessen Sandbox sperrt
    das Netz auch zum eigenen Rechner. Bleibt sie nach einem harten Ende liegen, fragt sie selbst nach einem
    Lebenszeichen (ohne Schlüssel: 403 ist auch eine Antwort) und sagt sonst, wie es weitergeht."""
    datei = Path(daten) / "start.html"
    teile = urllib.parse.urlsplit(url)
    lebenszeichen = f"{teile.scheme}://{teile.netloc}/"
    dateien = " oder ".join(f'<a href="{h["dashboard"]}">{h["name"]}</a>' for h in harness.HARNESSE.values())
    fd = os.open(datei, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><meta charset="utf-8"><title>3zero Harness-Inventur</title>
<noscript><meta http-equiv="refresh" content="0; url={url}"></noscript>
<p id="lage">Öffne das Dashboard …</p>
<p id="tot" hidden>Der Server läuft nicht mehr, diese Startdatei ist liegen geblieben. Starte ihn mit <code>python3 server.py</code>
im Projektordner, oder öffne das Dashboard nur zum Lesen: {dateien}.</p>
<script>
fetch({json.dumps(lebenszeichen)}, {{ mode: "no-cors" }}).then(() => location.replace({json.dumps(url)}), () => {{
  document.getElementById("lage").hidden = true;
  document.getElementById("tot").hidden = false;
}});
</script>
""")
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


def bedienen(srv, start):
    """Läuft, bis jemand den Server beendet, und räumt dann auf. Nicht nur bei Ctrl+C: kill (SIGTERM) und ein
    geschlossenes Terminal (SIGHUP) beendeten ihn sonst sofort, und start.html blieb liegen — an ihr erkennt der
    Skill aber, dass der Server läuft."""
    def aufhoeren(*_):
        raise KeyboardInterrupt
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, aufhoeren)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        start.unlink(missing_ok=True)  # der Schlüssel gilt nur für diesen Lauf
        with srv.laeufe_sperre:
            for prozess in srv.prozesse.values():  # eigene Prozessgruppe: Ctrl+C im Terminal erreicht sie nicht mehr
                if prozess.poll() is None:  # die Gruppen-ID eines beendeten kann schon einem fremden Prozess gehören
                    beenden(prozess)


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
    bedienen(srv, start)


if __name__ == "__main__":
    main()
