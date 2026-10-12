"""Anweisungen, die der Harness als Text in die Sitzung lädt: CLAUDE.md, Regeln, Output-Styles, MEMORY.md und AGENTS.md.

Der eingebaute Systemprompt des Harness steht in keiner Datei und fehlt hier. Nur lesen: Was sich abschalten lässt,
legt `anweisungen_aktionen()` in build.py fest. Wo Claude Code die Dateien sucht und wann es sie lädt, steht in
https://code.claude.com/docs/en/memory und …/output-styles; für Codex in codex-rs/codex-home/src/instructions/mod.rs.
"""

import fnmatch
import json
import os
import re
from datetime import datetime
from pathlib import Path

VERWALTET = [Path("/Library/Application Support/ClaudeCode/CLAUDE.md"), Path("/etc/claude-code/CLAUDE.md")]
MEMORY_ZEILEN, MEMORY_BYTES = 200, 25_000  # mehr von MEMORY.md lädt Claude Code beim Start nicht
EINGEBAUT = ("Proactive", "Concise", "Explanatory", "Learning")
ANWEISUNG = ("CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md")
ZAUN = re.compile(r"(?ms)^```.*?^```")
IMPORT = re.compile(r"(?<![\w`@/])@((?:~|\.{1,2})?/?[\w.-][^\s`)]*)")
# Ab hier warnt Claude Code beim Start („… instruction files add up to 151.0k chars, over the 150.0k-char total limit“).
# Die Zahl steht in keiner Doku, sie stammt aus dieser Warnung (Claude Code 2.1.296). Darunter eine eigene Vorwarnung.
LIMIT_ZEICHEN, LIMIT_NAHE = 150_000, 120_000
GROSS_ZEILEN, GROSS_TOKENS = 200, 5_000  # darüber trägt eine CLAUDE.md die Marke „groß“
SUCHGRENZE = 20_000  # so viele Dateien eines Projekts sieht die Prüfung „verwaist“ höchstens an
AUSLASSEN = {".git", "node_modules", ".venv"}
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


def gebunden(text):
    """Die Muster aus `paths` im Kopf einer Regel, als Liste oder als Text mit Kommas; None ohne das Feld. Mit `paths`
    lädt die Regel erst, wenn Claude eine passende Datei mit Read, Edit oder Write anfasst."""
    m = KOPF.match(text)
    zeilen = m.group(1).split("\n") if m else []
    for nr, z in enumerate(zeilen):
        wert = re.match(r"paths[ \t]*:[ \t]*(.*?)[ \t]*$", z)
        if not wert:
            continue
        if wert.group(1):
            teile = re.split(r",(?![^{]*\})", wert.group(1).strip("[]"))  # ein Komma in {ts,tsx} trennt nicht
        else:
            teile = []
            for folge in zeilen[nr + 1:]:
                eintrag = re.match(r"[ \t]+-[ \t]*(.*?)[ \t]*$", folge)
                if not eintrag:
                    break
                teile.append(eintrag.group(1))
        return [t for t in (t.strip().strip("\"'") for t in teile) if t] or None
    return None


def muster_regex(muster):
    """Ein Muster aus `paths` als Regex über den Pfad relativ zum Projekt: `**` über Ordner hinweg, `*` und `?` in
    einem Namen, `{a,b}` als Auswahl, `[ab]` wie geschrieben. None, wenn es sich so nicht lesen lässt. Claude Code
    rechnet mit seiner eigenen Logik; bei ausgefallenen Mustern können beide abweichen."""
    aus, i, tiefe = "", 0, 0
    while i < len(muster):
        c = muster[i]
        if muster.startswith("**/", i):
            aus, i = aus + "(?:.*/)?", i + 3
            continue
        if muster.startswith("**", i):
            aus, i = aus + ".*", i + 2
            continue
        ende = muster.find("]", i + 2) if c == "[" else -1
        if ende > 0:
            aus, i = aus + muster[i:ende + 1], ende + 1
            continue
        if c == "{":
            tiefe += 1
        elif c == "}":
            tiefe -= 1
        aus += {"*": "[^/]*", "?": "[^/]", "{": "(?:", "}": ")"}.get(c) or ("|" if c == "," and tiefe else re.escape(c))
        i += 1
    try:
        return re.compile(aus + r"\Z") if tiefe == 0 else None
    except re.error:
        return None


