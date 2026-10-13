"""Was beide Leser des Bestands brauchen: claude.py für Claude Code und codex.py für Codex.

Lesen ohne Absturz, IDs, Hooks, Ziele von MCP-Servern ohne Zugangsdaten, Wörter für Befehle zum Kopieren,
der Update-Vergleich für Skills aus `npx skills` und das Anhängen der Bewertung. Nichts hier weiß, welcher
Harness gerade gelesen wird: Was sich unterscheidet, geben die Leser als Argument mit.
"""

import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import mcp_direkt


def lies_json(pfad):
    """Nur ein JSON-Objekt, sonst {}: Alle Aufrufer lesen mit .get weiter, auch in Dateien, die andere geschrieben haben."""
    try:
        daten = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return daten if isinstance(daten, dict) else {}


HOOK_TAGE = 14  # so weit zurück zählen Transkripte und Sitzungen: Hook-Ausgaben, MCP-Messung und der Tab „Modelle“


def zeitgrenze():
    """Ab wann ein Ereignis zählt, als ISO-Zeitstempel in UTC wie die Transkripte. Die Dateiauswahl in
    `transkripte()` reicht nicht: Wird eine alte Sitzung fortgesetzt, ist ihre Datei frisch, ihre Ereignisse nicht.
    Steht hier und nicht in claude.py, weil build.py das Fenster auch für die Modelle unter Codex braucht."""
    return (datetime.now(timezone.utc) - timedelta(days=HOOK_TAGE)).strftime("%Y-%m-%dT%H:%M:%S")


# Unerwartete Fehler je Plugin oder Projekt: Sie kosten nur diese Quelle und stehen im Dashboard. Ein Absturz
# bräche build.py ab, und ohne gelungenen Lauf startet der Server nicht.
LESEFEHLER = []


def lesefehler(quelle, fehler):
    LESEFEHLER.append({"quelle": quelle, "fehler": f"{type(fehler).__name__}: {fehler}"})


def frontmatter(pfad):
    """Liest den YAML-Kopf einer Markdown-Datei (einfache Teilmenge, ohne PyYAML)."""
    try:
        # utf-8-sig: sonst steht ein BOM vor dem ---; replace: eine Datei in Latin-1 behält ihre Beschreibung
        text = Path(pfad).read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    daten, schluessel = {}, None
    for zeile in m.group(1).splitlines():
        if zeile[:1] in (" ", "\t") and schluessel:
            inhalt = zeile.strip()
            if isinstance(daten[schluessel], dict):
                k, _, v = inhalt.partition(":")
                daten[schluessel][k.strip()] = v.strip().strip("\"'")
            else:
                daten[schluessel] = (daten[schluessel] + " " + inhalt).strip()
            continue
        k, sep, v = zeile.partition(":")
        if not sep:
            continue
        schluessel, v = k.strip(), v.strip()
        if v in (">", ">-", "|", "|-"):
            daten[schluessel] = ""
        elif v == "":
            daten[schluessel] = {}
        else:
            daten[schluessel] = v.strip("\"'")
    return daten


