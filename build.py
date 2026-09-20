#!/usr/bin/env python3
"""Harness-Inventur für Claude Code und Codex.

Liest MCP-Server, Plugins und Skills des Harness, in dem der Skill zuletzt gestartet wurde:
bei Claude Code aus ~/.claude, ~/.claude.json und ~/.agents (mit Live-Status über `claude mcp list`),
bei Codex über codex.py aus ~/.codex und ~/.agents. Schreibt inventar.json und dashboard.html,
für Codex inventar-codex.json und dashboard-codex.html.

Alles Geschriebene landet in ~/.3zero-harness-inventur, nicht im Projektordner: Es gehört zum Rechner
des Benutzers, nicht zum Werkzeug. Dort liegt auch die fachliche Bewertung (Beschreibung auf Deutsch,
Überschneidungen, Empfehlungen), bewertung-claude-code.json bzw. bewertung-codex.json. Die pflegt der
Skill /3zero-harness-inventur, dieses Skript liest sie nur.

Aufruf: python3 build.py [--harness claude-code|codex]   (ohne Angabe gilt der zuletzt genannte)
"""

import argparse
import base64
import json
import re
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import harness

HOME = Path.home()
CLAUDE = HOME / ".claude"
AGENTS = HOME / ".agents"  # dorthin legt `npx skills` Skills; Codex liest denselben Ordner
CODEX = HOME / ".codex"  # nur für die Frage, ob ein Skill auch in Codex lädt
PLUGINS = CLAUDE / "plugins"
HIER = Path(__file__).resolve().parent
DATEN = harness.DATEN  # Bewertung, Inventar, Dashboard und harness.json: alles außerhalb des Repos
LOGO = HIER / "wordmark.svg"  # Kopie aus dem 3zero-Design-Kit, viewBox schon gekürzt; siehe Kommentar in der Datei
ICON = HIER / "app-icon.svg"  # Favicon und Zeichen im Seitenkopf
APP_ICON = HIER / "app-icon.png"  # fürs Dock, gerendert aus ICON mit render-app-icon.py


def data_uri(inhalt, mime):
    """Bettet Bytes in die Seite ein, damit das Dashboard eine einzelne Datei bleibt."""
    return f"data:{mime};base64," + base64.b64encode(inhalt).decode()


def lies_json(pfad):
    try:
        return json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


CFG = lies_json(HOME / ".claude.json")
SETTINGS = lies_json(CLAUDE / "settings.json")
BEWERTUNG = lies_json(DATEN / harness.HARNESSE["claude-code"]["bewertung"])
NUTZUNG = CFG.get("skillUsage", {})
OVERRIDES = SETTINGS.get("skillOverrides", {})
LOCK = lies_json(AGENTS / ".skill-lock.json").get("skills", {})


def gesperrte_namen(settings):
    """Server, die deniedMcpServers per Name in allen Projekten sperrt."""
    return {e.get("serverName") for e in settings.get("deniedMcpServers", []) if isinstance(e, dict)} - {None}


GESPERRT = gesperrte_namen(SETTINGS)
HOOK_TAGE = 14  # so weit zurück liest build.py die Transkripte, um Hook-Ausgaben zu messen