def dateien_im(ordner):
    """Die Pfade der Dateien eines Projekts, relativ und mit /, höchstens SUCHGRENZE; dazu, ob das alle waren."""
    pfade = []
    for wurzel, unter, namen in os.walk(ordner):
        unter[:] = [u for u in unter if u not in AUSLASSEN]
        rel = os.path.relpath(wurzel, ordner)
        vor = "" if rel == "." else rel.replace(os.sep, "/") + "/"
        for name in namen:
            if len(pfade) >= SUCHGRENZE:
                return pfade, False
            pfade.append(vor + name)
    return pfade, True


def verwaiste_markieren(zeilen, ordner):
    """„verwaist“: Auf kein Muster der Regel passt eine Datei des Projekts, sie lädt also nie — etwa nach dem
    Umbenennen einer Datei. „nicht geprüft“, wo sich das nicht sagen lässt: ein Muster, das sich nicht lesen ließ,
    oder ein Projekt mit mehr Dateien als SUCHGRENZE. Lieber kein Urteil als eine falsche Warnung."""
    offen = [z for z in zeilen if z["status"] == "bei Bedarf" and z.get("gebunden")]
    if not offen:
        return
    pfade, alle = dateien_im(ordner)
    for z in offen:
        regexe = [muster_regex(m) for m in z["gebunden"]]
        if any(r.match(p) for r in regexe if r for p in pfade):
            continue
        z["marken"].append("verwaist" if alle and all(regexe) else "nicht geprüft")


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
    z = {"bereich": bereich, "art": art, "name": name or kurz(datei, home), "pfad": str(datei), "tokens": len(text) // 4, "zeichen": len(text),
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
    if art == "CLAUDE.md" and (len(text.splitlines()) > GROSS_ZEILEN or z["tokens"] > GROSS_TOKENS):
        z["marken"].append("groß")
    if str(datei) in eigene:
        z.update(status="aus", laedt=False, ausgeschlossen="eigen")
    elif any(fnmatch.fnmatchcase(str(datei), m) for m in aus):
        z.update(status="aus", laedt=False, ausgeschlossen="muster")
        z["marken"].append("per Muster")
    muster = gebunden(text) if art == "Regel" else None
    if muster:
        z["gebunden"] = muster
        if z["status"] == "aktiv":
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


def limit_markieren(zeilen):
    """„über dem Limit“ oder „nahe am Limit“: Die Anweisungsdateien, die in einem Projekt beim Start laden, haben mit
    den globalen zusammen mehr als LIMIT_ZEICHEN bzw. LIMIT_NAHE Zeichen. So zählt Claude Code in seiner Warnung:
    CLAUDE.md, Regeln ohne `paths` und AGENTS.md, nicht MEMORY.md und nicht der Output-Style. Die Marke steht an der
    größten dieser Dateien im Projekt, mit der Summe in `limit_summe`; für die globalen Dateien allein an der größten
    globalen."""
    zaehlt = lambda z: z["laedt"] and z["art"] in ("CLAUDE.md", "Regel", "AGENTS.md")
    je = {}
    for z in zeilen:
        if zaehlt(z):
            je.setdefault(z["bereich"], []).append(z)
    global_summe = sum(z["zeichen"] for z in je.get("global", []))
    for bereich, dateien in je.items():
        summe = sum(z["zeichen"] for z in dateien) + (0 if bereich == "global" else global_summe)
        if summe > LIMIT_NAHE:
            groesste = max(dateien, key=lambda z: z["zeichen"])
            groesste["limit_summe"] = summe
            groesste["marken"].append("über dem Limit" if summe > LIMIT_ZEICHEN else "nahe am Limit")


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
        gefunden = regeln(projekt, ordner / ".claude" / "rules", home, aus, eigene)
        verwaiste_markieren(gefunden, ordner)
        neu += gefunden
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
    limit_markieren(zeilen)
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
