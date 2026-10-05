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
import os
import re
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import berechtigungen
import harness
import mcp_direkt
import modelle
import werkzeuge

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
    """Nur ein JSON-Objekt, sonst {}: Alle Aufrufer lesen mit .get weiter, auch in Dateien, die andere geschrieben haben."""
    try:
        daten = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return daten if isinstance(daten, dict) else {}


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
UNGENUTZT_TAGE = 30  # so lange ohne Nutzung gilt ein Skill, der in jede Sitzung lädt, als ungenutzt
# Unerwartete Fehler je Plugin oder Projekt: Sie kosten nur diese Quelle und stehen im Dashboard. Ein Absturz
# bräche build.py ab, und ohne gelungenen Lauf startet der Server nicht.
LESEFEHLER = []


def lesefehler(quelle, fehler):
    LESEFEHLER.append({"quelle": quelle, "fehler": f"{type(fehler).__name__}: {fehler}"})


def datum_ms(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d") if ms else None


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


def nutzung(anzeige):
    n = NUTZUNG.get(anzeige)
    if not n:
        return None
    return {"anzahl": n.get("usageCount", 0), "zuletzt": datum_ms(n.get("lastUsedAt"))}


def skill_eintrag(anzeige, typ, datei, plugin_aktiv=True, schluessel=None):
    """Ein Skill, Befehl oder Agent. `anzeige` ist der Name im Dashboard, `schluessel` der, unter dem Claude Code
    ihn führt — bei gleichnamigen Plugins trägt nur die Anzeige den Marktplatz, Nutzung und Overrides nicht."""
    schluessel = schluessel or anzeige
    fm = frontmatter(datei)
    beschreibung = fm.get("description") if isinstance(fm.get("description"), str) else ""
    override = OVERRIDES.get(schluessel, "on")
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
        text = schluessel if override == "name-only" else f"- {schluessel}: {beschreibung}"
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
        "nutzung": nutzung(schluessel),
        "pfad": str(datei),
        "meta_autor": meta.get("author"),
        # nur wenn er abweicht: Er sagt, unter welchem Namen skillOverrides und skillUsage den Eintrag führen
        **({"schluessel": schluessel} if schluessel != anzeige else {}),
    }


def autoren():
    """Anzeigenamen aus der Bewertung; sie stammt aus einem Lauf und kann alles Mögliche enthalten."""
    a = BEWERTUNG.get("autoren")
    return a if isinstance(a, dict) else {}


