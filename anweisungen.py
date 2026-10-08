"""Anweisungen, die der Harness als Text in die Sitzung lädt: CLAUDE.md, Regeln, Output-Styles, MEMORY.md und AGENTS.md.

Der eingebaute Systemprompt des Harness steht in keiner Datei und fehlt hier. Nur lesen: Was sich abschalten lässt,
legt `anweisungen_aktionen()` in build.py fest. Wo Claude Code die Dateien sucht und wann es sie lädt, steht in
https://code.claude.com/docs/en/memory und …/output-styles; für Codex in codex-rs/codex-home/src/instructions/mod.rs.
"""

import fnmatch
import json
import re
from datetime import datetime
from pathlib import Path

VERWALTET = [Path("/Library/Application Support/ClaudeCode/CLAUDE.md"), Path("/etc/claude-code/CLAUDE.md")]
MEMORY_ZEILEN, MEMORY_BYTES = 200, 25_000  # mehr von MEMORY.md lädt Claude Code beim Start nicht
EINGEBAUT = ("Proactive", "Concise", "Explanatory", "Learning")
ANWEISUNG = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
ZAUN = re.compile(r"(?ms)^```.*?^```")
IMPORT = re.compile(r"(?<![\w`@/])@((?:~|\.{1,2})?/?[\w.-][^\s`)]*)")
KOPF = re.compile(r"\A\ufeff?---\n(.*?)\n---", re.S)


def lies(pfad):
    try:
        return Path(pfad).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def lies_json(pfad):
    try:
        daten = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return daten if isinstance(daten, dict) else {}


def kopf(text):
    """Die Schlüssel der ersten Ebene im Kopf einer Datei; ein Wert über mehrere Zeilen (`paths:` als Liste) steht als ""."""
    m = KOPF.match(text)
    return dict(re.findall(r"(?m)^([\w-]+)[ \t]*:[ \t]*(.*?)[ \t]*$", m.group(1))) if m else {}


def kurz(pfad, home):
    pfad = str(pfad)
    return "~" + pfad[len(str(home)):] if pfad.startswith(str(home) + "/") else pfad


def importe(text, datei, home):
    """Wie viele vorhandene Dateien der Text mit @pfad einbindet. Nur was es gibt: `@anthropic-ai/sdk` ist kein Import."""
    gefunden = set()
    for ziel in IMPORT.findall(ZAUN.sub("", text)):
        ziel = ziel.rstrip(".,;:")
        pfad = Path(str(home) + ziel[1:]) if ziel.startswith("~") else Path(datei).parent / ziel
        try:
            if pfad.is_file():
                gefunden.add(pfad.resolve())
        except OSError:
            pass
    return len(gefunden)


def muster(*einstellungen):
    return [m for e in einstellungen if isinstance(e.get("claudeMdExcludes"), list) for m in e["claudeMdExcludes"] if isinstance(m, str)]


def zeile(bereich, art, datei, home, text, status="aktiv", name=None, **extra):
    """`laedt` heißt: steht beim Start jeder Sitzung im Kontext. Tokens sind Zeichen ÷ 4, wie überall."""
    try:
        geaendert = datetime.fromtimestamp(Path(datei).stat().st_mtime).strftime("%Y-%m-%d")
    except OSError:
        geaendert = None
    z = {"bereich": bereich, "art": art, "name": name or kurz(datei, home), "pfad": str(datei), "tokens": len(text) // 4,
         "geaendert": geaendert, "status": status, "laedt": status == "aktiv", "marken": [], **extra}
    anzahl = importe(text, datei, home)
    if anzahl:
        z.update(importe=anzahl)
        z["marken"].append("Importe")
    return z


def ausschliessbar(bereich, art, datei, home, aus, eigene, **extra):
    """Eine CLAUDE.md, Regel oder AGENTS.md. `aus` sind alle Muster aus claudeMdExcludes, die hier gelten, `eigene` die
    aus ~/.claude/settings.json: Nur einen Eintrag dort, der genau diese Datei nennt, nimmt das Dashboard wieder heraus."""
    text = lies(datei)
    if text is None:
        return None
    z = zeile(bereich, art, datei, home, text, **extra)
    if str(datei) in eigene:
        z.update(status="aus", laedt=False, ausgeschlossen="eigen")
    elif any(fnmatch.fnmatchcase(str(datei), m) for m in aus):
        z.update(status="aus", laedt=False, ausgeschlossen="muster")
        z["marken"].append("per Muster")
    elif art == "Regel" and "paths" in kopf(text):
        z.update(status="bei Bedarf", laedt=False)  # lädt erst, wenn Claude eine passende Datei anfasst
    return z


def regeln(bereich, ordner, home, aus, eigene):
    try:
        dateien = sorted(d for d in ordner.rglob("*.md") if d.is_file())
    except OSError:
        return []
    return [z for d in dateien if (z := ausschliessbar(bereich, "Regel", d, home, aus, eigene))]


