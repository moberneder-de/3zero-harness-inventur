"""Tab „Berechtigungen“: was Claude Code oder Codex ohne Rückfrage darf. Nur Anzeige, keine Aktionen.

Claude Code: `permissions` (allow, ask, deny, defaultMode, additionalDirectories) aus den verwalteten Einstellungen,
~/.claude/settings.json und je Projekt aus .claude/settings.json und .claude/settings.local.json. Codex:
approval_policy und sandbox_mode, auch in Profilen, die vertrauten Ordner und die Regeln in ~/.codex/rules.

Markiert wird, was mehr erlaubt, als man beim Eintragen meist meint („breit“), was ins Leere zeigt („verwaist“),
was sich widerspricht und was im Projekt steht, obwohl es schon global gilt („doppelt“). Die Dateien sind von Hand änderbar: Werte mit falschem Typ fallen still weg.
"""

import json
import os
import re
from pathlib import Path

VERWALTET = [Path("/Library/Application Support/ClaudeCode/managed-settings.json"), Path("/etc/claude-code/managed-settings.json")]
GANZ = {"Bash", "Edit", "Write", "MultiEdit", "NotebookEdit", "Read", "WebFetch"}  # ohne Klammer heißt: alles damit
# Wer eines davon mit beliebigen Argumenten starten darf, darf praktisch alles ausführen
INTERPRETER = {"python", "python3", "node", "bun", "bunx", "deno", "npx", "uvx", "ruby", "perl", "php", "bash", "sh", "zsh",
               "osascript", "env", "xargs", "sudo", "eval", "exec"}


def breit(regel):
    """Ein ganzes Werkzeug, ein ganzer MCP-Server oder ein Interpreter mit beliebigen Argumenten (`Bash(python3:*)`)."""
    if regel.startswith("mcp__"):
        return regel.count("__") == 1 or regel.endswith("__*")
    m = re.fullmatch(r"([\w-]+)(?:\((.*)\))?", regel, re.S)
    if not m:
        return False
    werkzeug, inhalt = m.groups()
    if inhalt is None or inhalt.strip() in ("", "*", "**", ":*"):
        return werkzeug in GANZ
    if werkzeug != "Bash" or not inhalt.rstrip().endswith("*"):
        return False
    vorn = inhalt.rstrip()[:-1].rstrip(" :").replace("'", " ").replace('"', " ").split()
    return bool(vorn) and os.path.basename(vorn[0]) in INTERPRETER and all(t.startswith("-") for t in vorn[1:])


def kurz(pfad, home):
    pfad = str(pfad)
    return "~" + pfad[len(str(home)):] if pfad == str(home) or pfad.startswith(str(home) + os.sep) else pfad


def lies(pfad):
    try:
        d = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    p = d.get("permissions") if isinstance(d, dict) else None
    return p if isinstance(p, dict) else {}


def zeile(bereich, quelle, art, regel, marken=(), **extra):
    return {"bereich": bereich, "quelle": quelle, "art": art, "regel": regel, "marken": list(marken), **extra}


def widersprueche(zeilen):
    """Dieselbe Regel als allow, ask oder deny in einem Bereich oder global: Es gilt die strengere, gemeint war etwas anderes."""
    regeln = [z for z in zeilen if z["art"] in ("allow", "ask", "deny")]
    for z in regeln:
        sichtbar = [a for a in regeln if a["bereich"] in ("global", z["bereich"])]
        if any(a["regel"] == z["regel"] and a["art"] != z["art"] for a in sichtbar):
            z["marken"].append("widerspricht")
        # dieselbe Regel mit derselben Art steht schon global: Im Projekt ist sie überflüssig
        if z["bereich"] != "global" and any(a["bereich"] == "global" and a["regel"] == z["regel"] and a["art"] == z["art"] for a in regeln):
            z["marken"].append("doppelt")