def cli_quelle(repo):
    besitzer = repo.split("/")[0]
    return {
        "art": "skills-CLI",
        "autor": autoren().get(besitzer, besitzer),
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
    try:
        daten = json.loads(r.stdout)
        return {"": daten["sha"], **{t["path"]: t["sha"] for t in daten["tree"] if t["type"] == "tree"}}
    except (ValueError, KeyError, TypeError):  # eine unerwartete Antwort kostet nur die Update-Angabe, nicht den ganzen Lauf
        return None


def gh_json(pfad):
    """GET gegen die GitHub-API per gh. None ohne gh, ohne Netz oder bei einem Fehler: Dann fehlt nur die Angabe."""
    try:
        r = subprocess.run(["gh", "api", pfad], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        return None


# Repos, Pfade und Stände kommen aus fremden Marktplatz-Dateien und gehen an gh api: nur in harmloser Form
GH_REPO = re.compile(r"[\w][\w.-]*/[\w.-]+")
GH_PFAD = re.compile(r"[\w.-]+(?:/[\w.-]+)*|")
GH_REF = re.compile(r"[\w][\w./-]*")
GITHUB_URL = re.compile(r"https://github\.com/([\w][\w.-]*/[\w.-]+?)(?:\.git)?/?")


def gh_datei(repo, pfad, ref=None):
    """Eine JSON-Datei aus einem GitHub-Repo über die contents-API; None, wenn sie fehlt oder nicht parst."""
    antwort = gh_json(f"repos/{repo}/contents/{pfad}" + (f"?ref={ref}" if ref else ""))
    try:
        inhalt = json.loads(base64.b64decode(antwort["content"]))
    except (TypeError, KeyError, ValueError):
        return None
    return inhalt if isinstance(inhalt, dict) else None


def ordner_geaendert(repo, ordner, stand, ref):
    """Liegt der letzte Commit am Ordner nicht im installierten Stand, gibt es Neues. None ohne Antwort."""
    letzte = gh_json(f"repos/{repo}/commits?per_page=1" + (f"&path={ordner}" if ordner else "") + (f"&sha={ref}" if ref else ""))
    try:
        sha = letzte[0]["sha"]
    except (TypeError, IndexError, KeyError):
        return None
    vergleich = gh_json(f"repos/{repo}/compare/{stand}...{sha}")
    return {"ahead": True, "diverged": True, "behind": False, "identical": False}.get(
        vergleich.get("status") if isinstance(vergleich, dict) else None)


def plugin_update(pl, eintrag):
    """So, wie Claude Code die Version festhält: die aus plugin.json; ohne sie der Git-Stand der Quelle, bei einem Ordner
    im Marktplatz also der des ganzen Marktplatzes. Dessen übrige Commits ändern das Plugin nicht, deshalb zählt dort
    nur ein Commit am Ordner, oder an den Bestandteilen, die der Eintrag selbst aufzählt (document-skills teilt sich ein
    Repo mit anderen Plugins). Legt der Marktplatz einen Stand fest (`sha`), gilt der."""
    quelle = eintrag.get("source")
    stand = pl.get("commit") or (pl.get("version") if re.fullmatch(r"[0-9a-f]{7,40}", pl.get("version") or "") else None)
    if isinstance(quelle, str):
        repo, ordner, ref = pl["marktplatz_repo"], quelle.strip().removeprefix("./").strip("/"), None
    elif isinstance(quelle, dict):
        if isinstance(quelle.get("sha"), str):
            return quelle["sha"] != pl["commit"] if pl.get("commit") else None
        url = GITHUB_URL.fullmatch(quelle.get("url") or "") if isinstance(quelle.get("url"), str) else None
        repo = quelle.get("repo") if quelle.get("source") == "github" else url and url.group(1)
        ordner = str(quelle.get("path") or "").strip("/")
        ref = quelle.get("ref")
        if ref is not None and not (isinstance(ref, str) and GH_REF.fullmatch(ref)):
            return None
    else:
        return None
    teile = [x for k in ("skills", "commands", "agents") if isinstance(eintrag.get(k), list) for x in eintrag[k] if isinstance(x, str)]
    pfade = [f"{ordner}/{x.strip().removeprefix('./').strip('/')}".strip("/") for x in teile] or [ordner]
    harmlos = lambda pfad: GH_PFAD.fullmatch(pfad) and ".." not in pfad.split("/")
    if not (isinstance(repo, str) and GH_REPO.fullmatch(repo) and harmlos(ordner) and all(map(harmlos, pfade))):
        return None
    manifest = gh_datei(repo, f"{ordner}/.claude-plugin/plugin.json".lstrip("/"), ref)
    if isinstance((manifest or {}).get("version"), str):
        return manifest["version"] != pl.get("version")
    if not (stand and GH_REF.fullmatch(stand)):
        return None
    geaendert = [ordner_geaendert(repo, pfad, stand, ref) for pfad in pfade]
    return True if True in geaendert else False if all(g is False for g in geaendert) else None


def plugin_updates_markieren(plugins):
    """Setzt `update` bei Plugins aus einem Marktplatz auf GitHub; `claude plugin list` zeigt das nicht. Die Marktplatz-Datei
    einmal je Marktplatz. Ohne gh, ohne Netz oder bei einer unerwarteten Antwort bleibt das Feld weg."""
    repos = sorted({p["marktplatz_repo"] for p in plugins if isinstance(p.get("marktplatz_repo"), str) and GH_REPO.fullmatch(p["marktplatz_repo"])})
    with ThreadPoolExecutor() as pool:
        marktplaetze = dict(zip(repos, pool.map(lambda r: gh_datei(r, ".claude-plugin/marketplace.json"), repos)))

    def eins(pl):
        eintraege = (marktplaetze.get(pl.get("marktplatz_repo")) or {}).get("plugins")
        eintrag = next((e for e in eintraege if isinstance(e, dict) and e.get("name") == pl["name"]), None) if isinstance(eintraege, list) else None
        return plugin_update(pl, eintrag) if eintrag else None
    with ThreadPoolExecutor() as pool:
        for pl, update in zip(plugins, pool.map(eins, plugins)):
            if update is not None:
                pl["update"] = update


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


def projekt_ids(eintraege, praefix):
    """Setzt die IDs von Projekt-Einträgen: <praefix>:<Ordnername>/<Name>. Haben zwei Projekte mit demselben Ordnernamen
    Einträge, trägt die ID stattdessen den ganzen Pfad — sonst teilten sie sich eine und eine Aktion träfe das erste."""
    pfade = {}
    for e in eintraege:
        pfade.setdefault(Path(e["projekt"]).name, set()).add(e["projekt"])
    for e in eintraege:
        ordner = Path(e["projekt"]).name
        e["id"] = f"{praefix}:{e['projekt'] if len(pfade[ordner]) > 1 else ordner}/{e['name']}"


def projekt_skills(projekte):
    """Skills und Befehle unter <Projekt>/.claude aller Projekte, die Claude Code kennt. Sie laden nur in ihrem Projekt."""
    ergebnis, gesehen = [], {CLAUDE.resolve()}
    for pfad in sorted(projekte):
        wurzel = Path(pfad) / ".claude"
        try:
            if not wurzel.is_dir() or wurzel.resolve() in gesehen:
                continue
            gesehen.add(wurzel.resolve())
            dateien = [(o.name, "Skill", o / "SKILL.md") for o in sorted((wurzel / "skills").iterdir())] if (wurzel / "skills").is_dir() else []
            dateien += [(d.stem, "Befehl", d) for d in sorted(wurzel.glob("commands/*.md"))]
        except OSError:  # etwa ~/Documents, das macOS ohne Freigabe verweigert: nur dieses Projekt fällt weg
            continue
        try:
            lock = lies_json(Path(pfad) / "skills-lock.json").get("skills", {})
            eintraege = []
            for name, typ, datei in dateien:
                if not datei.is_file():
                    continue
                e = skill_eintrag(name, typ, datei)
                e["projekt"] = str(pfad)
                repo = (lock.get(name) or {}).get("source") if typ == "Skill" else None
                e["quelle"] = cli_quelle(repo) if repo else {"art": "lokal", "autor": e["meta_autor"] or "unbekannt"}
                eintraege.append(e)
        except Exception as fehler:
            lesefehler(f"Projekt {pfad}", fehler)
            continue
        ergebnis += eintraege
    projekt_ids(ergebnis, "skill")
    return ergebnis


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


MOD_AUFRUF = re.compile(r"""\bon\(\s*(['"`])([\w.*-]+)\1\s*(?:,\s*(\{[^{}]{0,200}\}))?""")


def mod_eintraege(hooks_datei, wurzel, schluessel, quelle, status="aktiv"):
    """Mods: Hook-Module, die hooks/hooks.json unter `modules` nennt. Sie laufen in jeder Sitzung mit und können
    Werkzeug-Aufrufe, Prompt und System-Prompt ändern. Ein Eintrag je Ereignis, abgelesen an `on('…'` im Text der
    genannten Datei — gelesen, nie ausgeführt. Steht ein `on(` nur in einer importierten Datei, fehlt es; ohne Fund
    bleibt deshalb eine Zeile, sonst wäre das Modul unsichtbar."""
    module = lies_json(hooks_datei).get("modules")
    ergebnis = {}
    for m in module if isinstance(module, list) else []:
        if not isinstance(m, str):
            continue
        datei = (hooks_datei.parent / m).resolve()
        # der Pfad kommt aus einer fremden Datei: nichts lesen, was aus dem Plugin herausführt
        if not datei.is_relative_to(wurzel.resolve()) or not datei.is_file():
            continue
        funde = MOD_AUFRUF.findall(datei.read_text(encoding="utf-8", errors="replace")) or [("", "unbekannt", "")]
        for _, ereignis, matcher in funde:
            e = ergebnis.setdefault(ereignis, {
                "id": f"hook:{schluessel}:mod:{ereignis}", "name": "Mod" if ereignis == "unbekannt" else ereignis, "typ": "Hook",
                "ereignis": ereignis, "status": status, "befehle": [], "quelle": dict(quelle), "pfad": str(datei),
                "tokens": 0, "ausgabe": None, "dauer": None, "mod": True})
            e["befehle"].append({"befehl": datei.name, "matcher": matcher or None, "bedingung": None})
    return [ergebnis[k] for k in sorted(ergebnis)]


PLUGIN_ORDNER = "CLAUDE_CODE_PLUGIN_DIRS"


def ordner_plugins():
    """Hooks und Mods aus Plugins, die ohne Installation aus einem Ordner laden: Skill-Ordner mit eigener plugin.json,
    die Ordner aus CLAUDE_CODE_PLUGIN_DIRS (Umgebung oder `env` in settings.json) und die Mods aus
    ~/.claude/dev-mods/<Sitzung>, die nur in ihrer Sitzung laden. Die ID trägt den Ordner, `ordner` sagt `aktionen()`
    und `befehl()`, dass der Eintrag weder zu einem Plugin noch in settings.json gehört."""
    def unter(pfad):
        try:
            return sorted(p for p in pfad.iterdir() if p.is_dir())
        except OSError:
            return []
    env = SETTINGS.get("env")
    eingetragen = [os.environ.get(PLUGIN_ORDNER), env.get(PLUGIN_ORDNER) if isinstance(env, dict) else None]
    orte = [("Skill-Ordner", o, "aktiv") for o in unter(CLAUDE / "skills")]
    orte += [("Plugin-Ordner", Path(p).expanduser(), "aktiv")
             for liste in eingetragen if isinstance(liste, str) for p in liste.split(os.pathsep) if p]
    orte += [("Mod-Ordner", o, "nur eine Sitzung") for sitzung in unter(CLAUDE / "dev-mods") for o in unter(sitzung)]
    ergebnis, gesehen = [], set()
    for art, wurzel, status in orte:
        try:
            if wurzel.resolve() in gesehen or not (wurzel / ".claude-plugin" / "plugin.json").is_file():
                continue
            gesehen.add(wurzel.resolve())
            quelle = {"art": art, "autor": f"{art} {wurzel.name}"}
            datei = wurzel / "hooks" / "hooks.json"
            eintraege = hook_eintraege(lies_json(datei).get("hooks"), wurzel, quelle, datei, status)
            eintraege += mod_eintraege(datei, wurzel, wurzel, quelle, status)
        except Exception as fehler:
            lesefehler(f"{art} {wurzel}", fehler)
            continue
        ergebnis += [{**e, "ordner": str(wurzel)} for e in eintraege]
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
        # Ein Eintrag steht für ein Ereignis mit allen seinen Befehlen. Claude Code führt sie laut Doku parallel
        # aus ("All matching hooks run in parallel"), also wartet der Benutzer auf den langsamsten, nicht auf alle.
        gemessen = [dauern[k] for k in schluessel if k in dauern]
        h["dauer"] = {"median": max(g["median"] for g in gemessen), "max": max(g["max"] for g in gemessen),
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


def mcp_messungen(dateien, praefixe=()):
    """Je MCP-Server aus den Transkripten: angebotene Werkzeuge, Kontext in Tokens, Aufrufe. `tokens` sind der
    Anweisungsblock und die Werkzeugnamen, die jede Sitzung in ihrer Liste sieht; die Schemata lädt Claude Code erst
    bei Bedarf, und sie stehen nicht im Transkript.
    `praefixe` sind die Werkzeugpräfixe der bekannten Server: Ein Servername kann selbst `__` enthalten."""
    laengste = sorted(praefixe, key=len, reverse=True)

    def server(n):
        return next((p for p in laengste if n.startswith(f"mcp__{p}__")), None) or n.split("__")[1]
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
                            werkzeuge.setdefault(server(n), set()).add(n)
                elif a.get("type") == "mcp_instructions_delta":
                    for name, block in zip(a.get("addedNames") or [], a.get("addedBlocks") or []):
                        tokens[werkzeug_praefix(name)] = round(len(block) / 4)
                inhalt = (d.get("message") or {}).get("content")
                for c in inhalt if isinstance(inhalt, list) else []:
                    name = c.get("name") if isinstance(c, dict) and c.get("type") == "tool_use" else None
                    # Werkzeugliste und Anweisungsblock beschreiben den Server, nicht ein Ereignis, und bleiben
                    if isinstance(name, str) and name.startswith("mcp__") and (d.get("timestamp") or "")[:19] >= grenze:
                        aufrufe.setdefault(server(name), []).append((d.get("timestamp") or "")[:10])
    return {s: {"werkzeuge": len(werkzeuge.get(s, ())),
                "tokens": tokens.get(s, 0) + round(sum(len(n) + 1 for n in werkzeuge.get(s, ())) / 4), "anweisungen": tokens.get(s, 0),
                "nutzung": {"anzahl": len(aufrufe[s]), "zuletzt": max(aufrufe[s])} if s in aufrufe else None}
            for s in set(werkzeuge) | set(tokens) | set(aufrufe)}


def mcp_definitionen(wurzel, manifest):
    roh = lies_json(wurzel / ".mcp.json")
    # Ohne `mcpServers` gilt die Datei nur dann als Servertabelle, wenn jeder Wert wie eine Serverangabe aussieht:
    # `{"servers": …, "inputs": …}` (die VS-Code-Schreibweise) ergäbe sonst einen Eintrag namens „servers“.
    ist_server = lambda c: isinstance(c, dict) and ("command" in c or "url" in c)
    if isinstance(roh.get("mcpServers"), dict):
        server = roh["mcpServers"]
    else:
        server = roh if roh and all(ist_server(c) for c in roh.values()) else {}
    if isinstance(manifest.get("mcpServers"), dict):
        server = {**server, **manifest["mcpServers"]}
    return {n: c for n, c in server.items() if isinstance(c, dict)}


def marktplatz_datei(mkt_info):
    """Die marketplace.json eines Marktplatzes. Ohne Ortsangabe nichts lesen: `Path("")` wäre der Arbeitsordner."""
    ort = mkt_info.get("installLocation")
    return lies_json(Path(ort) / ".claude-plugin" / "marketplace.json") if ort else {}


def plugin_skill_dateien(wurzel, mkt_eintrag, manifest):
    """Welche SKILL.md zum Plugin gehören. Nennt der Marktplatz sie, gelten seine Angaben. Sonst hilft nur der
    Ordner — aber nur, wenn er dem Plugin gehört: Repo-Marktplätze teilen einen Checkout, und dort lägen die
    Skills aller Plugins des Repos mit. Ein eigenes .claude-plugin/plugin.json unterscheidet beide Fälle."""
    if mkt_eintrag.get("skills"):
        pfade = mkt_eintrag["skills"]
        return [wurzel / p / "SKILL.md" for p in ([pfade] if isinstance(pfade, str) else pfade) if isinstance(p, str)]
    return sorted(wurzel.glob("skills/*/SKILL.md")) if manifest else []


def mehrfache_namen(pids):
    """Plugin-Namen, die es in mehreren Marktplätzen gibt. Ihre Bestandteile brauchen den Marktplatz im Namen,
    sonst tragen zwei Skills dieselbe ID und eine Aktion träfe das falsche Plugin."""
    namen = [str(pid).partition("@")[0] for pid in pids]
    return {n for n in namen if namen.count(n) > 1}


def plugins():
    roh = lies_json(PLUGINS / "installed_plugins.json").get("plugins", {})
    # nur Installationen mit Pfad: Ein Eintrag ohne sie ist nicht installiert
    installiert = {pid: mit_pfad for pid, inst in roh.items() if (mit_pfad := [i for i in inst if i.get("installPath")])}
    aktiviert = SETTINGS.get("enabledPlugins", {})
    marktplaetze = lies_json(PLUGINS / "known_marketplaces.json")
    liste, bestandteile, hooks = [], [], []
    mehrfach = mehrfache_namen(installiert)
    for pid, installationen in sorted(installiert.items()):
        try:
            inst = next((i for i in installationen if i.get("scope") == "user"), installationen[0])
            name, _, mkt = pid.partition("@")
            praefix = pid if name in mehrfach else name  # bei gleichem Namen in zwei Marktplätzen mit Marktplatz
            wurzel = Path(inst["installPath"])
            manifest = lies_json(wurzel / ".claude-plugin" / "plugin.json")
            mkt_info = marktplaetze.get(mkt, {})
            mkt_datei = marktplatz_datei(mkt_info)
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
            autor = autoren().get(autor, autor)

            skill_dateien = plugin_skill_dateien(wurzel, mkt_eintrag, manifest)
            teile, plugin_hooks = [], []
            for d in skill_dateien:
                if d.is_file():
                    teile.append(skill_eintrag(f"{praefix}:{d.parent.name}", "Skill", d, aktiv, f"{name}:{d.parent.name}"))
            for d in sorted(wurzel.glob("commands/*.md")):
                teile.append(skill_eintrag(f"{praefix}:{d.stem}", "Befehl", d, aktiv, f"{name}:{d.stem}"))
            for d in sorted(wurzel.glob("agents/*.md")):
                teile.append(skill_eintrag(f"{praefix}:{d.stem}", "Agent", d, aktiv, f"{name}:{d.stem}"))
            for t in teile:
                t["plugin"] = pid
                t["quelle"] = {"art": "Plugin", "autor": autor, "repo": pid}
            hooks_datei = wurzel / "hooks" / "hooks.json"
            hooks_roh = lies_json(hooks_datei).get("hooks")
            hooks_roh = hooks_roh if isinstance(hooks_roh, dict) else {}
            for h in hook_eintraege(hooks_roh, pid, {"art": "Plugin", "autor": autor, "repo": pid}, hooks_datei, "aktiv" if aktiv else "aus"):
                plugin_hooks.append({**h, "plugin": pid})
            mods = mod_eintraege(hooks_datei, wurzel, pid, {"art": "Plugin", "autor": autor, "repo": pid}, "aktiv" if aktiv else "aus")
            plugin_hooks += [{**h, "plugin": pid} for h in mods]

            genutzt = [t["nutzung"] for t in teile if t["nutzung"]]
            pnutzung = CFG.get("pluginUsage", {}).get(pid, {})
            mkt_repo = (mkt_info.get("source") or {}).get("repo")
            eintrag = {
                "id": f"plugin:{pid}",
                "name": name,
                "marktplatz": mkt,
                "marktplatz_repo": mkt_repo,
                "autor": autor,
                "homepage": manifest.get("homepage") or manifest.get("repository") or mkt_eintrag.get("homepage"),
                "version": inst.get("version"),
                "commit": inst.get("gitCommitSha"),
                "installiert": (inst.get("installedAt") or "")[:10] or None,
                "aktualisiert": (inst.get("lastUpdated") or "")[:10] or None,
                "status": status,
                "beschreibung": manifest.get("description") or mkt_eintrag.get("description") or "",
                "skills": [t["name"] for t in teile if t["typ"] == "Skill"],
                "befehle": [t["name"] for t in teile if t["typ"] == "Befehl"],
                "agenten": [t["name"] for t in teile if t["typ"] == "Agent"],
                "hooks": sorted(hooks_roh.keys()),
                "mods": [h["ereignis"] for h in mods],
                "mcp": mcp_definitionen(wurzel, manifest),
                "tokens": sum(t["tokens"] for t in teile),
                "nutzung": {
                    "anzahl": sum(n["anzahl"] for n in genutzt),
                    "zuletzt": max((n["zuletzt"] for n in genutzt if n["zuletzt"]), default=None),
                } if genutzt else None,
                "aktivitaet": pnutzung.get("usageCount"),
            }
        except Exception as fehler:
            lesefehler(f"Plugin {pid}", fehler)
            continue
        liste.append(eintrag)
        bestandteile += teile
        hooks += plugin_hooks
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


def mcp_server(plugin_liste, live, messungen=None, direkt=None):
    """`direkt` fragt einen Server anhand seiner Konfiguration selbst (mcp_direkt.gemessen); ihre Werte gehen denen aus
    den Transkripten vor, weil sie den heutigen Stand zeigen."""
    server, roh = {}, {}
    for name in CFG.get("claudeAiMcpEverConnected", []):
        server[name] = {"art": "claude.ai-Connector", "quelle": "claude.ai-Konto", "ziel": ""}
    for name, c in (CFG.get("mcpServers") or {}).items():
        server[name] = {"art": "User", "quelle": "~/.claude.json", "ziel": ziel_text(c)}
        roh[name] = c
    mehrfach = mehrfache_namen(f"{pl['name']}@{pl['marktplatz']}" for pl in plugin_liste)
    for pl in plugin_liste:
        pid = f"{pl['name']}@{pl['marktplatz']}"
        for sname, c in pl["mcp"].items():
            # bei gleichem Namen in zwei Marktplätzen mit Marktplatz, sonst überschriebe der zweite den ersten.
            # Claude Code führt den Server ohne: Live-Status, Sperre und Messung hängen deshalb am `schluessel`.
            name = f"plugin:{pid if pl['name'] in mehrfach else pl['name']}:{sname}"
            roh[name] = c
            server[name] = {
                "art": "Plugin",
                "quelle": pid,
                "ziel": ziel_text(c),
                "plugin_status": pl["status"],
                **({"schluessel": f"plugin:{pl['name']}:{sname}"} if pl["name"] in mehrfach else {}),
            }
    aus_in = {}
    for pfad, p in (CFG.get("projects") or {}).items():
        for name in p.get("disabledMcpServers") or []:
            aus_in.setdefault(name, []).append(pfad)

    ergebnis = []
    for name, s in sorted(server.items()):
        echt = s.get("schluessel", name)  # der Name, unter dem Claude Code den Server führt
        if live is not None and echt in live:
            status = zustand_deutsch(live[echt]["zustand"])
            s["ziel"] = s["ziel"] or verdecken(live[echt]["ziel"])
        elif s.get("plugin_status") and s["plugin_status"] != "aktiv":
            status = "inaktiv (Plugin aus)"
        else:
            status = "unbekannt"
        if echt in GESPERRT:
            status = "deaktiviert"
        # ohne Messung bleiben die Felder None: nicht gemessen ist etwas anderes als null Werkzeuge
        gemessen = (messungen or {}).get(werkzeug_praefix(echt))
        gemessen = {**gemessen, "messung": "Transkript"} if gemessen else {"werkzeuge": None, "tokens": None, "nutzung": None}
        # gesperrt oder mit dem Plugin aus: lädt in keine Sitzung, die Inventur fragt ihn nicht
        d = direkt(roh[name]) if direkt and name in roh and status not in ("deaktiviert", "inaktiv (Plugin aus)") else None
        if d:
            gemessen = {**gemessen, **mcp_direkt.felder(d, werkzeug_praefix(echt))}
        ergebnis.append({"id": f"mcp:{name}", "name": name, "status": status, "gesperrt": echt in GESPERRT,
                         "aus_in_projekten": aus_in.get(echt, []), **s, **gemessen,
                         "netz": netz(s["art"], roh.get(name), status, gemessen.get("messung"), (d or {}).get("anmeldung"))})
    return ergebnis


def projekt_mcp(projekte):
    """MCP-Server, die nur in einem Projekt gelten: aus <Projekt>/.mcp.json und lokal in ~/.claude.json eingetragen.
    Status nur aus der Konfiguration; ein Verbindungstest bräuchte `claude mcp list` in jedem Projekt."""
    ergebnis = []
    for pfad, p in sorted(projekte.items()):
        try:
            eintraege = []
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
                eintraege.append({"name": name, "projekt": pfad, "art": art, "quelle": quelle,
                                 "ziel": ziel_text(c), "status": status, "gesperrt": name in GESPERRT, "aus_in_projekten": []})
        except Exception as fehler:
            lesefehler(f"Projekt {pfad}", fehler)
            continue
        ergebnis += eintraege
    projekt_ids(ergebnis, "mcp")
    return ergebnis


SICHER = re.compile(r"[\w@%+=:,./-]+")


def shellwort(wert):
    """Ein Wort für einen Befehl, den der Benutzer ins Terminal einfügt. Einfache Anführungszeichen, weil in
    doppelten `, $ und \\ wirksam blieben: Ein Projektpfad mit $(…) führte sonst etwas anderes aus, als dasteht."""
    wert = str(wert)
    return wert if SICHER.fullmatch(wert) else "'" + wert.replace("'", "'\\''") + "'"


def papierkorb_befehl(ziel):
    """Der Befehl zum Kopieren; ~/.Trash gibt es nur auf dem Mac. Im Dashboard verschiebt der Server selbst (Schritt `papierkorb`)."""
    return f"mv {ziel} ~/.Trash/" if sys.platform == "darwin" else f"gio trash {ziel}"


def befehl(e, empfehlung):
    """Der Befehl, mit dem sich die Empfehlung umsetzen ließe."""
    eid = e["id"]
    if eid.startswith("hook:") and e.get("ordner"):
        if e["quelle"]["art"] == "Plugin-Ordner":
            return f"{PLUGIN_ORDNER} → {e['ordner']} herausnehmen"
        # im Skill-Ordner nur die Hooks: Der Skill daneben soll bleiben
        return papierkorb_befehl(shellwort(e["ordner"] + ("/hooks" if e["quelle"]["art"] == "Skill-Ordner" else "")))
    if eid.startswith("hook:") and not e.get("plugin"):
        if e["ereignis"] == "Statuszeile":
            return '~/.claude/settings.json → "statusLine" entfernen'
        return f'~/.claude/settings.json → "hooks" → "{e["ereignis"]}" entfernen'
    # Plugins und MCP-Server vor skillOverrides: Ein Override unter ihrem Namen bewirkt nichts
    if eid.startswith("plugin:"):
        pid = shellwort(e['name'] + '@' + e['marktplatz'])
        if empfehlung == "nur manuell":
            return f'nur je Skill: ~/.claude/settings.json → "skillOverrides" → "{e["name"]}:<skill>": "user-invocable-only"'
        return f"claude plugin disable {pid}" if empfehlung == "abschalten" else f"claude plugin uninstall {pid}"
    if eid.startswith("mcp:") and empfehlung == "abschalten" and not e.get("projekt"):
        sperre = json.dumps({"serverName": e.get("schluessel", e["name"])}, ensure_ascii=False)
        return f'~/.claude/settings.json → "deniedMcpServers" → {sperre} ergänzen'
    if empfehlung in ("abschalten", "nur manuell") and not eid.startswith("mcp:"):
        wert = "off" if empfehlung == "abschalten" else "user-invocable-only"
        return f"~/.claude/settings.json → \"skillOverrides\": {json.dumps({e.get('schluessel', e['name']): wert}, ensure_ascii=False)}"
    if eid.startswith("mcp:"):
        if e.get("projekt") and e["status"] == "Ordner fehlt":
            # claude mcp remove -s local braucht den Projektordner als Arbeitsverzeichnis
            return f'~/.claude.json → "projects" → "{e["projekt"]}" → "mcpServers" → "{e["name"]}" von Hand löschen, während keine Claude-Sitzung läuft'
        if e.get("projekt"):
            scope = "project" if e["art"] == ".mcp.json" else "local"
            return f"cd {shellwort(e['projekt'])} && claude mcp remove {shellwort(e['name'])} -s {scope}"
        if e["art"] == "claude.ai-Connector":
            return "claude.ai → Einstellungen → Connectors → trennen"
        if e["art"] == "Plugin":
            return f"claude plugin uninstall {shellwort(e['quelle'])}"
        return f"claude mcp remove {shellwort(e['name'])} -s user"
    if e.get("plugin"):
        return f"claude plugin uninstall {shellwort(e['plugin'])}"
    if e.get("projekt") and e["quelle"]["art"] == "skills-CLI":
        return f"cd {shellwort(e['projekt'])} && npx -y {harness.SKILLS_CLI} remove {shellwort(e['name'])}"
    if e.get("projekt"):
        ziel = e["pfad"] if e["typ"] == "Befehl" else str(Path(e["pfad"]).parent)
        return papierkorb_befehl(shellwort(ziel))
    if e["quelle"]["art"] == "skills-CLI":
        return f"npx -y {harness.SKILLS_CLI} remove --global {shellwort(e['name'])}"
    if e["typ"] == "Befehl":
        return papierkorb_befehl(f"~/.claude/commands/{shellwort(e['name'] + '.md')}")
    return papierkorb_befehl(f"~/.claude/skills/{shellwort(e['name'])}")  # ~ bleibt ungequotet, sonst löst die Shell es nicht auf


def aktionen(e):
    """Was sich über das Dashboard umsetzen lässt: Aktion → Befehl (`argv`, ggf. `cwd`), skillOverrides-Wert,
    deniedMcpServers-Eintrag (`sperren`, zurück mit `entsperren`), Papierkorb (`papierkorb`) oder ein Hook-Block aus
    settings.json (`hook_entfernen`). Ein Override `None` nimmt den Eintrag weg. Aktionen, die schon gelten, fehlen."""
    eid = e["id"]
    if eid.startswith("hook:"):
        if e.get("plugin") or e.get("ordner"):
            return {}  # kommt mit dem Plugin oder seinem Ordner und geht mit ihm
        # Einen einzelnen Hook kann Claude Code nicht abschalten, nur alle zusammen (disableAllHooks). Bleibt das Entfernen.
        return {"deinstallieren": {"hook_entfernen": "statusLine" if e["ereignis"] == "Statuszeile" else e["ereignis"]}}
    if eid.startswith("plugin:"):
        pid = f"{e['name']}@{e['marktplatz']}"
        # aktualisieren immer: Ob ein Update bereitsteht, zeigt `claude plugin list` nicht, der Befehl sagt es selbst.
        # Ohne -y: Damit nähme der Server einen geänderten Installationsbefehl aus dem Marktplatz ungefragt an.
        a = {"aktualisieren": {"argv": ["claude", "plugin", "update", pid]},
             "deinstallieren": {"argv": ["claude", "plugin", "uninstall", pid]}}
        if e["status"] == "aktiv":
            a["deaktivieren"] = {"argv": ["claude", "plugin", "disable", pid]}
        else:
            a["einschalten"] = {"argv": ["claude", "plugin", "enable", pid]}
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
        sperre = {"serverName": e.get("schluessel", e["name"])}
        if e.get("gesperrt"):
            a["einschalten"] = {"entsperren": sperre}
        else:
            a["deaktivieren"] = {"sperren": sperre}
        return a
    if e["typ"] == "Agent":
        return {}
    if e.get("projekt"):
        # Overrides gelten global nach Namen und träfen gleichnamige Skills anderswo, deshalb nur deinstallieren
        if e["quelle"]["art"] == "skills-CLI":
            return {"deinstallieren": {"argv": ["npx", "-y", harness.SKILLS_CLI, "remove", "-y", e["name"]], "cwd": e["projekt"]}}
        ziel = e["pfad"] if e["typ"] == "Befehl" else str(Path(e["pfad"]).parent)
        return {"deinstallieren": {"papierkorb": ziel}}
    a = {}
    if not e.get("plugin"):
        if e.get("update"):
            a["aktualisieren"] = {"argv": ["npx", "-y", harness.SKILLS_CLI, "update", e["name"], "-g", "-y"]}
        if e["quelle"]["art"] == "skills-CLI":
            # npx skills remove --global entfernt den Skill für alle Harnesses, die ihn lesen
            a["deinstallieren"] = {"argv": ["npx", "-y", harness.SKILLS_CLI, "remove", "--global", "-y", e["name"]], "gemeinsam": True}
        else:
            ziel = e["pfad"] if e["typ"] == "Befehl" else str(Path(e["pfad"]).parent)
            schritt = {"papierkorb": ziel}
            if Path(ziel).resolve().is_relative_to((AGENTS / "skills").resolve()):
                schritt["gemeinsam"] = True
            a["deinstallieren"] = schritt
    if e["override"] != "off":
        a["deaktivieren"] = {"override": "off"}
    if e["aufruf"] != "nur /":
        a["nur manuell"] = {"override": "user-invocable-only"}
    # Die Gegen-Aktionen nehmen den Override weg. Nur wo er der Grund ist: disable-model-invocation steht in der Datei des Skills.
    if e["override"] == "off":
        a["einschalten"] = {"override": None}
    if e["override"] == "user-invocable-only":
        a["wieder automatisch"] = {"override": None}
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
        # nie aus der Bewertung: Sie stammt aus einem Lauf, der fremde Skill-Texte liest, und der Befehl steht neben dem Kopierknopf
        e["befehl"] = befehl_fuer(e, (b or {}).get("empfehlung"))


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
    """Tokens, die in jeder Sitzung laden. Projekt-Skills und Projekt-Server gelten nur in ihrem Projekt.
    Ohne die eingebauten Skills des Harness: Codex legt sie als Dateien ab und könnte sie messen, Claude Code
    spielt sie zur Laufzeit ein. Gezählt wird nur, was beide sehen — sonst hieße die Kachel in beiden
    Dashboards gleich und meinte Verschiedenes, und die beiden Verläufe wären nicht vergleichbar."""
    return sum(e.get("tokens") or 0 for art in ("skills", "hooks", "mcp") for e in inhalt.get(art, []))


def rechte(h, inhalt):
    """Der Tab „Berechtigungen“ des jeweiligen Harness. Verwaist ist eine MCP-Regel, deren Server nicht mehr eingetragen ist."""
    if h == "codex":
        import codex
        return rechte_aktionen(berechtigungen.codex(codex.lies_toml(codex.CODEX / "config.toml"), codex.CODEX / "rules", HOME))
    praefixe = {werkzeug_praefix(e.get("schluessel", e["name"])) for e in inhalt.get("mcp", []) + inhalt.get("projekt_mcp", [])}
    return rechte_aktionen(berechtigungen.claude(HOME, (CFG.get("projects") or {}).keys(), praefixe))


def rechte_aktionen(zeilen):
    """Was markiert ist, lässt sich im Dashboard entfernen: die Regel aus ihrer Datei, unter Codex der verwaiste
    vertraute Ordner aus config.toml. Nur Regeln aus allow, ask und deny: Ein Modus oder Ordner steht nicht in einer
    Liste, verwaltete Einstellungen gehören dem Administrator, und die Regeln in ~/.codex/rules schreibt Codex selbst.
    Die ID nennt die Stelle selbst, keine Nummer: Nach einem neuen Lauf träfe ein Klick sonst die Nachbarzeile."""
    for z in zeilen:
        if not z["marken"] or z.get("verwaltet"):
            continue
        if z["art"] == "Vertrauen":
            z.update(id=f"recht:Vertrauen:{z['bereich']}", aktionen={"entfernen": {"toml_tabelle_entfernen": ["projects", z["bereich"]]}})
        elif z.get("datei") and z["art"] in ("allow", "ask", "deny"):
            z.update(id=f"recht:{z['datei']}:{z['art']}:{z['regel']}",
                     aktionen={"entfernen": {"regel_entfernen": {"datei": z["datei"], "art": z["art"], "regel": z["regel"]}}})
    return zeilen


def modelle_lesen(h, inhalt):
    """Der Tab „Modelle“ des jeweiligen Harness: was eingestellt ist und was lief, im selben Zeitfenster wie die Hooks."""
    if h == "codex":
        import codex
        grenze = datetime.now().timestamp() - HOOK_TAGE * 86400
        frisch = [d for d in codex.sitzungen() if d.is_file() and d.stat().st_mtime >= grenze]
        ergebnis = modelle.codex(codex.lies_toml(codex.CODEX / "config.toml"), frisch, zeitgrenze(), HOME)
    else:
        dateien = [{"bereich": e.get("projekt") or "global", "eintrag": f"{e.get('typ', 'Skill')} {e['name']}", "pfad": e["pfad"],
                    "kopf": frontmatter(e["pfad"])} for e in inhalt.get("skills", []) + inhalt.get("projekt_skills", []) if e.get("pfad")]
        ergebnis = modelle.claude(HOME, (CFG.get("projects") or {}).keys(), dateien, transkripte(), zeitgrenze())
    return {**ergebnis, "tage": HOOK_TAGE}


def verlauf_fortschreiben(pfad, eintrag):
    """Eine Zeile je Tag: Mehrere Läufe am selben Tag überschreiben sich, sonst wüchse die Datei bei jedem Öffnen.
    Die IDs (`ids`) bleiben nur heute und beim letzten Lauf davor: Mehr braucht aenderungen() nicht."""
    try:
        reihe = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        reihe = []
    if not isinstance(reihe, list):
        reihe = []
    reihe = [e for e in reihe if isinstance(e, dict) and isinstance(e.get("datum"), str) and e["datum"] != eintrag["datum"]] + [eintrag]
    reihe.sort(key=lambda e: e["datum"])
    behalten = {eintrag["datum"], max((e["datum"] for e in reihe if e["datum"] < eintrag["datum"]), default=None)}
    reihe = [e if e["datum"] in behalten else {k: v for k, v in e.items() if k != "ids"} for e in reihe]
    harness.schreiben(pfad, json.dumps(reihe, ensure_ascii=False, indent=1))
    return reihe


ID_ARTEN = ("mcp", "plugins", "skills", "hooks", "projekt_skills", "projekt_mcp")


def aenderungen(reihe):
    """Was seit dem letzten Lauf vor heute neu oder weg ist, je Art. Verglichen wird mit dem Tag davor, weil sich
    Läufe am selben Tag überschreiben. None ohne Vergleich (erster Lauf, alter Verlauf ohne IDs); eine Art, die der
    alte Stand nicht kennt, zählt nicht, sonst stünde sie ganz als „neu“ da."""
    if len(reihe) < 2 or not isinstance(reihe[-2].get("ids"), dict) or not isinstance(reihe[-1].get("ids"), dict):
        return None
    vorher, jetzt = reihe[-2]["ids"], reihe[-1]["ids"]
    neu, weg = {}, {}
    for art, ids in jetzt.items():
        if not isinstance(vorher.get(art), list):
            continue
        alt = {i for i in vorher[art] if isinstance(i, str)}  # die Datei ist von Hand änderbar
        if dazu := sorted(set(ids) - alt):
            neu[art] = dazu
        if fort := sorted(alt - set(ids)):
            weg[art] = fort
    return {"seit": reihe[-2]["datum"], "neu": neu, "entfernt": weg}


def ungenutzt_markieren(skills, plugins, heute):
    """Markiert, was in jede Sitzung lädt, aber seit UNGENUTZT_TAGE nicht genutzt wurde: die besten Kandidaten zum
    Aufräumen. Wer erst seit Kurzem da ist, hatte die Zeit nicht; für Plugin-Bestandteile zählt die Installation
    des Plugins, und wo die fehlt (Codex), der Stand seines Ordners."""
    grenze = (heute - timedelta(days=UNGENUTZT_TAGE)).strftime("%Y-%m-%d")
    seit = {f"{p['name']}@{p['marktplatz']}": p.get("installiert") or p.get("aktualisiert") for p in plugins}
    for e in skills:
        genutzt = (e.get("nutzung") or {}).get("zuletzt") or ""
        da_seit = (seit.get(e["plugin"]) if e.get("plugin") else e.get("installiert")) or ""
        if e.get("im_kontext") and e.get("tokens") and genutzt < grenze and da_seit < grenze:
            e["ungenutzt"] = True


def eindeutige_ids(alle):
    """Ein Skill und ein Befehl gleichen Namens hießen beide skill:x, ein Projektserver in .mcp.json und lokal beide
    mcp:Projekt/x — der Server nähme bei einer Aktion den ersten. Der erste behält die ID, die übrigen tragen ihre Art."""
    gruppen = {}
    for e in alle:
        gruppen.setdefault(e["id"], []).append(e)
    for gruppe in gruppen.values():
        for e in gruppe[1:]:
            e["id"] += "~" + (e.get("art") or e["typ"]).lower()


def eingebaute(bewertung):
    """Die eingebauten Skills aus der Bewertung, nur mit den Feldern, die das Dashboard braucht. Die Bewertung stammt
    aus einem Lauf, der fremde Skill-Texte liest; alles Weitere landete sonst ungeprüft auf der Seite mit dem Token."""
    return [{"name": b["name"], "was": b.get("was") if isinstance(b.get("was"), str) else "",
             "tokens": b["tokens"] if isinstance(b.get("tokens"), int) else 0, "nutzung": nutzung(b["name"])}
            for b in bewertung.get("eingebaut") or [] if isinstance(b, dict) and isinstance(b.get("name"), str)]


def fuers_dashboard(inhalt):
    """Gruppen, Hinweise und Stand kommen ungeprüft aus der Bewertung. Das Template stürzte ab, wenn `mitglieder` fehlte
    oder `stand` keine Zeichenkette war, und zeigte dann nicht einmal den Knopf „neu bewerten“."""
    gruppen = inhalt.get("gruppen") if isinstance(inhalt.get("gruppen"), list) else []
    hinweise = inhalt.get("hinweise") if isinstance(inhalt.get("hinweise"), list) else []
    stand = inhalt.get("bewertung_stand")
    return {**inhalt,
            "gruppen": [{**g, "mitglieder": [m for m in g["mitglieder"] if isinstance(m, str)]}
                        for g in gruppen if isinstance(g, dict) and isinstance(g.get("mitglieder"), list)],
            "hinweise": [h for h in hinweise if isinstance(h, dict) and isinstance(h.get("titel"), str) and isinstance(h.get("text"), str)],
            "bewertung_stand": stand if isinstance(stand, str) else None}


def sammelordner(projekte):
    """Eingetragene Ordner, in denen andere eingetragene Projekte liegen und die selbst kein Git-Repo sind: ~ oder ein
    Ordner voller Projekte. Claude Code trägt jeden Ordner ein, in dem einmal eine Sitzung lief; der Tab „Projekte“
    lässt diese weg. Ein Repo mit eingetragenem Unterordner bleibt ein Projekt."""
    projekte = [str(p) for p in projekte]
    return sorted(p for p in projekte if not (Path(p) / ".git").exists()
                  and any(q.startswith(p.rstrip("/") + "/") for q in projekte))


def claude_inventar():
    plugin_liste, plugin_teile, plugin_hooks = plugins()
    plugin_updates_markieren(plugin_liste)
    skills = persoenliche_skills() + plugin_teile
    updates_markieren(skills, LOCK)
    projekte = projekt_skills((CFG.get("projects") or {}).keys())
    hooks = eigene_hooks(SETTINGS, CLAUDE / "settings.json") + plugin_hooks + ordner_plugins()
    dateien = transkripte()
    messen(hooks, *hook_ausgaben(dateien))
    for p in plugin_liste:
        p["tokens"] += sum(h["tokens"] for h in hooks if h.get("plugin") == f"{p['name']}@{p['marktplatz']}")
    live = mcp_live()
    # erst die Server, dann die Messung: Welcher Server ein Werkzeug anbietet, lässt sich nur an ihren Namen ablesen
    praefixe = [werkzeug_praefix(s.get("schluessel", s["name"])) for s in mcp_server(plugin_liste, live)]
    speicher = werkzeuge.Zwischenspeicher(DATEN, datetime.now().date())
    mcp = mcp_server(plugin_liste, live, mcp_messungen(dateien, praefixe), lambda c: mcp_direkt.gemessen(c, speicher))
    speicher.sichern()
    mcp_projekte = projekt_mcp(CFG.get("projects") or {})
    alle = mcp + plugin_liste + skills + projekte + mcp_projekte + hooks
    eindeutige_ids(alle)
    bewerte(alle)
    for e in alle:
        e["aktionen"] = aktionen(e)

    eingebaut = eingebaute(BEWERTUNG)

    unbewertet, entfallen = abgleich(alle, BEWERTUNG)
    # Overrides gelten nach Namen, auch für Projekt- und eingebaute Skills
    verwaist = sorted(set(OVERRIDES) - {e.get("schluessel", e["name"]) for e in skills + projekte + eingebaut})
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
        "sammelordner": sammelordner((CFG.get("projects") or {}).keys()),
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
    LESEFEHLER.clear()
    DATEN.mkdir(parents=True, exist_ok=True)
    DATEN.chmod(0o700)  # Pfade und MCP-Ziele gehen andere Benutzer des Rechners nichts an
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
    inhalt = fuers_dashboard(inhalt)
    jetzt = datetime.now()
    ungenutzt_markieren(inhalt.get("skills", []), inhalt.get("plugins", []), jetzt)
    try:
        inhalt["werkzeuge"] = werkzeuge.pruefen(inhalt, h, DATEN, jetzt.date())
    except Exception as fehler:
        lesefehler("Werkzeuge", fehler)
        inhalt["werkzeuge"] = []
    try:
        inhalt["quellcode"] = werkzeuge.quellcode_pruefen(inhalt, DATEN, jetzt.date())
    except Exception as fehler:
        lesefehler("MCP-Server aus Quellcode", fehler)
        inhalt["quellcode"] = []
    try:
        inhalt["berechtigungen"] = rechte(h, inhalt)
    except Exception as fehler:
        lesefehler("Berechtigungen", fehler)
        inhalt["berechtigungen"] = []
    try:
        inhalt["modelle"] = modelle_lesen(h, inhalt)
    except Exception as fehler:
        lesefehler("Modelle", fehler)
        inhalt["modelle"] = {"eingestellt": [], "gemessen": [], "tage": HOOK_TAGE}
    inhalt.update(lesefehler=list(LESEFEHLER), ungenutzt_tage=UNGENUTZT_TAGE, ruhend_tage=werkzeuge.RUHEND_TAGE)
    last = kontextlast(inhalt)
    verlauf = verlauf_fortschreiben(DATEN / angaben["verlauf"], {
        "datum": jetzt.strftime("%Y-%m-%d"), "kontextlast": last,
        **{art: len(inhalt.get(art, [])) for art in ("mcp", "plugins", "skills", "hooks")},
        "ids": {art: sorted(e["id"] for e in inhalt.get(art, [])) for art in ID_ARTEN}})
    # ins Dashboard nur, was sich geändert hat: Die IDs zweier Tage wären dort ein paar Kilobyte ohne Nutzen
    daten = {"erstellt": jetzt.strftime("%Y-%m-%d %H:%M"), "harness": h, "harness_name": angaben["name"],
             "version": version(angaben["cli"]), "kontextlast": last,
             "verlauf": [{k: v for k, v in e.items() if k != "ids"} for e in verlauf], "aenderungen": aenderungen(verlauf),
             "bewertung_erstellt": datei_stand(DATEN / angaben["bewertung"]), **inhalt}
    harness.schreiben(DATEN / angaben["inventar"], json.dumps(daten, ensure_ascii=False, indent=1))

    # jedes < escapen, nicht nur </: Mit <!--<script aus einem Namen beendete das folgende </script> das Skript nicht mehr
    einbettung = json.dumps(daten, ensure_ascii=False).replace("<", "\\u003c")
    vorlage = ((HIER / "template.html").read_text(encoding="utf-8")
               .replace("__LOGO__", data_uri(LOGO.read_bytes(), "image/svg+xml"))
               .replace("__ICON__", data_uri(ICON.read_bytes(), "image/svg+xml"))
               .replace("__APPICON__", data_uri(APP_ICON.read_bytes(), "image/png")))
    harness.schreiben(DATEN / angaben["dashboard"], vorlage.replace("__DATEN__", einbettung))

    print(f"{angaben['dashboard']} geschrieben ({angaben['name']}): {len(daten['mcp'])} MCP-Server, {len(daten['plugins'])} Plugins, "
          f"{len(daten['skills'])} Skills/Befehle/Agenten, {len(daten['projekt_skills'])} Projekt-Skills, "
          f"{len(daten['projekt_mcp'])} Projekt-MCP-Server, {len(daten['hooks'])} Hooks")
    if daten["live_status"] is False:
        print("Hinweis: `claude mcp list` nicht erreichbar, MCP-Status nur aus der Konfiguration.")
    for f in daten["lesefehler"]:
        print(f"Übersprungen, {f['quelle']}: {f['fehler']}")
    if daten["unbewertet"]:
        print(f"Noch nicht bewertet ({len(daten['unbewertet'])}): " + ", ".join(daten["unbewertet"]))
    if daten["entfallen"]:
        print(f"Bewertet, aber nicht mehr installiert ({len(daten['entfallen'])}): " + ", ".join(daten["entfallen"]))
    updates = [e["name"] for e in daten["plugins"] + daten["skills"] if e.get("update")]
    if updates:
        print(f"Update verfügbar ({len(updates)}): " + ", ".join(updates))


if __name__ == "__main__":
    main()