def output_styles(home, settings):
    """Alle eigenen Styles; aktiv ist der, den `outputStyle` in ~/.claude/settings.json nennt. Ein Projekt kann einen
    anderen wählen, das steht hier nicht. Eigene Styles lassen die eingebauten Programmier-Anweisungen weg, außer mit
    `keep-coding-instructions: true`."""
    gewaehlt = settings.get("outputStyle") if isinstance(settings.get("outputStyle"), str) else None
    zeilen = []
    try:
        dateien = sorted(d for d in (home / ".claude" / "output-styles").glob("*.md") if d.is_file())
    except OSError:
        dateien = []
    for datei in dateien:
        text = lies(datei)
        if text is None:
            continue
        k = kopf(text)
        name = k.get("name") or datei.stem
        z = zeile("global", "Output-Style", datei, home, text, "aktiv" if name == gewaehlt else "aus", name)
        if k.get("keep-coding-instructions", "").lower() != "true":
            z["marken"].append("ohne Programmier-Anweisungen")
        zeilen.append(z)
    if gewaehlt and gewaehlt != "default" and not any(z["status"] == "aktiv" for z in zeilen):
        bekannt = gewaehlt in EINGEBAUT  # ein Name, den es nicht gibt, ergibt den Standard
        zeilen.append({"bereich": "global", "art": "Output-Style", "name": gewaehlt, "pfad": None, "tokens": 0, "geaendert": None,
                       "status": "aktiv" if bekannt else "wirkungslos", "laedt": bekannt, "marken": ["eingebaut" if bekannt else "unbekannt"]})
    for z in zeilen:
        z["gewaehlt"] = z["name"] == gewaehlt
    return zeilen


def memory(projekt, home, an):
    """Auto-Memory eines Projekts. Der Ordner heißt wie der Pfad, mit - für jedes Zeichen außer Buchstaben und Ziffern."""
    datei = home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(projekt)) / "memory" / "MEMORY.md"
    text = lies(datei)
    if text is None:
        return None
    geladen = "\n".join(text.split("\n")[:MEMORY_ZEILEN]).encode("utf-8")[:MEMORY_BYTES].decode("utf-8", errors="ignore")
    z = zeile(str(projekt), "Memory", datei, home, geladen, "aktiv" if an else "aus", "MEMORY.md")
    if len(geladen) < len(text):
        z["marken"].append("gekürzt geladen")
    return z


def claude(home, settings, projekte, verwaltet=VERWALTET):
    home = Path(home)
    settings = settings if isinstance(settings, dict) else {}
    eigene = set(muster(settings))
    zeilen = [zeile("global", "CLAUDE.md", d, home, text, verwaltet=True) for d in verwaltet if (text := lies(d)) is not None]
    zeilen += [z for z in [ausschliessbar("global", "CLAUDE.md", home / ".claude" / "CLAUDE.md", home, eigene, eigene)] if z]
    zeilen += regeln("global", home / ".claude" / "rules", home, eigene, eigene)
    zeilen += output_styles(home, settings)

    eigen = (home / ".claude" / "CLAUDE.md").resolve()
    for projekt in sorted(projekte):
        ordner = Path(projekt)
        try:
            if not ordner.is_dir() or ordner.resolve() == home.resolve():
                continue  # ~ ist auch ein Projekt; seine Dateien sind die globalen
        except OSError:
            continue
        lokal = [lies_json(ordner / ".claude" / n) for n in ("settings.json", "settings.local.json")]
        aus = [*eigene, *muster(*lokal)]
        neu = []
        # Claude Code lädt auch CLAUDE.md aus jedem Ordner über dem Projekt, zuerst die oberste
        for oben in [*reversed(ordner.parents), ordner]:
            for name in ANWEISUNG:
                if (oben / name).resolve() == eigen:
                    continue  # die globale Datei: über ~ als Ordner darüber, oder weil ~/.claude selbst ein Projekt ist
                z = ausschliessbar(projekt, "CLAUDE.md", oben / name, home, aus, eigene, **({} if oben == ordner else {"geerbt": True}))
                if z:
                    neu.append(z)
        claude_md = bool(neu)
        # CLAUDE.md und .claude/CLAUDE.md laden beide: Wer nur eine öffnet, hält sie für die ganze Anleitung
        beide = [z for z in neu if not z.get("geerbt") and Path(z["pfad"]).name == "CLAUDE.md"]
        if len(beide) == 2:
            for z in beide:
                z["marken"].append("zwei CLAUDE.md")
        neu += regeln(projekt, ordner / ".claude" / "rules", home, aus, eigene)
        for name in ("AGENTS.md", ".claude/AGENTS.md"):
            z = ausschliessbar(projekt, "AGENTS.md", ordner / name, home, aus, eigene)
            if z and claude_md and z["status"] == "aktiv":
                z.update(status="wirkungslos", laedt=False)  # Claude Code liest sie nur, wenn es keine CLAUDE.md gibt
            if z:
                neu.append(z)
        an = not any(e.get("autoMemoryEnabled") is False for e in (settings, *lokal))
        z = memory(projekt, home, an)
        if z:
            neu.append(z)
        zeilen += neu
    return zeilen


def codex(home):
    """Global lädt Codex die erste nicht leere von AGENTS.override.md und AGENTS.md aus ~/.codex."""
    home = Path(home)
    zeilen, gefunden = [], False
    for name in ("AGENTS.override.md", "AGENTS.md"):
        datei = home / ".codex" / name
        text = lies(datei)
        if text is None:
            continue
        zeilen.append(zeile("global", "AGENTS.md", datei, home, text.strip(), "aktiv" if text.strip() and not gefunden else "wirkungslos"))
        gefunden = gefunden or bool(text.strip())
    return zeilen