def github_baum(repo):
    """Ordnerstände eines GitHub-Repos auf dem Standardzweig: Pfad → Git-SHA, "" für die Wurzel. None ohne gh oder Netz."""
    try:
        r = subprocess.run(["gh", "api", f"repos/{repo}/git/trees/HEAD?recursive=1"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        daten = json.loads(r.stdout)
        return {"": daten["sha"], **{t["path"]: t["sha"] for t in daten["tree"] if t["type"] == "tree"}}
    except (ValueError, KeyError, TypeError):  # eine unerwartete Antwort kostet nur die Update-Angabe, nicht den ganzen Lauf
        return None


def updates_markieren(skills, lock):
    """Setzt `update` bei per npx skills installierten Skills: True, wenn ihr Ordner auf GitHub einen anderen Stand hat
    als bei der Installation (skillFolderHash ist die Git-SHA des Ordners). Ohne Antwort von GitHub bleibt das Feld weg."""
    cli = [(e, lock[e["name"]]) for e in skills if not e.get("plugin") and e["quelle"]["art"] == "skills-CLI" and e["name"] in lock]
    # ohne Repo, Pfad oder Stand der Installation gibt es nichts zu vergleichen
    cli = [(e, l) for e, l in cli if all(isinstance(l.get(f), str) for f in ("source", "skillPath", "skillFolderHash"))]
    repos = sorted({l["source"] for _, l in cli})
    with ThreadPoolExecutor() as pool:
        baeume = dict(zip(repos, pool.map(github_baum, repos)))
    for e, l in cli:
        stand = (baeume[l["source"]] or {}).get(l["skillPath"].rpartition("/")[0])
        if stand:
            e["update"] = stand != l["skillFolderHash"]


def projekt_ids(eintraege, praefix):
    """Setzt die IDs von Projekt-Einträgen: <praefix>:<Ordnername>/<Name>. Haben zwei Projekte mit demselben Ordnernamen
    Einträge, trägt die ID stattdessen den ganzen Pfad — sonst teilten sie sich eine und eine Aktion träfe das erste."""
    pfade = {}
    for e in eintraege:
        pfade.setdefault(Path(e["projekt"]).name, set()).add(e["projekt"])
    for e in eintraege:
        ordner = Path(e["projekt"]).name
        e["id"] = f"{praefix}:{e['projekt'] if len(pfade[ordner]) > 1 else ordner}/{e['name']}"


def hook_eintraege(roh, schluessel, quelle, pfad, status="aktiv"):
    """Ein Eintrag je Ereignis aus einem hooks-Block (settings.json oder hooks/hooks.json), mit allen Befehlen darunter."""
    ergebnis = []
    for ereignis, gruppen in sorted((roh if isinstance(roh, dict) else {}).items()):
        if not isinstance(gruppen, list):  # fremde hooks.json: was nicht die erwartete Form hat, fällt weg
            continue
        befehle = [{"befehl": h.get("command") or h.get("prompt") or h.get("url") or h.get("type"),
                    "matcher": g.get("matcher"), "bedingung": h.get("if")}
                   for g in gruppen if isinstance(g, dict) and isinstance(g.get("hooks"), list)
                   for h in g["hooks"] if isinstance(h, dict)]
        ergebnis.append({"id": f"hook:{schluessel}:{ereignis}", "name": ereignis, "typ": "Hook", "ereignis": ereignis,
                         "status": status, "befehle": befehle, "quelle": dict(quelle), "pfad": str(pfad),
                         "tokens": 0, "ausgabe": None, "dauer": None})
    return ergebnis


def werkzeug_praefix(name):
    """Im Transkript heißen MCP-Werkzeuge `mcp__<Server>__<Werkzeug>`, mit ersetzten Sonderzeichen."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def mehrfache_namen(pids):
    """Plugin-Namen, die es in mehreren Marktplätzen gibt. Ihre Bestandteile brauchen den Marktplatz im Namen,
    sonst tragen zwei Skills dieselbe ID und eine Aktion träfe das falsche Plugin."""
    namen = [str(pid).partition("@")[0] for pid in pids]
    return {n for n in namen if namen.count(n) > 1}


GEHEIM_NAME = r"[\w-]*(?:token|key|secret|password|passwd|auth)[\w-]*"

GEHEIM = [
    (re.compile(r"(\w+://)[^/@\s]+@"), r"\1…@"),  # nutzer:passwort@ in einer URL
    (re.compile(r"([?&][^=&#\s]+=)[^&#\s]+"), r"\1…"),  # Werte in der Query
    (re.compile(rf"((?:^|\s)--?{GEHEIM_NAME}[= ])(?!-)\S+", re.I), r"\1…"),  # --api-key X, --token=X
    (re.compile(rf"((?:^|\s){GEHEIM_NAME}=)\S+", re.I), r"\1…"),  # GITHUB_TOKEN=X
    (re.compile(r"(Bearer\s+)\S+", re.I), r"\1…"),
]


def verdecken(text):
    """Zugangsdaten in URL und Argumenten eines MCP-Servers durch … ersetzen: Das Ziel steht im Inventar, im Dashboard
    und im Kontext des Bewertungslaufs."""
    for muster, ersatz in GEHEIM:
        text = muster.sub(ersatz, text)
    return text


def ziel_text(c):
    if c.get("url"):
        return verdecken(c["url"])
    return verdecken(" ".join([c.get("command") or ""] + [str(a) for a in c.get("args") or []]).strip())


LOKAL = {"localhost", "127.0.0.1", "::1"}


def netz(art, c, status, messung, anmeldung):
    """Betreiber und Anmeldung eines MCP-Servers im Netz; None für Server, die per Befehl oder auf diesem Rechner laufen.
    Die Domain stammt aus der Konfiguration, nie aus einer Antwort des Servers. `anmeldung` ist die Antwort auf die
    direkte Frage (mcp_direkt): verlangt der Server eine, weiß das nur er."""
    if art == "claude.ai-Connector":
        return {"domain": "claude.ai", "anmeldung": "über dein claude.ai-Konto"}
    url = (c or {}).get("url")
    try:
        host = urlparse(url).hostname if isinstance(url, str) else None
    except ValueError:
        host = None
    if not host or host in LOKAL:
        return None
    if "oauth" in c:
        weg = "OAuth"
    elif any(k in c for k in mcp_direkt.ZUGANG):
        weg = "Header oder Token"
    elif anmeldung:
        weg = anmeldung
    elif messung == "direkt":
        weg = "keine"
    elif status == "Anmeldung nötig":
        weg = "nötig, nicht angemeldet"
    else:
        weg = "unbekannt"
    return {"domain": host, "anmeldung": weg}


SICHER = re.compile(r"[\w@%+=:,./-]+")


def shellwort(wert):
    """Ein Wort für einen Befehl, den der Benutzer ins Terminal einfügt. Einfache Anführungszeichen, weil in
    doppelten `, $ und \\ wirksam blieben: Ein Projektpfad mit $(…) führte sonst etwas anderes aus, als dasteht."""
    wert = str(wert)
    return wert if SICHER.fullmatch(wert) else "'" + wert.replace("'", "'\\''") + "'"


def papierkorb_befehl(ziel):
    """Der Befehl zum Kopieren; ~/.Trash gibt es nur auf dem Mac. Im Dashboard verschiebt der Server selbst (Schritt `papierkorb`)."""
    return f"mv {ziel} ~/.Trash/" if sys.platform == "darwin" else f"gio trash {ziel}"


def bewerte(eintraege, bewertung, befehl_fuer):
    """Hängt Bewertung und Umsetzungsbefehl an: `bewertung` ist die Datei des Harness, `befehl_fuer` sein befehl()."""
    bewertungen = bewertung.get("eintraege", {})
    for e in eintraege:
        b = bewertungen.get(e["id"])
        e["bewertung"] = b
        if b and e.get("quelle") and isinstance(e["quelle"], dict):
            for feld in ("autor", "repo", "url"):
                if b.get(feld):
                    e["quelle"][feld] = b[feld]
        # nie aus der Bewertung: Sie stammt aus einem Lauf, der fremde Skill-Texte liest, und der Befehl steht neben dem Kopierknopf
        e["befehl"] = befehl_fuer(e, (b or {}).get("empfehlung"))


def abgleich(alle, bewertung):
    """(unbewertet, entfallen): IDs ohne Bewertung und bewertete IDs, die nicht mehr installiert sind."""
    ids = {e["id"] for e in alle}
    return [e["id"] for e in alle if not e["bewertung"]], sorted(set(bewertung.get("eintraege", {})) - ids)
