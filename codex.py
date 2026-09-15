"""Harness-Inventur für Codex.

Liest ~/.codex und ~/.agents in dieselbe Datenform, die build.py für Claude Code erzeugt, und wird von
build.py mit `--harness codex` aufgerufen. Umgebungswerte von MCP-Servern (env, Tokens, Header) übernimmt
es nie. Codex führt keine Nutzungszähler; die Nutzung stammt aus den Sitzungsprotokollen in ~/.codex/sessions.
"""

import json
import re
import tomllib
from pathlib import Path

from build import frontmatter, lies_json

HOME = Path.home()
CODEX = HOME / ".codex"
AGENTS = HOME / ".agents"
HIER = Path(__file__).resolve().parent
SKILL_PFAD = re.compile(r"(/[^\s'\"<>]+/SKILL\.md)")


def lies_toml(pfad):
    try:
        return tomllib.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def abgeschaltete(cfg):
    """[[skills.config]] mit enabled = false, als (Pfade, Namen). Codex vergleicht Pfade aufgelöst, also ohne Symlinks."""
    pfade, namen = set(), set()
    for e in (cfg.get("skills") or {}).get("config") or []:
        if not isinstance(e, dict) or e.get("enabled", True) is not False:
            continue
        if isinstance(e.get("path"), str):
            pfade.add(str(Path(e["path"]).expanduser().resolve()))
        elif isinstance(e.get("name"), str):
            namen.add(e["name"])
    return pfade, namen


def nur_manuell(ordner):
    """policy.allow_implicit_invocation: false in agents/openai.yaml: Codex lädt den Skill nicht in den Katalog, er läuft nur per $name."""
    try:
        text = (Path(ordner) / "agents" / "openai.yaml").read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r"^\s+allow_implicit_invocation:\s*false\s*$", text, re.M) is not None


def skill_eintrag(anzeige, datei, aus, aktiv=True):
    """Ein Skill. `aus` sind die abgeschalteten (Pfade, Namen); `aktiv` ist False, wenn sein Plugin nicht lädt."""
    fm = frontmatter(datei)
    beschreibung = fm.get("description") if isinstance(fm.get("description"), str) else ""
    pfade, namen = aus
    status = "aktiv" if aktiv and str(Path(datei).resolve()) not in pfade and anzeige not in namen else "aus"
    aufruf = "nur $" if nur_manuell(Path(datei).parent) else "automatisch + $"
    im_kontext = status == "aktiv" and aufruf != "nur $"
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    return {
        "id": f"skill:{anzeige}",
        "name": anzeige,
        "typ": "Skill",
        "beschreibung": beschreibung,
        "status": status,
        "override": None,
        "aufruf": aufruf,
        "im_kontext": im_kontext,
        "tokens": round(len(f"- {anzeige}: {beschreibung}") / 4) if im_kontext else 0,
        "nutzung": None,
        "pfad": str(datei),
        "meta_autor": meta.get("author"),
    }


def quelle_cli(repo, autoren):
    besitzer = repo.split("/")[0]
    return {"art": "skills-CLI", "autor": autoren.get(besitzer, besitzer), "repo": repo, "url": f"https://github.com/{repo}"}


def eigene_skills(aus, lock, autoren):
    """~/.agents/skills (dort legt npx skills ab) und ~/.codex/skills ohne .system. Bei gleichem Namen zählt der erste."""
    ergebnis, namen = [], set()
    for wurzel in (AGENTS / "skills", CODEX / "skills"):
        if not wurzel.is_dir():
            continue
        for ordner in sorted(wurzel.iterdir()):
            datei = ordner / "SKILL.md"
            if ordner.name.startswith(".") or ordner.name in namen or not datei.is_file():
                continue
            namen.add(ordner.name)
            e = skill_eintrag(ordner.name, datei, aus)
            eintrag = lock.get(ordner.name) if wurzel == AGENTS / "skills" else None
            if eintrag:
                pfad = eintrag.get("skillPath", "")
                e["quelle"] = quelle_cli(eintrag.get("source", ""), autoren)
                e["installiert"] = (eintrag.get("installedAt") or "")[:10] or None
                if "/deprecated/" in pfad:
                    e["reife"] = "upstream veraltet"
                elif "/in-progress/" in pfad:
                    e["reife"] = "upstream unfertig"
            else:
                e["quelle"] = {"art": "Symlink" if ordner.is_symlink() else "lokal", "autor": e["meta_autor"] or "unbekannt"}
            ergebnis.append(e)
    return ergebnis


def eingebaute(aus, bewertung):
    """Die Skills, die Codex selbst in ~/.codex/skills/.system einspielt. Das deutsche `was` pflegt der Skill in bewertung-codex.json."""
    was = {b.get("name"): b.get("was") for b in bewertung.get("eingebaut", []) if isinstance(b, dict)}
    ergebnis = []
    for datei in sorted((CODEX / "skills" / ".system").glob("*/SKILL.md")):
        e = skill_eintrag(datei.parent.name, datei, aus)
        ergebnis.append({"name": e["name"], "was": was.get(e["name"]) or e["beschreibung"],
                         "aufruf": e["aufruf"], "tokens": e["tokens"], "pfad": e["pfad"]})
    return ergebnis


def sitzungen():
    return sorted(CODEX.glob("sessions/**/*.jsonl")) + sorted(CODEX.glob("archived_sessions/**/*.jsonl"))


def nutzung_zaehlen(dateien):
    """Skills zählen je Sitzung einmal: per $name gewählt oder ihre SKILL.md gelesen. MCP-Server zählen je Aufruf.
    Ergebnis: ({aufgelöster SKILL.md-Pfad: [Tag, …]}, {Server: [Tag, …]})."""
    skills, mcp = {}, {}
    for datei in dateien:
        gelesen = {}
        try:
            f = open(datei, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for zeile in f:
                if "SKILL.md" not in zeile and '"McpToolCall"' not in zeile:
                    continue
                try:
                    d = json.loads(zeile)
                except ValueError:
                    continue
                tag = (d.get("timestamp") or "")[:10]
                payload = d.get("payload") if isinstance(d.get("payload"), dict) else {}
                item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                if payload.get("type") == "item_completed" and item.get("type") == "McpToolCall" and item.get("server"):
                    mcp.setdefault(item["server"], []).append(tag)
                    continue
                if payload.get("type") == "item_completed" and item.get("type") == "CommandExecution":
                    pfade = SKILL_PFAD.findall(str(item.get("command")))
                elif "skills.selected_skill_instructions" in zeile:
                    pfade = re.findall(r"<path>([^<]+/SKILL\.md)</path>", str(payload))
                else:
                    pfade = []
                for pfad in pfade:
                    gelesen[pfad] = max(tag, gelesen.get(pfad, ""))
        for pfad, tag in gelesen.items():
            skills.setdefault(str(Path(pfad).resolve()), []).append(tag)
    return skills, mcp


def nutzung(tage):
    return {"anzahl": len(tage), "zuletzt": max(tage) or None} if tage else None
