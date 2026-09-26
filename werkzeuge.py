"""Werkzeuge, die die Inventur, die MCP-Server und die Hooks brauchen: installierte und neueste Version,
Pflegeende und der Befehl zum Aktualisieren.

Nur zur Anzeige: Das Dashboard aktualisiert nichts selbst, schon gar nicht die feste Version von skills
(harness.SKILLS_CLI) — die soll erst geprüft werden. Die neuesten Versionen fragt es einmal am Tag im Netz ab
und merkt sie sich in werkzeuge.json im Datenordner; ohne Netz gilt der letzte Stand.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.request
from pathlib import Path

import harness

# Name im PATH → (Anzeigename, Quelle der neuesten Version)
WERKZEUGE = {
    "python3": ("Python", "eol:python"),
    "node": ("Node.js", "eol:nodejs"),
    "bun": ("Bun", "eol:bun"),
    "uv": ("uv", "github:astral-sh/uv"),
    "deno": ("Deno", "github:denoland/deno"),
    "docker": ("Docker", None),
    "gh": ("GitHub CLI", "github:cli/cli"),
    "claude": ("Claude Code", "npm:@anthropic-ai/claude-code"),
    "codex": ("Codex", "npm:@openai/codex"),
}
# Aufrufname → Werkzeug: npx kommt mit Node, bunx mit Bun, uvx mit uv
ALIAS = {"python": "python3", "npx": "node", "npm": "node", "bunx": "bun", "uvx": "uv"}
# Was die Inventur selbst braucht, unabhängig von MCP-Servern und Hooks
GRUNDBEDARF = {"python3": "Inventur (build.py, server.py)", "gh": "Update-Prüfung der Skills", "node": "npx für Skill-Aktionen"}


def version_aus(text):
    m = re.search(r"\d+(?:\.\d+)+", text or "")
    return m.group() if m else None


def zahlen(version):
    return tuple(int(x) for x in version.split("."))


def bewerten(installiert, quelle, heute):
    """`quelle` ist die neueste Version als Text, die Linien von endoflife.date (neueste zuerst) oder None.
    Veraltet heißt: In der eigenen Linie gibt es Neueres. Eine neuere Linie allein macht nichts veraltet."""
    ergebnis = {"status": "unbekannt", "neueste": None, "pflege_bis": None, "neue_linie": None}
    if isinstance(quelle, str):
        ergebnis["neueste"] = quelle
    elif quelle:
        passend = [l for l in quelle if installiert and (installiert == l["name"] or installiert.startswith(l["name"] + "."))]
        linie = max(passend, key=lambda l: len(l["name"]), default=None)
        if linie:
            ergebnis.update(neueste=linie["latest"], pflege_bis=linie["eol"],
                            neue_linie=quelle[0]["latest"] if quelle[0] is not linie else None)
    if installiert is None:
        return {**ergebnis, "status": "fehlt"}
    if ergebnis["pflege_bis"] and ergebnis["pflege_bis"] <= heute.isoformat():
        return {**ergebnis, "status": "ohne Pflege"}
    if ergebnis["neueste"]:
        ergebnis["status"] = "veraltet" if zahlen(installiert) < zahlen(ergebnis["neueste"]) else "aktuell"
    return ergebnis


def werkzeuge_in(befehl):
    """Welche Werkzeuge ein Befehl startet. Ein Pfad zählt nur, wenn er auf dasselbe Programm zeigt wie der Name im PATH:
    Codex bringt ein eigenes Node mit, dessen Pflege Sache der App ist. Ein .py-Argument braucht Python, auch hinter bash."""
    try:
        teile = shlex.split(befehl)
    except ValueError:
        teile = befehl.split()
    if not teile or "://" in befehl:
        return []
    name = os.path.basename(teile[0])
    name = ALIAS.get(name, name)
    gefunden = []
    if name in WERKZEUGE:
        im_pfad = shutil.which(name)
        if "/" not in teile[0] or (im_pfad and os.path.realpath(im_pfad) == os.path.realpath(teile[0])):
            gefunden.append(name)
    if any(t.endswith(".py") for t in teile[1:]) and "python3" not in gefunden:
        gefunden.append("python3")
    return gefunden


def gebraucht(inhalt):
    """Werkzeug → wer es braucht, aus den Befehlen der MCP-Server und Hooks."""
    bedarf = {}

    def eintragen(befehl, wer):
        for w in werkzeuge_in(befehl or ""):
            if wer not in bedarf.setdefault(w, []):
                bedarf[w].append(wer)
    for m in inhalt.get("mcp", []) + inhalt.get("projekt_mcp", []):
        eintragen(m.get("ziel"), f"MCP-Server {m['name']}" + (f" ({Path(m['projekt']).name})" if m.get("projekt") else ""))
    for h in inhalt.get("hooks", []):
        for b in h.get("befehle", []):
            eintragen(b.get("befehl"), f"Hook {h['name']}")
    return bedarf


def update_befehl(pfad):
    """Der Befehl zum Aktualisieren, abgelesen am echten Pfad des Programms; None, wenn die Installationsart unklar ist."""
    if m := re.search(r"/Cellar/([^/]+)/", pfad):
        return f"brew upgrade {m.group(1)}"
    if m := re.search(r"/lib/node_modules/((?:@[^/]+/)?[^/]+)/", pfad):
        return f"npm install -g {m.group(1)}@latest"
    if "/.bun/" in pfad:
        return "bun upgrade"
    if "/.local/share/claude/" in pfad:
        return "claude update"
    return None


def installiert(werkzeug):
    """(Version, echter Pfad) oder (None, None)."""
    pfad = shutil.which(werkzeug)
    if not pfad:
        return None, None
    try:
        r = subprocess.run([pfad, "--version"], capture_output=True, text=True, errors="replace", timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None, os.path.realpath(pfad)
    return version_aus(r.stdout + r.stderr), os.path.realpath(pfad)


def abrufen(url):
    try:
        anfrage = urllib.request.Request(url, headers={"User-Agent": "3zero-harness-inventur", "Accept": "application/json"})
        with urllib.request.urlopen(anfrage, timeout=5) as antwort:
            return json.load(antwort)
    except Exception:  # Netz, Zeitgrenze, kein JSON: dann fehlt nur diese Angabe
        return None


def neueste(quelle):
    art, _, was = (quelle or "").partition(":")
    try:
        if art == "eol":
            daten = abrufen(f"https://endoflife.date/api/v1/products/{was}/")
            return [{"name": r["name"], "latest": (r.get("latest") or {}).get("name"), "eol": r.get("eolFrom")}
                    for r in daten["result"]["releases"]]
        if art == "npm":
            return abrufen(f"https://registry.npmjs.org/{was}/latest")["version"]
        if art == "github":
            return version_aus(abrufen(f"https://api.github.com/repos/{was}/releases/latest")["tag_name"])
    except (TypeError, KeyError):
        return None
    return None


def pruefen(inhalt, harness_id, daten, heute):
    datei = Path(daten) / "werkzeuge.json"
    try:
        gemerkt = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        gemerkt = {}
    gemerkt = gemerkt if isinstance(gemerkt, dict) else {}
    stand = gemerkt.get("neueste") if isinstance(gemerkt.get("neueste"), dict) else {}
    # je Quelle ein Versuch am Tag, auch ein erfolgloser; ein neu gebrauchtes Werkzeug wird am selben Tag noch gefragt
    versucht = set(gemerkt.get("versucht") or []) if gemerkt.get("datum") == heute.isoformat() else set()

    bedarf = {w: [grund] for w, grund in GRUNDBEDARF.items()}
    bedarf[harness.HARNESSE[harness_id]["cli"]] = ["Harness"]
    for w, wer in gebraucht(inhalt).items():
        bedarf.setdefault(w, []).extend(x for x in wer if x not in bedarf[w])

    def nachschlagen(quelle):
        if quelle and quelle not in versucht:
            versucht.add(quelle)
            wert = neueste(quelle)
            if wert is not None:  # sonst gilt der letzte Stand
                stand[quelle] = wert
        return stand.get(quelle)

    liste = []
    for w, wer in bedarf.items():
        name, quelle = WERKZEUGE[w]
        version, pfad = installiert(w)
        b = bewerten(version, nachschlagen(quelle), heute)
        befehl = update_befehl(pfad) if pfad and b["status"] in ("veraltet", "ohne Pflege") else None
        liste.append({"id": w, "name": name, "installiert": version, "pfad": pfad, **b, "befehl": befehl, "gebraucht_von": wer})

    fest = harness.SKILLS_CLI.split("@", 1)[1]
    b = bewerten(fest, nachschlagen("npm:skills"), heute)
    liste.append({"id": "skills", "name": "skills (npx)", "installiert": fest, "pfad": None, **b,
                  "befehl": f'harness.py → SKILLS_CLI = "skills@{b["neueste"]}" (erst prüfen, dann eintragen)' if b["status"] == "veraltet" else None,
                  "gebraucht_von": ["Aktionen im Dashboard (npx skills)"]})

    harness.schreiben(datei, json.dumps({"datum": heute.isoformat(), "versucht": sorted(versucht), "neueste": stand}, ensure_ascii=False, indent=1))
    return liste