def claude(home, projekte, praefixe):
    """`praefixe` sind die Werkzeugpräfixe der installierten MCP-Server (build.werkzeug_praefix)."""
    home = Path(home)
    laengste = sorted(praefixe, key=len, reverse=True)

    def verwaist(regel):
        return regel.startswith("mcp__") and not any(regel == f"mcp__{p}" or regel.startswith(f"mcp__{p}__") for p in laengste)

    quellen = [(d, "global", True) for d in VERWALTET if d.exists()] + [(home / ".claude/settings.json", "global", False)]
    for projekt in sorted(projekte):
        quellen += [(Path(projekt) / ".claude" / f, projekt, False) for f in ("settings.json", "settings.local.json")]
    zeilen, gelesen = [], set()
    for datei, bereich, verwaltet in quellen:
        echt = os.path.realpath(datei)
        if echt in gelesen:  # ~ als Projekt: dessen .claude/settings.json ist die Benutzerdatei
            continue
        gelesen.add(echt)
        p, quelle = lies(datei), kurz(datei, home)
        neu = [zeile(bereich, quelle, art, r, ["breit"] if art == "allow" and breit(r) else [])
               for art in ("allow", "ask", "deny") if isinstance(p.get(art), list) for r in p[art] if isinstance(r, str)]
        for z in neu:
            if verwaist(z["regel"]):
                z["marken"].append("verwaist")
        if isinstance(p.get("defaultMode"), str):
            neu.append(zeile(bereich, quelle, "Modus", f"defaultMode: {p['defaultMode']}",
                             ["breit"] if p["defaultMode"] == "bypassPermissions" else []))
        ordner = p.get("additionalDirectories")
        for d in ordner if isinstance(ordner, list) else []:
            if isinstance(d, str):
                ziel = Path(str(home) + d[1:]) if d.startswith("~") else Path(d)
                neu.append(zeile(bereich, quelle, "Ordner", d, ["breit"] if ziel in (home, Path("/")) else []))
        for z in neu:
            z["datei"] = str(datei)  # für die Aktion „entfernen“; quelle ist nur der Anzeigename
            if verwaltet:
                z["verwaltet"] = True
        zeilen += neu
    widersprueche(zeilen)
    return zeilen


def muster_text(pattern):
    return " ".join(t if isinstance(t, str) else "{" + "|".join(map(str, t)) + "}" for t in pattern)


def codex(cfg, regeln_ordner, home):
    home = Path(home)
    toml = "~/.codex/config.toml"
    zeilen = []

    def modus(quelle, tabelle):
        for schluessel, zu_weit in (("approval_policy", "never"), ("sandbox_mode", "danger-full-access")):
            if isinstance(tabelle.get(schluessel), str):
                zeilen.append(zeile("global", quelle, "Modus", f"{schluessel}: {tabelle[schluessel]}",
                                    ["breit"] if tabelle[schluessel] == zu_weit else []))
    modus(toml, cfg)
    profile = cfg.get("profiles") if isinstance(cfg.get("profiles"), dict) else {}
    for name, tabelle in sorted(profile.items()):
        if isinstance(tabelle, dict):
            modus(f"{toml} → profiles.{name}", tabelle)
    for datei in sorted(Path(regeln_ordner).glob("*.rules")) if Path(regeln_ordner).is_dir() else []:
        quelle = kurz(datei, home)
        for text in datei.read_text(encoding="utf-8", errors="replace").splitlines():
            text = text.strip()
            if not text.startswith("prefix_rule("):
                continue
            muster = re.search(r"pattern\s*=\s*(\[.*\])\s*,\s*decision", text)
            entscheid = re.search(r'decision\s*=\s*"(\w+)"', text)
            try:
                pattern = json.loads(muster.group(1)) if muster else None
            except ValueError:
                pattern = None
            if not (isinstance(pattern, list) and pattern and all(isinstance(t, (str, list)) for t in pattern)):
                zeilen.append(zeile("global", quelle, "unlesbar", text[:300]))
                continue
            ganz = isinstance(pattern[0], str) and (len(pattern) == 1 or pattern[1:] in (["-c"], ["-lc"]))
            zeilen.append(zeile("global", quelle, entscheid.group(1) if entscheid else "allow", muster_text(pattern),
                                ["breit"] if ganz else []))
    projekte = cfg.get("projects") if isinstance(cfg.get("projects"), dict) else {}
    for pfad, p in sorted(projekte.items()):
        if isinstance(p, dict) and isinstance(p.get("trust_level"), str):
            zeilen.append(zeile(pfad, toml, "Vertrauen", p["trust_level"], [] if Path(pfad).is_dir() else ["verwaist"]))
    return zeilen