def datum_ms(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d") if ms else None


def frontmatter(pfad):
    """Liest den YAML-Kopf einer Markdown-Datei (einfache Teilmenge, ohne PyYAML)."""
    try:
        text = Path(pfad).read_text(encoding="utf-8")
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


def nutzung(anzeige):
    n = NUTZUNG.get(anzeige)
    if not n:
        return None
    return {"anzahl": n.get("usageCount", 0), "zuletzt": datum_ms(n.get("lastUsedAt"))}


def skill_eintrag(anzeige, typ, datei, plugin_aktiv=True):
    """Ein Skill, Befehl oder Agent. `anzeige` ist der Name, unter dem Claude Code ihn führt."""
    fm = frontmatter(datei)
    beschreibung = fm.get("description") if isinstance(fm.get("description"), str) else ""
    override = OVERRIDES.get(anzeige, "on")
    status = "aktiv" if plugin_aktiv and override != "off" else "aus"
    if str(fm.get("disable-model-invocation", "")).lower() == "true" or override == "user-invocable-only":
        aufruf = "nur /"
    elif str(fm.get("user-invocable", "")).lower() == "false":
        aufruf = "nur automatisch"
    else:
        aufruf = "automatisch + /"
    if typ == "Agent":
        aufruf = "als Subagent"
    im_kontext = status == "aktiv" and aufruf != "nur /"
    tokens = 0
    if im_kontext:
        text = anzeige if override == "name-only" else f"- {anzeige}: {beschreibung}"
        tokens = round(len(text) / 4)
    praefix = "agent" if typ == "Agent" else "skill"
    meta = fm.get("metadata") if isinstance(fm.get("metadata"), dict) else {}
    return {
        "id": f"{praefix}:{anzeige}",
        "name": anzeige,
        "typ": typ,
        "beschreibung": beschreibung,
        "status": status,
        "override": override if override != "on" else None,
        "aufruf": aufruf,
        "im_kontext": im_kontext,
        "tokens": tokens,
        "nutzung": nutzung(anzeige),
        "pfad": str(datei),
        "meta_autor": meta.get("author"),
    }


def cli_quelle(repo):
    besitzer = repo.split("/")[0]
    return {
        "art": "skills-CLI",
        "autor": BEWERTUNG.get("autoren", {}).get(besitzer, besitzer),
        "repo": repo,
        "url": f"https://github.com/{repo}",
    }


def github_baum(repo):
    """Ordnerstände eines GitHub-Repos auf dem Standardzweig: Pfad → Git-SHA, "" für die Wurzel. None ohne gh oder Netz."""
    try:
        r = subprocess.run(["gh", "api", f"repos/{repo}/git/trees/HEAD?recursive=1"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    daten = json.loads(r.stdout)
    return {"": daten["sha"], **{t["path"]: t["sha"] for t in daten["tree"] if t["type"] == "tree"}}


def updates_markieren(skills, lock):
    """Setzt `update` bei per npx skills installierten Skills: True, wenn ihr Ordner auf GitHub einen anderen Stand hat
    als bei der Installation (skillFolderHash ist die Git-SHA des Ordners). Ohne Antwort von GitHub bleibt das Feld weg."""
    cli = [(e, lock[e["name"]]) for e in skills if not e.get("plugin") and e["quelle"]["art"] == "skills-CLI" and e["name"] in lock]
    repos = sorted({l["source"] for _, l in cli})
    with ThreadPoolExecutor() as pool:
        baeume = dict(zip(repos, pool.map(github_baum, repos)))
    for e, l in cli:
        stand = (baeume[l["source"]] or {}).get(l["skillPath"].rpartition("/")[0])
        if stand:
            e["update"] = stand != l["skillFolderHash"]


def persoenliche_skills():
    ergebnis = []
    if not (CLAUDE / "skills").is_dir():  # auf einem frischen Rechner gibt es den Ordner noch nicht
        return ergebnis
    for ordner in sorted((CLAUDE / "skills").iterdir()):
        datei = ordner / "SKILL.md"
        if not datei.is_file():
            continue
        e = skill_eintrag(ordner.name, "Skill", datei)
        # Codex liest ~/.agents/skills selbst; dieselbe Datei kann auch unter ~/.codex/skills liegen
        verweis = CODEX / "skills" / ordner.name / "SKILL.md"
        if datei.resolve().is_relative_to((AGENTS / "skills").resolve()) or (verweis.is_file() and verweis.resolve() == datei.resolve()):
            e["geteilt"] = True
        lock = LOCK.get(ordner.name)
        if lock:
            pfad = lock.get("skillPath", "")
            e["quelle"] = cli_quelle(lock.get("source", ""))
            e["installiert"] = (lock.get("installedAt") or "")[:10] or None
            if "/deprecated/" in pfad:
                e["reife"] = "upstream veraltet"
            elif "/in-progress/" in pfad:
                e["reife"] = "upstream unfertig"
        else:
            # Ein Symlink zeigt auf ein Repo, in dem der Skill gepflegt wird; ein Ordner ist eine Kopie
            e["quelle"] = {"art": "Symlink" if ordner.is_symlink() else "lokal", "autor": e["meta_autor"] or "unbekannt"}
        ergebnis.append(e)
    for datei in sorted((CLAUDE / "commands").glob("*.md")):
        e = skill_eintrag(datei.stem, "Befehl", datei)
        e["quelle"] = {"art": "Symlink" if datei.is_symlink() else "lokal", "autor": "unbekannt"}
        ergebnis.append(e)
    return ergebnis


def projekt_skills(projekte):
    """Skills und Befehle unter <Projekt>/.claude aller Projekte, die Claude Code kennt. Sie laden nur in ihrem Projekt."""
    ergebnis, gesehen = [], {CLAUDE.resolve()}
    for pfad in sorted(projekte):
        wurzel = Path(pfad) / ".claude"
        if not wurzel.is_dir() or wurzel.resolve() in gesehen:
            continue
        gesehen.add(wurzel.resolve())
        lock = lies_json(Path(pfad) / "skills-lock.json").get("skills", {})
        dateien = [(o.name, "Skill", o / "SKILL.md") for o in sorted((wurzel / "skills").iterdir())] if (wurzel / "skills").is_dir() else []
        dateien += [(d.stem, "Befehl", d) for d in sorted(wurzel.glob("commands/*.md"))]
        for name, typ, datei in dateien:
            if not datei.is_file():
                continue
            e = skill_eintrag(name, typ, datei)
            e["id"] = f"skill:{Path(pfad).name}/{name}"
            e["projekt"] = str(pfad)
            repo = (lock.get(name) or {}).get("source") if typ == "Skill" else None
            e["quelle"] = cli_quelle(repo) if repo else {"art": "lokal", "autor": e["meta_autor"] or "unbekannt"}
            ergebnis.append(e)
    return ergebnis


def hook_eintraege(roh, schluessel, quelle, pfad, status="aktiv"):
    """Ein Eintrag je Ereignis aus einem hooks-Block (settings.json oder hooks/hooks.json), mit allen Befehlen darunter."""
    ergebnis = []
    for ereignis, gruppen in sorted((roh or {}).items()):
        befehle = [{"befehl": h.get("command") or h.get("prompt") or h.get("url") or h.get("type"),
                    "matcher": g.get("matcher"), "bedingung": h.get("if")}
                   for g in gruppen for h in g.get("hooks", [])]
        ergebnis.append({"id": f"hook:{schluessel}:{ereignis}", "name": ereignis, "typ": "Hook", "ereignis": ereignis,
                         "status": status, "befehle": befehle, "quelle": dict(quelle), "pfad": str(pfad),
                         "tokens": 0, "ausgabe": None, "dauer": None})
    return ergebnis


def eigene_hooks(settings, pfad):
    quelle = {"art": "Einstellungen", "autor": "eigene Einstellungen"}
    hooks = hook_eintraege(settings.get("hooks"), "einstellungen", quelle, pfad)
    zeile = settings.get("statusLine")
    if isinstance(zeile, dict) and zeile.get("command"):
        (status,) = hook_eintraege({"statusLine": [{"hooks": [zeile]}]}, "einstellungen", quelle, pfad)
        hooks.append({**status, "name": "Statuszeile", "ereignis": "Statuszeile"})
    return hooks


def an_claude(ereignis, stdout):
    """Der Teil einer Hook-Ausgabe, den Claude zu sehen bekommt."""
    try:
        daten = json.loads(stdout)
    except ValueError:
        # reiner Text landet nur bei diesen beiden Ereignissen im Kontext
        return stdout.strip() if ereignis in ("SessionStart", "UserPromptSubmit") else ""
    if not isinstance(daten, dict):
        return ""
    spezifisch = daten.get("hookSpecificOutput") if isinstance(daten.get("hookSpecificOutput"), dict) else {}
    teile = [spezifisch.get("additionalContext"), daten.get("reason") if daten.get("decision") == "block" else None]
    return "".join(t for t in teile if isinstance(t, str))


def zeitgrenze():
    """Ab wann ein Ereignis zählt, als ISO-Zeitstempel in UTC wie die Transkripte. Die Dateiauswahl in
    `transkripte()` reicht nicht: Wird eine alte Sitzung fortgesetzt, ist ihre Datei frisch, ihre Ereignisse nicht."""
    return (datetime.now(timezone.utc) - timedelta(days=HOOK_TAGE)).strftime("%Y-%m-%dT%H:%M:%S")


def transkripte():
    grenze = datetime.now().timestamp() - HOOK_TAGE * 86400
    # rglob: Hooks laufen auch in Subagenten, deren Transkripte liegen eine Ebene tiefer
    def frisch(d):
        try:
            return d.stat().st_mtime >= grenze
        except OSError:  # toter Symlink, etwa nach einem verschobenen Projektordner
            return False
    return [d for d in (CLAUDE / "projects").rglob("*.jsonl") if frisch(d)]


def hook_ausgaben(dateien):
    """Je (Ereignis, Befehl) zwei Messungen aus den Transkripten: was der Hook an Claude zurückgab
    (zuletzt, höchstens, Läufe mit Ausgabe) und wie lange er lief (Median, Maximum, Messungen).
    Laufzeiten stehen in zwei Quellen: in jeder Hook-Ausgabe und in der Zusammenfassung am Turn-Ende,
    die auch die Stop-Hooks ohne Ausgabe erfasst. Ein Stop-Hook mit Ausgabe steht in beiden, deshalb
    zählt je Sekunde und Dauer nur ein Lauf — zwei gleich lange Läufe in derselben Sekunde gelten als einer."""
    funde, zeiten = {}, {}
    grenze = zeitgrenze()
    for datei in dateien:
        try:
            f = open(datei, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for zeile in f:
                if '"hook_success"' not in zeile and '"hookInfos"' not in zeile:
                    continue
                try:
                    d = json.loads(zeile)
                except ValueError:
                    continue
                sekunde = (d.get("timestamp") or "")[:19]
                if sekunde < grenze:  # alte Sitzung, gestern fortgesetzt: die Datei ist frisch, das Ereignis nicht
                    continue
                if d.get("subtype") == "stop_hook_summary":
                    for info in d.get("hookInfos") or []:
                        if isinstance(info.get("durationMs"), (int, float)):
                            zeiten.setdefault(("Stop", info.get("command")), set()).add((sekunde, info["durationMs"]))
                    continue
                a = d.get("attachment") or {}
                if a.get("type") != "hook_success":
                    continue
                schluessel = (a.get("hookEvent"), a.get("command"))
                if isinstance(a.get("durationMs"), (int, float)):
                    zeiten.setdefault(schluessel, set()).add((sekunde, a["durationMs"]))
                text = an_claude(a.get("hookEvent"), a.get("stdout") or "")
                if text:
                    funde.setdefault(schluessel, []).append((d.get("timestamp") or "", round(len(text) / 4)))
    ausgaben = {k: {"zuletzt": max(l)[0][:10], "tokens": max(l)[1], "max": max(t for _, t in l), "laeufe": len(l)}
                for k, l in funde.items()}
    dauern = {}
    for k, laeufe in zeiten.items():
        werte = [dauer for _, dauer in laeufe]
        dauern[k] = {"median": round(statistics.median(werte)), "max": max(werte), "messungen": len(werte)}
    return ausgaben, dauern


def messen(hooks, ausgaben, dauern):
    """Trägt die gemessenen Ausgaben und Laufzeiten ein. Kontext in jeder Sitzung kosten nur aktive SessionStart-Hooks."""
    for h in hooks:
        schluessel = {(h["ereignis"], b["befehl"]) for b in h["befehle"]}
        # ein Eintrag steht für ein Ereignis mit allen seinen Befehlen: sie laufen zusammen, der Median zählt zusammen
        gemessen = [dauern[k] for k in schluessel if k in dauern]
        h["dauer"] = {"median": sum(g["median"] for g in gemessen), "max": max(g["max"] for g in gemessen),
                      "messungen": sum(g["messungen"] for g in gemessen)} if gemessen else None
        funde = [ausgaben[k] for k in schluessel if k in ausgaben]
        if not funde:
            continue
        letzte = max(funde, key=lambda f: f["zuletzt"])
        h["ausgabe"] = {"zuletzt": letzte["zuletzt"], "tokens": letzte["tokens"],
                        "max": max(f["max"] for f in funde), "laeufe": sum(f["laeufe"] for f in funde)}
        if h["ereignis"] == "SessionStart" and h["status"] == "aktiv":
            h["tokens"] = sum(f["tokens"] for f in funde)


def werkzeug_praefix(name):
    """Im Transkript heißen MCP-Werkzeuge `mcp__<Server>__<Werkzeug>`, mit ersetzten Sonderzeichen."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", name)


def mcp_messungen(dateien):
    """Je MCP-Server aus den Transkripten: angebotene Werkzeuge, Anweisungsblock in Tokens, Aufrufe.
    Die Schemata sofort geladener Werkzeuge stehen nicht im Transkript; `tokens` misst nur den Anweisungsblock."""
    werkzeuge, tokens, aufrufe = {}, {}, {}
    grenze = zeitgrenze()
    for datei in dateien:
        try:
            f = open(datei, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for zeile in f:
                if "mcp__" not in zeile and "mcp_instructions_delta" not in zeile:
                    continue
                try:
                    d = json.loads(zeile)
                except ValueError:
                    continue
                a = d.get("attachment") or {}
                if a.get("type") in ("deferred_tools_delta", "deferred_tools_record"):
                    namen = a.get("addedNames") or [e.get("name") for e in a.get("entries") or []]
                    for n in namen:
                        if isinstance(n, str) and n.startswith("mcp__"):
                            werkzeuge.setdefault(n.split("__")[1], set()).add(n)
                elif a.get("type") == "mcp_instructions_delta":
                    for name, block in zip(a.get("addedNames") or [], a.get("addedBlocks") or []):
                        tokens[werkzeug_praefix(name)] = round(len(block) / 4)
                inhalt = (d.get("message") or {}).get("content")
                for c in inhalt if isinstance(inhalt, list) else []:
                    name = c.get("name") if isinstance(c, dict) and c.get("type") == "tool_use" else None
                    # Werkzeugliste und Anweisungsblock beschreiben den Server, nicht ein Ereignis, und bleiben
                    if isinstance(name, str) and name.startswith("mcp__") and (d.get("timestamp") or "")[:19] >= grenze:
                        aufrufe.setdefault(name.split("__")[1], []).append((d.get("timestamp") or "")[:10])
    return {s: {"werkzeuge": len(werkzeuge.get(s, ())), "tokens": tokens.get(s, 0),
                "nutzung": {"anzahl": len(aufrufe[s]), "zuletzt": max(aufrufe[s])} if s in aufrufe else None}
            for s in set(werkzeuge) | set(tokens) | set(aufrufe)}


def mcp_definitionen(wurzel, manifest):
    roh = lies_json(wurzel / ".mcp.json")
    server = roh.get("mcpServers", roh)
    if isinstance(manifest.get("mcpServers"), dict):
        server = {**server, **manifest["mcpServers"]}
    return {n: c for n, c in server.items() if isinstance(c, dict)}


def mehrfache_namen(pids):
    """Plugin-Namen, die es in mehreren Marktplätzen gibt. Ihre Bestandteile brauchen den Marktplatz im Namen,
    sonst tragen zwei Skills dieselbe ID und eine Aktion träfe das falsche Plugin."""
    namen = [str(pid).partition("@")[0] for pid in pids]
    return {n for n in namen if namen.count(n) > 1}


def plugins():
    installiert = lies_json(PLUGINS / "installed_plugins.json").get("plugins", {})
    aktiviert = SETTINGS.get("enabledPlugins", {})
    marktplaetze = lies_json(PLUGINS / "known_marketplaces.json")
    liste, bestandteile, hooks = [], [], []
    mehrfach = mehrfache_namen(installiert)
    for pid, installationen in sorted(installiert.items()):
        inst = next((i for i in installationen if i.get("scope") == "user"), installationen[0])
        name, _, mkt = pid.partition("@")
        praefix = pid if name in mehrfach else name  # bei gleichem Namen in zwei Marktplätzen mit Marktplatz
        wurzel = Path(inst["installPath"])
        manifest = lies_json(wurzel / ".claude-plugin" / "plugin.json")
        mkt_info = marktplaetze.get(mkt, {})
        mkt_datei = lies_json(Path(mkt_info.get("installLocation", "")) / ".claude-plugin" / "marketplace.json")
        mkt_eintrag = next((p for p in mkt_datei.get("plugins", []) if p.get("name") == name), {})

        if aktiviert.get(pid) is True:
            status = "aktiv"
        elif pid in aktiviert:
            status = "deaktiviert"
        else:
            status = "nicht aktiviert"
        aktiv = status == "aktiv"

        autor = (manifest.get("author") or mkt_eintrag.get("author") or mkt_datei.get("owner") or {})
        autor = autor.get("name") if isinstance(autor, dict) else str(autor)
        autor = BEWERTUNG.get("autoren", {}).get(autor, autor)

        if mkt_eintrag.get("skills"):
            skill_dateien = [wurzel / p / "SKILL.md" for p in mkt_eintrag["skills"]]
        else:
            skill_dateien = sorted(wurzel.glob("skills/*/SKILL.md"))
        teile = []
        for d in skill_dateien:
            if d.is_file():
                teile.append(skill_eintrag(f"{praefix}:{d.parent.name}", "Skill", d, aktiv))
        for d in sorted(wurzel.glob("commands/*.md")):
            teile.append(skill_eintrag(f"{praefix}:{d.stem}", "Befehl", d, aktiv))
        for d in sorted(wurzel.glob("agents/*.md")):
            teile.append(skill_eintrag(f"{praefix}:{d.stem}", "Agent", d, aktiv))
        for t in teile:
            t["plugin"] = pid
            t["quelle"] = {"art": "Plugin", "autor": autor, "repo": pid}
        bestandteile += teile
        hooks_datei = wurzel / "hooks" / "hooks.json"
        hooks_roh = lies_json(hooks_datei).get("hooks", {})
        for h in hook_eintraege(hooks_roh, pid, {"art": "Plugin", "autor": autor, "repo": pid}, hooks_datei, "aktiv" if aktiv else "aus"):
            hooks.append({**h, "plugin": pid})

        genutzt = [t["nutzung"] for t in teile if t["nutzung"]]
        pnutzung = CFG.get("pluginUsage", {}).get(pid, {})
        mkt_repo = (mkt_info.get("source") or {}).get("repo")
        liste.append({
            "id": f"plugin:{pid}",
            "name": name,
            "marktplatz": mkt,
            "marktplatz_repo": mkt_repo,
            "autor": autor,
            "homepage": manifest.get("homepage") or manifest.get("repository") or mkt_eintrag.get("homepage"),
            "version": inst.get("version"),
            "installiert": (inst.get("installedAt") or "")[:10] or None,
            "aktualisiert": (inst.get("lastUpdated") or "")[:10] or None,
            "status": status,
            "beschreibung": manifest.get("description") or mkt_eintrag.get("description") or "",
            "skills": [t["name"] for t in teile if t["typ"] == "Skill"],
            "befehle": [t["name"] for t in teile if t["typ"] == "Befehl"],
            "agenten": [t["name"] for t in teile if t["typ"] == "Agent"],
            "hooks": sorted(hooks_roh.keys()),
            "mcp": mcp_definitionen(wurzel, manifest),
            "tokens": sum(t["tokens"] for t in teile),
            "nutzung": {
                "anzahl": sum(n["anzahl"] for n in genutzt),
                "zuletzt": max((n["zuletzt"] for n in genutzt if n["zuletzt"]), default=None),
            } if genutzt else None,
            "aktivitaet": pnutzung.get("usageCount"),
        })
    return liste, bestandteile, hooks


def mcp_live():
    """Status laut `claude mcp list`, aus diesem Ordner heraus, also ohne Projekt-Abschaltungen."""
    try:
        r = subprocess.run(["claude", "mcp", "list"], cwd=HIER, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return None
    status = {}
    for zeile in r.stdout.splitlines():
        if " - " not in zeile or ": " not in zeile:
            continue
        links, _, zustand = zeile.rpartition(" - ")
        name, _, ziel = links.partition(": ")
        status[name.strip()] = {"ziel": ziel.strip(), "zustand": zustand.strip()}
    return status


def zustand_deutsch(roh):
    if "Connected" in roh:
        return "verbunden"
    if "Disabled" in roh:
        return "deaktiviert"
    if "auth" in roh.lower():
        return "Anmeldung nötig"
    if "Failed" in roh:
        return "Fehler"
    return roh


def ziel_text(c):
    if c.get("url"):
        return c["url"]
    return " ".join([c.get("command", "")] + [str(a) for a in c.get("args", [])]).strip()


def mcp_server(plugin_liste, live, messungen=None):
    server = {}
    for name in CFG.get("claudeAiMcpEverConnected", []):
        server[name] = {"art": "claude.ai-Connector", "quelle": "claude.ai-Konto", "ziel": ""}
    for name, c in (CFG.get("mcpServers") or {}).items():
        server[name] = {"art": "User", "quelle": "~/.claude.json", "ziel": ziel_text(c)}
    for pl in plugin_liste:
        for sname, c in pl["mcp"].items():
            server[f"plugin:{pl['name']}:{sname}"] = {
                "art": "Plugin",
                "quelle": f"{pl['name']}@{pl['marktplatz']}",
                "ziel": ziel_text(c),
                "plugin_status": pl["status"],
            }
    aus_in = {}
    for pfad, p in (CFG.get("projects") or {}).items():
        for name in p.get("disabledMcpServers") or []:
            aus_in.setdefault(name, []).append(pfad)

    ergebnis = []
    for name, s in sorted(server.items()):
        if live is not None and name in live:
            status = zustand_deutsch(live[name]["zustand"])
            s["ziel"] = s["ziel"] or live[name]["ziel"]
        elif s.get("plugin_status") and s["plugin_status"] != "aktiv":
            status = "inaktiv (Plugin aus)"
        else:
            status = "unbekannt"
        if name in GESPERRT:
            status = "deaktiviert"
        # ohne Messung bleiben die Felder None: nicht gemessen ist etwas anderes als null Werkzeuge
        gemessen = (messungen or {}).get(werkzeug_praefix(name), {"werkzeuge": None, "tokens": None, "nutzung": None})
        ergebnis.append({"id": f"mcp:{name}", "name": name, "status": status, "gesperrt": name in GESPERRT,
                         "aus_in_projekten": aus_in.get(name, []), **s, **gemessen})
    return ergebnis


def projekt_mcp(projekte):
    """MCP-Server, die nur in einem Projekt gelten: aus <Projekt>/.mcp.json und lokal in ~/.claude.json eingetragen.
    Status nur aus der Konfiguration; ein Verbindungstest bräuchte `claude mcp list` in jedem Projekt."""
    ergebnis = []
    for pfad, p in sorted(projekte.items()):
        ordner = Path(pfad)
        einstellungen = [p] + [lies_json(ordner / ".claude" / f) for f in ("settings.json", "settings.local.json")]
        freigegeben = {n for s in einstellungen for n in s.get("enabledMcpjsonServers") or []}
        abgelehnt = {n for s in einstellungen for n in s.get("disabledMcpjsonServers") or []}
        alle_frei = any(s.get("enableAllProjectMcpServers") for s in einstellungen)
        server = [(n, c, ".mcp.json", str(ordner / ".mcp.json")) for n, c in mcp_definitionen(ordner, {}).items()]
        server += [(n, c, "lokal", "~/.claude.json") for n, c in (p.get("mcpServers") or {}).items()]
        for name, c, art, quelle in server:
            if not ordner.is_dir():
                status = "Ordner fehlt"
            elif name in GESPERRT or name in (p.get("disabledMcpServers") or []):
                status = "deaktiviert"
            elif art == "lokal":
                status = "eingetragen"
            elif name in abgelehnt:
                status = "abgelehnt"
            elif name in freigegeben or alle_frei:
                status = "freigegeben"
            else:
                status = "wartet auf Freigabe"
            ergebnis.append({"id": f"mcp:{ordner.name}/{name}", "name": name, "projekt": pfad, "art": art, "quelle": quelle,
                             "ziel": ziel_text(c), "status": status, "gesperrt": name in GESPERRT, "aus_in_projekten": []})
    return ergebnis


def befehl(e, empfehlung):
    """Der Befehl, mit dem sich die Empfehlung umsetzen ließe."""
    eid = e["id"]
    if eid.startswith("hook:") and not e.get("plugin"):
        if e["ereignis"] == "Statuszeile":
            return '~/.claude/settings.json → "statusLine" entfernen'
        return f'~/.claude/settings.json → "hooks" → "{e["ereignis"]}" entfernen'
    if empfehlung in ("abschalten", "nur manuell"):
        wert = "off" if empfehlung == "abschalten" else "user-invocable-only"
        return f"~/.claude/settings.json → \"skillOverrides\": {{\"{e['name']}\": \"{wert}\"}}"
    if eid.startswith("plugin:"):
        return f"claude plugin uninstall {e['name']}@{e['marktplatz']}"
    if eid.startswith("mcp:"):
        if e.get("projekt") and e["status"] == "Ordner fehlt":
            # claude mcp remove -s local braucht den Projektordner als Arbeitsverzeichnis
            return f'~/.claude.json → "projects" → "{e["projekt"]}" → "mcpServers" → "{e["name"]}" von Hand löschen, während keine Claude-Sitzung läuft'
        if e.get("projekt"):
            scope = "project" if e["art"] == ".mcp.json" else "local"
            return f"cd \"{e['projekt']}\" && claude mcp remove \"{e['name']}\" -s {scope}"
        if e["art"] == "claude.ai-Connector":
            return "claude.ai → Einstellungen → Connectors → trennen"
        if e["art"] == "Plugin":
            return f"claude plugin uninstall {e['quelle']}"
        return f"claude mcp remove \"{e['name']}\" -s user"
    if e.get("plugin"):
        return f"claude plugin uninstall {e['plugin']}"
    if e.get("projekt") and e["quelle"]["art"] == "skills-CLI":
        return f"cd \"{e['projekt']}\" && npx skills remove {e['name']}"
    if e.get("projekt"):
        ziel = e["pfad"] if e["typ"] == "Befehl" else str(Path(e["pfad"]).parent)
        return f"mv \"{ziel}\" ~/.Trash/"
    if e["quelle"]["art"] == "skills-CLI":
        return f"npx skills remove --global {e['name']}"
    if e["typ"] == "Befehl":
        return f"mv ~/.claude/commands/{e['name']}.md ~/.Trash/"
    return f"mv ~/.claude/skills/{e['name']} ~/.Trash/"


def aktionen(e):
    """Was sich über das Dashboard umsetzen lässt: Aktion → Befehl (`argv`, ggf. `cwd`), skillOverrides-Wert
    oder deniedMcpServers-Eintrag (`sperren`). Aktionen, die schon gelten, fehlen."""
    eid = e["id"]
    if eid.startswith("hook:"):
        return {}
    if eid.startswith("plugin:"):
        pid = f"{e['name']}@{e['marktplatz']}"
        a = {"deinstallieren": {"argv": ["claude", "plugin", "uninstall", pid]}}
        if e["status"] == "aktiv":
            a["deaktivieren"] = {"argv": ["claude", "plugin", "disable", pid]}
        return a
    if eid.startswith("mcp:") and e.get("projekt"):
        # Sperren per deniedMcpServers gilt global nach Namen, deshalb nur deinstallieren; ohne Ordner geht nicht einmal das
        if e["status"] == "Ordner fehlt":
            return {}
        scope = "project" if e["art"] == ".mcp.json" else "local"
        return {"deinstallieren": {"argv": ["claude", "mcp", "remove", e["name"], "-s", scope], "cwd": e["projekt"]}}
    if eid.startswith("mcp:"):
        a = {}
        if e["art"] == "User":
            a["deinstallieren"] = {"argv": ["claude", "mcp", "remove", e["name"], "-s", "user"]}
        if not e.get("gesperrt"):
            a["deaktivieren"] = {"sperren": {"serverName": e["name"]}}
        return a
    if e["typ"] == "Agent":
        return {}
    if e.get("projekt"):
        # Overrides gelten global nach Namen und träfen gleichnamige Skills anderswo, deshalb nur deinstallieren
        if e["quelle"]["art"] == "skills-CLI":
            return {"deinstallieren": {"argv": ["npx", "skills", "remove", "-y", e["name"]], "cwd": e["projekt"]}}
        ziel = e["pfad"] if e["typ"] == "Befehl" else str(Path(e["pfad"]).parent)
        return {"deinstallieren": {"argv": ["mv", ziel, str(HOME / ".Trash") + "/"]}}
    a = {}
    if not e.get("plugin"):
        if e.get("update"):
            a["aktualisieren"] = {"argv": ["npx", "skills", "update", e["name"], "-g", "-y"]}
        if e["quelle"]["art"] == "skills-CLI":
            # npx skills remove --global entfernt den Skill für alle Harnesses, die ihn lesen
            a["deinstallieren"] = {"argv": ["npx", "skills", "remove", "--global", "-y", e["name"]], "gemeinsam": True}
        else:
            ziel = e["pfad"] if e["typ"] == "Befehl" else str(Path(e["pfad"]).parent)
            schritt = {"argv": ["mv", ziel, str(HOME / ".Trash") + "/"]}
            if Path(ziel).resolve().is_relative_to((AGENTS / "skills").resolve()):
                schritt["gemeinsam"] = True
            a["deinstallieren"] = schritt
    if e["override"] != "off":
        a["deaktivieren"] = {"override": "off"}
    if e["aufruf"] != "nur /":
        a["nur manuell"] = {"override": "user-invocable-only"}
    return a


def bewerte(eintraege, bewertung=None, befehl_fuer=None):
    """Hängt Bewertung und Umsetzungsbefehl an. Ohne Angaben: bewertung-claude-code.json und die Befehle für Claude Code."""
    bewertungen = (BEWERTUNG if bewertung is None else bewertung).get("eintraege", {})
    befehl_fuer = befehl_fuer or befehl
    for e in eintraege:
        b = bewertungen.get(e["id"])
        e["bewertung"] = b
        if b and e.get("quelle") and isinstance(e["quelle"], dict):
            for feld in ("autor", "repo", "url"):
                if b.get(feld):
                    e["quelle"][feld] = b[feld]
        b = b or {}
        e["befehl"] = b.get("befehl") or befehl_fuer(e, b.get("empfehlung"))


def abgleich(alle, bewertung):
    """(unbewertet, entfallen): IDs ohne Bewertung und bewertete IDs, die nicht mehr installiert sind."""
    ids = {e["id"] for e in alle}
    return [e["id"] for e in alle if not e["bewertung"]], sorted(set(bewertung.get("eintraege", {})) - ids)


def version(cli):
    try:
        return subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def datei_stand(pfad):
    """Wann die Datei zuletzt geschrieben wurde. Die Bewertung trägt selbst nur ein Datum."""
    try:
        return datetime.fromtimestamp(Path(pfad).stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    except OSError:
        return None


def kontextlast(inhalt):
    """Tokens, die in jeder Sitzung laden. Projekt-Skills und Projekt-Server gelten nur in ihrem Projekt."""
    return sum(e.get("tokens") or 0 for art in ("skills", "hooks", "mcp", "eingebaut") for e in inhalt.get(art, []))


def verlauf_fortschreiben(pfad, eintrag):
    """Eine Zeile je Tag: Mehrere Läufe am selben Tag überschreiben sich, sonst wüchse die Datei bei jedem Öffnen."""
    try:
        reihe = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        reihe = []
    if not isinstance(reihe, list):
        reihe = []
    reihe = [e for e in reihe if isinstance(e, dict) and e.get("datum") != eintrag["datum"]] + [eintrag]
    reihe.sort(key=lambda e: e["datum"])
    Path(pfad).write_text(json.dumps(reihe, ensure_ascii=False, indent=1), encoding="utf-8")
    return reihe


def claude_inventar():
    plugin_liste, plugin_teile, plugin_hooks = plugins()
    skills = persoenliche_skills() + plugin_teile
    updates_markieren(skills, LOCK)
    projekte = projekt_skills((CFG.get("projects") or {}).keys())
    hooks = eigene_hooks(SETTINGS, CLAUDE / "settings.json") + plugin_hooks
    dateien = transkripte()
    messen(hooks, *hook_ausgaben(dateien))
    for p in plugin_liste:
        p["tokens"] += sum(h["tokens"] for h in hooks if h.get("plugin") == f"{p['name']}@{p['marktplatz']}")
    live = mcp_live()
    mcp = mcp_server(plugin_liste, live, mcp_messungen(dateien))
    mcp_projekte = projekt_mcp(CFG.get("projects") or {})
    alle = mcp + plugin_liste + skills + projekte + mcp_projekte + hooks
    bewerte(alle)
    for e in alle:
        e["aktionen"] = aktionen(e)

    eingebaut = []
    for b in BEWERTUNG.get("eingebaut", []):
        eingebaut.append({**b, "nutzung": nutzung(b["name"])})

    unbewertet, entfallen = abgleich(alle, BEWERTUNG)
    # Overrides gelten nach Namen, auch für Projekt- und eingebaute Skills
    verwaist = sorted(set(OVERRIDES) - {e["name"] for e in skills + projekte + eingebaut})
    return {
        "live_status": live is not None,
        "bewertung_stand": BEWERTUNG.get("stand"),
        "overrides_verwaist": verwaist,
        "mcp": mcp,
        "plugins": plugin_liste,
        "skills": skills,
        "projekt_skills": projekte,
        "projekt_mcp": mcp_projekte,
        "hooks": hooks,
        "hook_tage": HOOK_TAGE,
        "eingebaut": eingebaut,
        "gruppen": BEWERTUNG.get("gruppen", []),
        "hinweise": BEWERTUNG.get("hinweise", []),
        "unbewertet": unbewertet,
        "entfallen": entfallen,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Harness-Inventur für Claude Code oder Codex")
    parser.add_argument("--harness", choices=list(harness.HARNESSE), help="ohne Angabe gilt der zuletzt genannte")
    wahl = parser.parse_args(argv).harness
    DATEN.mkdir(parents=True, exist_ok=True)
    if wahl:
        harness.merken(DATEN, wahl)
    h = wahl or harness.aktuell(DATEN)
    angaben = harness.HARNESSE[h]
    if h == "codex":
        # codex.py holt sich Funktionen mit `from build import …`. Als Skript gestartet heißt dieses Modul
        # aber __main__, nicht build: Der Import führte die Modulebene ein zweites Mal aus und legte eine
        # zweite Kopie aller Konstanten an, mit erneut gelesenen Dateien. Hier zeigen beide Namen auf dasselbe.
        sys.modules.setdefault("build", sys.modules["__main__"])
        import codex  # erst hier, weil codex.py selbst aus build importiert
        inhalt = codex.inventar()
    else:
        inhalt = claude_inventar()
    jetzt = datetime.now()
    last = kontextlast(inhalt)
    verlauf = verlauf_fortschreiben(DATEN / angaben["verlauf"], {
        "datum": jetzt.strftime("%Y-%m-%d"), "kontextlast": last,
        **{art: len(inhalt.get(art, [])) for art in ("mcp", "plugins", "skills", "hooks")}})
    daten = {"erstellt": jetzt.strftime("%Y-%m-%d %H:%M"), "harness": h, "harness_name": angaben["name"],
             "version": version(angaben["cli"]), "kontextlast": last, "verlauf": verlauf,
             "bewertung_erstellt": datei_stand(DATEN / angaben["bewertung"]), **inhalt}
    (DATEN / angaben["inventar"]).write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")

    einbettung = json.dumps(daten, ensure_ascii=False).replace("</", "<\\/")
    vorlage = ((HIER / "template.html").read_text(encoding="utf-8")
               .replace("__LOGO__", data_uri(LOGO.read_bytes(), "image/svg+xml"))
               .replace("__ICON__", data_uri(ICON.read_bytes(), "image/svg+xml"))
               .replace("__APPICON__", data_uri(APP_ICON.read_bytes(), "image/png")))
    (DATEN / angaben["dashboard"]).write_text(vorlage.replace("__DATEN__", einbettung), encoding="utf-8")

    print(f"{angaben['dashboard']} geschrieben ({angaben['name']}): {len(daten['mcp'])} MCP-Server, {len(daten['plugins'])} Plugins, "
          f"{len(daten['skills'])} Skills/Befehle/Agenten, {len(daten['projekt_skills'])} Projekt-Skills, "
          f"{len(daten['projekt_mcp'])} Projekt-MCP-Server, {len(daten['hooks'])} Hooks")
    if daten["live_status"] is False:
        print("Hinweis: `claude mcp list` nicht erreichbar, MCP-Status nur aus der Konfiguration.")
    if daten["unbewertet"]:
        print(f"Noch nicht bewertet ({len(daten['unbewertet'])}): " + ", ".join(daten["unbewertet"]))
    if daten["entfallen"]:
        print(f"Bewertet, aber nicht mehr installiert ({len(daten['entfallen'])}): " + ", ".join(daten["entfallen"]))
    updates = [e["name"] for e in daten["skills"] if e.get("update")]
    if updates:
        print(f"Update verfügbar ({len(updates)}): " + ", ".join(updates))


if __name__ == "__main__":
    main()
