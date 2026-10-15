#!/usr/bin/env python3
"""Der Bestand von Claude Code für die Harness-Inventur.

Liest MCP-Server, Plugins, Skills und Hooks aus ~/.claude, ~/.claude.json und ~/.agents (mit Live-Status über
`claude mcp list`) in die Datenform, die template.html erwartet, und wird von build.py aufgerufen. Das Gegenstück
für Codex ist codex.py. Die fachliche Bewertung (bewertung-claude-code.json im Datenordner) pflegt der Skill
/3zero-harness-inventur, dieses Modul liest sie nur.
"""

import base64
import json
import os
import re
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import harness
import mcp_direkt
import werkzeuge
from bestand import (MESS_TAGE, abgleich, bewerte, frisch, frontmatter, hook_eintraege, lesefehler, lies_json, mehrfache_namen, netz,
                     papierkorb_befehl, projekt_ids, shellwort, updates_markieren, verdecken, werkzeug_praefix, zeitgrenze, ziel_text)

HOME = Path.home()
CLAUDE = HOME / ".claude"
AGENTS = HOME / ".agents"  # dorthin legt `npx skills` Skills; Codex liest denselben Ordner
CODEX = HOME / ".codex"  # nur für die Frage, ob ein Skill auch in Codex lädt
PLUGINS = CLAUDE / "plugins"
HIER = Path(__file__).resolve().parent

# Hier steht bewusst nichts Gelesenes: main() in build.py liest ~/.claude.json (`cfg`), settings.json (`settings`), die Bewertung
# und das Lock von npx skills einmal je Lauf und gibt sie weiter. Als Modulwerte lasen 13 Funktionen sie heimlich mit,
# und ein Test, der nur SETTINGS ersetzte, bekam weiter das GESPERRT des echten Rechners.


def gesperrte_namen(settings):
    """Server, die deniedMcpServers per Name in allen Projekten sperrt."""
    return {e.get("serverName") for e in settings.get("deniedMcpServers", []) if isinstance(e, dict)} - {None}


def datum_ms(ms):
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d") if ms else None


def nutzung(cfg, anzeige):
    n = cfg.get("skillUsage", {}).get(anzeige)
    if not n:
        return None
    return {"anzahl": n.get("usageCount", 0), "zuletzt": datum_ms(n.get("lastUsedAt"))}


def skill_eintrag(cfg, settings, anzeige, typ, datei, plugin_aktiv=True, schluessel=None):
    """Ein Skill, Befehl oder Agent. `anzeige` ist der Name im Dashboard, `schluessel` der, unter dem Claude Code
    ihn führt — bei gleichnamigen Plugins trägt nur die Anzeige den Marktplatz, Nutzung und Overrides nicht."""
    schluessel = schluessel or anzeige
    fm = frontmatter(datei)
    beschreibung = fm.get("description") if isinstance(fm.get("description"), str) else ""
    override = settings.get("skillOverrides", {}).get(schluessel, "on")
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
        "nutzung": nutzung(cfg, schluessel),
        "pfad": str(datei),
        "meta_autor": meta.get("author"),
        # nur wenn er abweicht: Er sagt, unter welchem Namen skillOverrides und skillUsage den Eintrag führen
        **({"schluessel": schluessel} if schluessel != anzeige else {}),
    }


def autoren(bewertung):
    """Anzeigenamen aus der Bewertung; sie stammt aus einem Lauf und kann alles Mögliche enthalten."""
    a = bewertung.get("autoren")
    return a if isinstance(a, dict) else {}


def cli_quelle(repo, autoren):
    besitzer = repo.split("/")[0]
    return {
        "art": "skills-CLI",
        "autor": autoren.get(besitzer, besitzer),
        "repo": repo,
        "url": f"https://github.com/{repo}",
    }


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


def persoenliche_skills(cfg, settings, lock, autoren):
    ergebnis = []
    if not (CLAUDE / "skills").is_dir():  # auf einem frischen Rechner gibt es den Ordner noch nicht
        return ergebnis
    for ordner in sorted((CLAUDE / "skills").iterdir()):
        datei = ordner / "SKILL.md"
        if not datei.is_file():
            continue
        e = skill_eintrag(cfg, settings, ordner.name, "Skill", datei)
        # Codex liest ~/.agents/skills selbst; dieselbe Datei kann auch unter ~/.codex/skills liegen
        verweis = CODEX / "skills" / ordner.name / "SKILL.md"
        if datei.resolve().is_relative_to((AGENTS / "skills").resolve()) or (verweis.is_file() and verweis.resolve() == datei.resolve()):
            e["geteilt"] = True
        installation = lock.get(ordner.name)
        if installation:
            pfad = installation.get("skillPath", "")
            e["quelle"] = cli_quelle(installation.get("source", ""), autoren)
            e["installiert"] = (installation.get("installedAt") or "")[:10] or None
            if "/deprecated/" in pfad:
                e["reife"] = "upstream veraltet"
            elif "/in-progress/" in pfad:
                e["reife"] = "upstream unfertig"
        else:
            # Ein Symlink zeigt auf ein Repo, in dem der Skill gepflegt wird; ein Ordner ist eine Kopie
            e["quelle"] = {"art": "Symlink" if ordner.is_symlink() else "lokal", "autor": e["meta_autor"] or "unbekannt"}
        ergebnis.append(e)
    for datei in sorted((CLAUDE / "commands").glob("*.md")):
        e = skill_eintrag(cfg, settings, datei.stem, "Befehl", datei)
        e["quelle"] = {"art": "Symlink" if datei.is_symlink() else "lokal", "autor": "unbekannt"}
        ergebnis.append(e)
    return ergebnis


def projekt_skills(cfg, settings, autoren, projekte):
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
                e = skill_eintrag(cfg, settings, name, typ, datei)
                e["projekt"] = str(pfad)
                repo = (lock.get(name) or {}).get("source") if typ == "Skill" else None
                e["quelle"] = cli_quelle(repo, autoren) if repo else {"art": "lokal", "autor": e["meta_autor"] or "unbekannt"}
                eintraege.append(e)
        except Exception as fehler:
            lesefehler(f"Projekt {pfad}", fehler)
            continue
        ergebnis += eintraege
    projekt_ids(ergebnis, "skill")
    return ergebnis


MOD_AUFRUF = re.compile(r"""\bon\(\s*(['"`])([\w.*-]+)\1""")
# Was ein Mod anzeigt, wo er eingreift und was er darf, abgelesen an den Ereignissen und Aufrufen, die
# `claude plugin validate` meldet. Die Namen stehen in den Typen von Claude Code (Skill plugin-authoring).
MOD_ZEIGT = {"Pane": "Panel", "AbovePrompt": "Leiste über dem Prompt", "PromptHint": "Hinweis am Prompt",
             "$.ui.open": "Panel", "$.ui.status": "Statuszeile", "$.ui.toast": "Einblendung", "$.ui.notify": "Mitteilung",
             "$.tool.register": "Werkzeug für das Modell", "$.agent.register": "Subagent-Typ", "$.audio.play": "Ton"}
MOD_GREIFT_EIN = {"tool.call": "Werkzeug-Aufrufe", "tool.check": "Freigaben", "prompt.submit": "Prompt",
                  "prompt.compose": "System-Prompt", "prompt.mention": "Erwähnungen", "config.set": "Einstellungen"}
MOD_DARF = {"$.fs.write": "Dateien schreiben", "$.http.fetch": "Netz", "$.process.run": "Programme starten",
            "$.process.spawn": "Programme starten", "$.model.complete": "Modell aufrufen", "$.model.fork": "Modell aufrufen",
            "$.model.classify": "Modell aufrufen", "$.agent.spawn": "Subagenten starten", "$.mcp.call": "MCP-Server aufrufen",
            "$.mcp.connect": "MCP-Server aufrufen", "$.settings.set": "Einstellungen ändern", "$.config.set": "Einstellungen ändern",
            "$.env.set": "Umgebung ändern", "$.prompt.submit": "Prompts absenden"}
MOD_BEFEHL = re.compile(r"command\.run\{command=([^}]+)\}")


def liste_aus(text):
    """`a, b{x=1, y=2}, c` in seine Teile; ein Komma in geschweiften Klammern trennt nicht."""
    teile, tiefe, aktuell = [], 0, ""
    for zeichen in text:
        tiefe += {"{": 1, "}": -1}.get(zeichen, 0)
        if zeichen == "," and tiefe == 0:
            teile.append(aktuell.strip())
            aktuell = ""
        else:
            aktuell += zeichen
    return [t for t in teile + [aktuell.strip()] if t]


def mod_bericht(wurzel):
    """Was `claude plugin validate` über die Module eines Plugins meldet: Ereignisse, Aufrufe auf `$` und Wächter
    ohne `.catch`. Liest den Quelltext wie Claude Code selbst, auch in importierten Dateien, und führt nichts aus.
    None ohne `claude` oder ohne lesbaren Bericht; dann bleibt die Textsuche."""
    try:
        r = subprocess.run(["claude", "plugin", "validate", "--json", str(wurzel)], capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        inhalt = next(c for c in json.loads(r.stdout)["contents"] if c.get("type") == "hooks")
    except (OSError, subprocess.TimeoutExpired, ValueError, LookupError, TypeError, StopIteration, AttributeError):
        return None
    bericht = {"ereignisse": [], "aufrufe": [], "waechter": []}
    for notiz in inhalt.get("notes") or []:
        if not isinstance(notiz, str):
            continue
        for feld, marke in (("ereignisse", " hooks: "), ("aufrufe", " calls: ")):
            if marke in notiz:
                for teil in liste_aus(notiz.split(marke, 1)[1]):
                    teil = re.sub(r" \(via [^)]*\)$", "", teil)
                    if teil not in bericht[feld]:
                        bericht[feld].append(teil)
    bericht["waechter"] = [w["hook"] for w in inhalt.get("gatingHooks") or []
                           if isinstance(w, dict) and isinstance(w.get("hook"), str) and not w.get("hasCatch")]
    return bericht if bericht["ereignisse"] or bericht["aufrufe"] else None


def mod_eintrag(hooks_datei, wurzel, schluessel, name, quelle, status="aktiv"):
    """Ein Mod: die Hook-Module, die hooks/hooks.json unter `modules` nennt. Sie laufen in jeder Sitzung mit, können
    Panels und Befehle hinzufügen, Werkzeug-Aufrufe und Prompt ändern, Dateien schreiben und Programme starten.
    Ein Eintrag je Plugin oder Ordner; None ohne Modul. Was er tut, meldet mod_bericht(); ohne Bericht bleiben die
    Ereignisse aus `on('…'` im Text der genannten Dateien — gelesen, nie ausgeführt, nur innerhalb des Plugins."""
    module = lies_json(hooks_datei).get("modules")
    dateien = []
    for m in module if isinstance(module, list) else []:
        if not isinstance(m, str):
            continue
        datei = (hooks_datei.parent / m).resolve()
        # der Pfad kommt aus einer fremden Datei: nichts lesen, was aus dem Plugin herausführt
        if datei.is_relative_to(wurzel.resolve()) and datei.is_file():
            dateien.append(datei)
    if not dateien:
        return None
    bericht = mod_bericht(wurzel)
    if bericht is None:
        funde = [f[1] for d in dateien for f in MOD_AUFRUF.findall(d.read_text(encoding="utf-8", errors="replace"))]
        bericht = {"ereignisse": sorted(set(funde)), "aufrufe": [], "waechter": []}
        messung = "Textsuche"
    else:
        messung = "validate"
    ereignisse, aufrufe = bericht["ereignisse"], bericht["aufrufe"]
    komponenten = [m.group(1) for e in ereignisse if (m := re.match(r"ui\.render\{component=(\w+)", e))]
    zeigt = [MOD_ZEIGT.get(k, k) for k in komponenten] + [MOD_ZEIGT[a] for a in aufrufe if a in MOD_ZEIGT]
    befehle = [b for e in ereignisse if (m := MOD_BEFEHL.fullmatch(e)) for b in m.group(1).split("|")]
    beschreibung = lies_json(wurzel / ".claude-plugin" / "plugin.json").get("description")
    return {"id": f"mod:{schluessel}", "name": name, "typ": "Mod", "status": status, "quelle": dict(quelle),
            "beschreibung": beschreibung if isinstance(beschreibung, str) else "",
            "pfad": str(dateien[0]), "module": [str(d.relative_to(wurzel.resolve())) for d in dateien], "messung": messung,
            "ereignisse": ereignisse, "aufrufe": aufrufe, "waechter": bericht["waechter"],
            "zeigt": list(dict.fromkeys(zeigt)), "befehle": befehle,
            "greift_ein": list(dict.fromkeys(MOD_GREIFT_EIN[e.split("{")[0]] for e in ereignisse if e.split("{")[0] in MOD_GREIFT_EIN)),
            "darf": list(dict.fromkeys(MOD_DARF[a] for a in aufrufe if a in MOD_DARF)), "tokens": 0}


PLUGIN_ORDNER = "CLAUDE_CODE_PLUGIN_DIRS"


def unterordner(pfad):
    try:
        return sorted(p for p in pfad.iterdir() if p.is_dir())
    except OSError:
        return []


def ordner_hooks(art, wurzel, status):
    """(Hooks, Mods) eines Plugins, das als Ordner lädt; ohne .claude-plugin/plugin.json ist es keins."""
    if not (wurzel / ".claude-plugin" / "plugin.json").is_file():
        return [], []
    quelle = {"art": art, "autor": f"{art} {wurzel.name}"}
    datei = wurzel / "hooks" / "hooks.json"
    hooks = hook_eintraege(lies_json(datei).get("hooks"), wurzel, quelle, datei, status)
    mod = mod_eintrag(datei, wurzel, wurzel, wurzel.name, quelle, status)
    return [{**e, "ordner": str(wurzel)} for e in hooks], [{**mod, "ordner": str(wurzel)}] if mod else []


def ordner_plugins(settings):
    """Hooks und Mods aus Plugins, die ohne Installation aus einem Ordner laden: Skill-Ordner mit eigener plugin.json,
    die Ordner aus CLAUDE_CODE_PLUGIN_DIRS (Umgebung oder `env` in settings.json) und die Mods aus
    ~/.claude/dev-mods/<Sitzung>, die nur in ihrer Sitzung laden. Die ID trägt den Ordner, `ordner` sagt `aktionen()`
    und `befehl()`, dass der Eintrag weder zu einem Plugin noch in settings.json gehört. Gibt (Hooks, Mods) zurück."""
    env = settings.get("env")
    eingetragen = [os.environ.get(PLUGIN_ORDNER), env.get(PLUGIN_ORDNER) if isinstance(env, dict) else None]
    orte = [("Skill-Ordner", o, "aktiv") for o in unterordner(CLAUDE / "skills")]
    orte += [("Plugin-Ordner", Path(p).expanduser(), "aktiv")
             for liste in eingetragen if isinstance(liste, str) for p in liste.split(os.pathsep) if p]
    orte += [("Mod-Ordner", o, "nur eine Sitzung") for sitzung in unterordner(CLAUDE / "dev-mods") for o in unterordner(sitzung)]
    hooks, mods, gesehen = [], [], set()
    for art, wurzel, status in orte:
        try:
            if wurzel.resolve() in gesehen:
                continue
            gesehen.add(wurzel.resolve())
            h, m = ordner_hooks(art, wurzel, status)
        except Exception as fehler:
            lesefehler(f"{art} {wurzel}", fehler)
            continue
        hooks += h
        mods += m
    return hooks, mods


def projekt_hooks(projekte):
    """Hooks und Mods, die nur in einem Projekt laufen: `hooks` aus .claude/settings.json und .claude/settings.local.json
    und Skill-Ordner mit eigener plugin.json unter .claude/skills. Nur Anzeige im Tab „Projekte“, ohne Bewertung und
    Aktion: Ein Hook in den Einstellungen eines Projekts gehört oft zum Repo. Die ID trägt Datei oder Ordner.
    Gibt (Hooks, Mods) zurück."""
    hooks, mods, gesehen = [], [], {CLAUDE.resolve()}
    for pfad in sorted(projekte):
        wurzel = Path(pfad) / ".claude"
        try:
            if not wurzel.is_dir() or wurzel.resolve() in gesehen:
                continue
            gesehen.add(wurzel.resolve())
        except OSError:  # etwa ~/Documents, das macOS ohne Freigabe verweigert: nur dieses Projekt fällt weg
            continue
        try:
            eintraege, projekt_mods = [], []
            for name in ("settings.json", "settings.local.json"):
                datei = wurzel / name
                eintraege += hook_eintraege(lies_json(datei).get("hooks"), datei, {"art": "Einstellungen", "autor": name}, datei)
            for ordner in unterordner(wurzel / "skills"):
                h, m = ordner_hooks("Skill-Ordner", ordner, "aktiv")
                eintraege += h
                projekt_mods += m
        except Exception as fehler:
            lesefehler(f"Projekt {pfad}", fehler)
            continue
        hooks += [{**e, "projekt": str(pfad)} for e in eintraege]
        mods += [{**e, "projekt": str(pfad)} for e in projekt_mods]
    return hooks, mods


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


def transkripte():
    # rglob: Hooks laufen auch in Subagenten, deren Transkripte liegen eine Ebene tiefer
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


def plugins(cfg, settings, autoren):
    """(Plugins, ihre Skills/Befehle/Agenten, ihre Hooks, ihre Mods)."""
    roh = lies_json(PLUGINS / "installed_plugins.json").get("plugins", {})
    # nur Installationen mit Pfad: Ein Eintrag ohne sie ist nicht installiert
    installiert = {pid: mit_pfad for pid, inst in roh.items() if (mit_pfad := [i for i in inst if i.get("installPath")])}
    aktiviert = settings.get("enabledPlugins", {})
    marktplaetze = lies_json(PLUGINS / "known_marketplaces.json")
    liste, bestandteile, hooks, mods = [], [], [], []
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
            autor = autoren.get(autor, autor)

            skill_dateien = plugin_skill_dateien(wurzel, mkt_eintrag, manifest)
            teile, plugin_hooks = [], []
            for d in skill_dateien:
                if d.is_file():
                    teile.append(skill_eintrag(cfg, settings, f"{praefix}:{d.parent.name}", "Skill", d, aktiv, f"{name}:{d.parent.name}"))
            for d in sorted(wurzel.glob("commands/*.md")):
                teile.append(skill_eintrag(cfg, settings, f"{praefix}:{d.stem}", "Befehl", d, aktiv, f"{name}:{d.stem}"))
            for d in sorted(wurzel.glob("agents/*.md")):
                teile.append(skill_eintrag(cfg, settings, f"{praefix}:{d.stem}", "Agent", d, aktiv, f"{name}:{d.stem}"))
            for t in teile:
                t["plugin"] = pid
                t["quelle"] = {"art": "Plugin", "autor": autor, "repo": pid}
            hooks_datei = wurzel / "hooks" / "hooks.json"
            hooks_roh = lies_json(hooks_datei).get("hooks")
            hooks_roh = hooks_roh if isinstance(hooks_roh, dict) else {}
            for h in hook_eintraege(hooks_roh, pid, {"art": "Plugin", "autor": autor, "repo": pid}, hooks_datei, "aktiv" if aktiv else "aus"):
                plugin_hooks.append({**h, "plugin": pid})
            mod = mod_eintrag(hooks_datei, wurzel, pid, name, {"art": "Plugin", "autor": autor, "repo": pid}, "aktiv" if aktiv else "aus")

            genutzt = [t["nutzung"] for t in teile if t["nutzung"]]
            pnutzung = cfg.get("pluginUsage", {}).get(pid, {})
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
                "mods": mod["ereignisse"] if mod else [],
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
        mods += [{**mod, "plugin": pid}] if mod else []
    return liste, bestandteile, hooks, mods


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


def mcp_server(cfg, gesperrt, plugin_liste, live, messungen=None, direkt=None):
    """`direkt` fragt einen Server anhand seiner Konfiguration selbst (mcp_direkt.gemessen); ihre Werte gehen denen aus
    den Transkripten vor, weil sie den heutigen Stand zeigen."""
    server, roh = {}, {}
    for name in cfg.get("claudeAiMcpEverConnected", []):
        server[name] = {"art": "claude.ai-Connector", "quelle": "claude.ai-Konto", "ziel": ""}
    for name, c in (cfg.get("mcpServers") or {}).items():
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
    for pfad, p in (cfg.get("projects") or {}).items():
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
        if echt in gesperrt:
            status = "deaktiviert"
        # ohne Messung bleiben die Felder None: nicht gemessen ist etwas anderes als null Werkzeuge
        gemessen = (messungen or {}).get(werkzeug_praefix(echt))
        gemessen = {**gemessen, "messung": "Transkript"} if gemessen else {"werkzeuge": None, "tokens": None, "nutzung": None}
        # gesperrt oder mit dem Plugin aus: lädt in keine Sitzung, die Inventur fragt ihn nicht
        d = direkt(roh[name]) if direkt and name in roh and status not in ("deaktiviert", "inaktiv (Plugin aus)") else None
        if d:
            gemessen = {**gemessen, **mcp_direkt.felder(d, werkzeug_praefix(echt))}
        ergebnis.append({"id": f"mcp:{name}", "name": name, "status": status, "gesperrt": echt in gesperrt,
                         "aus_in_projekten": aus_in.get(echt, []), **s, **gemessen,
                         "netz": netz(s["art"], roh.get(name), status, gemessen.get("messung"), (d or {}).get("anmeldung"))})
    return ergebnis


def projekt_mcp(gesperrt, projekte):
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
                elif name in gesperrt or name in (p.get("disabledMcpServers") or []):
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
                                 "ziel": ziel_text(c), "status": status, "gesperrt": name in gesperrt, "aus_in_projekten": []})
        except Exception as fehler:
            lesefehler(f"Projekt {pfad}", fehler)
            continue
        ergebnis += eintraege
    projekt_ids(ergebnis, "mcp")
    return ergebnis


def befehl(e, empfehlung):
    """Der Befehl, mit dem sich die Empfehlung umsetzen ließe."""
    eid = e["id"]
    if eid.startswith(("hook:", "mod:")) and e.get("ordner"):
        if e["quelle"]["art"] == "Plugin-Ordner":
            return f"{PLUGIN_ORDNER} → {e['ordner']} herausnehmen"
        # im Skill-Ordner nur die Hooks: Der Skill daneben soll bleiben
        return papierkorb_befehl(shellwort(e["ordner"] + ("/hooks" if e["quelle"]["art"] == "Skill-Ordner" else "")))
    if eid.startswith("hook:") and e.get("projekt"):
        return f'{e["pfad"]} → "hooks" → "{e["ereignis"]}" entfernen'
    if eid.startswith("hook:") and not e.get("plugin"):
        if e["ereignis"] == "Statuszeile":
            return '~/.claude/settings.json → "statusLine" entfernen'
        return f'~/.claude/settings.json → "hooks" → "{e["ereignis"]}" entfernen'
    if eid.startswith("mod:"):  # ein Mod kommt mit seinem Plugin und geht mit ihm
        pid = shellwort(e["plugin"])
        return f"claude plugin disable {pid}" if empfehlung == "abschalten" else f"claude plugin uninstall {pid}"
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
    if eid.startswith("mod:"):
        return {}  # kommt mit dem Plugin oder seinem Ordner und geht mit ihm
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


def umgesetzt(e):
    """Gilt die eigene Empfehlung des Eintrags schon? Das Gegenstück zu aktionen(): Was schon abgeschaltet oder auf
    „nur /“ gestellt ist, braucht keine Aktion mehr, steht nicht mehr unter den offenen Empfehlungen und spart nichts
    mehr. Entferntes steht gar nicht mehr im Bestand. Ein Plugin lädt nur mit dem Status „aktiv“."""
    b = e.get("bewertung")
    empfehlung = b.get("empfehlung") if isinstance(b, dict) else None
    if empfehlung == "abschalten":
        return (e.get("override") == "off" or e.get("gesperrt") is True or e.get("status") == "deaktiviert"
                or (e["id"].startswith("plugin:") and e.get("status") != "aktiv")
                or (e["id"].startswith("mod:") and bool(e.get("plugin")) and e.get("status") == "aus"))  # mit seinem Plugin aus
    return empfehlung == "nur manuell" and e.get("aufruf") == "nur /"


PRUEF_SKILL = "claude-md-management:claude-md-improver"


def pruefskill(inhalt):
    """Der Ordner des Skills claude-md-improver, wenn sein Plugin installiert ist und er lädt; sonst None. Mit ihm prüft
    das Dashboard eine CLAUDE.md (Schritt `pruefen` im Server)."""
    for e in inhalt.get("skills", []):
        if e.get("name") == PRUEF_SKILL and e.get("status") == "aktiv" and e.get("pfad"):
            return str(Path(e["pfad"]).parent)
    return None


def eindeutige_ids(alle):
    """Ein Skill und ein Befehl gleichen Namens hießen beide skill:x, ein Projektserver in .mcp.json und lokal beide
    mcp:Projekt/x — der Server nähme bei einer Aktion den ersten. Der erste behält die ID, die übrigen tragen ihre Art."""
    gruppen = {}
    for e in alle:
        gruppen.setdefault(e["id"], []).append(e)
    for gruppe in gruppen.values():
        for e in gruppe[1:]:
            e["id"] += "~" + (e.get("art") or e["typ"]).lower()


def eingebaute(cfg, bewertung):
    """Die eingebauten Skills aus der Bewertung, nur mit den Feldern, die das Dashboard braucht. Die Bewertung stammt
    aus einem Lauf, der fremde Skill-Texte liest; alles Weitere landete sonst ungeprüft auf der Seite mit dem Token."""
    return [{"name": b["name"], "was": b.get("was") if isinstance(b.get("was"), str) else "",
             "tokens": b["tokens"] if isinstance(b.get("tokens"), int) else 0, "nutzung": nutzung(cfg, b["name"])}
            for b in bewertung.get("eingebaut") or [] if isinstance(b, dict) and isinstance(b.get("name"), str)]


def fehlende_ordner(inhalt, projekte):
    """Eingetragene oder gemessene Projekte, deren Ordner es nicht mehr gibt. Ein gelöschtes Projekt steht noch im Tab
    „Projekte“, solange dort Tokens gemessen sind; die Zeile trägt dann „Ordner fehlt“, statt wie ein Projekt
    auszusehen, das es gibt. Ausblenden hieße, dass seine Tokens in der Summe fehlen."""
    gemessen = (inhalt.get("modelle") or {}).get("gemessen")
    pfade = set(projekte)
    for z in gemessen if isinstance(gemessen, list) else []:
        p = z.get("projekt") if isinstance(z, dict) else None
        if isinstance(p, str) and p.startswith(("~", "/")):
            pfade.add(str(HOME) + p[1:] if p.startswith("~") else p)
    fehlt = []
    for p in sorted(pfade):
        try:
            if not Path(p).is_dir():
                fehlt.append(p)
        except OSError:  # etwa ~/Documents ohne Freigabe: unbekannt ist nicht „fehlt“
            pass
    return fehlt


def sammelordner(projekte):
    """Eingetragene Ordner, in denen andere eingetragene Projekte liegen und die selbst kein Git-Repo sind: ~ oder ein
    Ordner voller Projekte. Claude Code trägt jeden Ordner ein, in dem einmal eine Sitzung lief; der Tab „Projekte“
    lässt diese weg. Ein Repo mit eingetragenem Unterordner bleibt ein Projekt."""
    projekte = [str(p) for p in projekte]
    return sorted(p for p in projekte if not (Path(p) / ".git").exists()
                  and any(q.startswith(p.rstrip("/") + "/") for q in projekte))


def inventar(cfg, settings, bewertung, lock):
    """Der Bestand von Claude Code. `cfg` ist ~/.claude.json, `settings` ~/.claude/settings.json, `bewertung` die Datei
    aus dem Datenordner, `lock` die Skills aus ~/.agents/.skill-lock.json: build.main() liest sie einmal und gibt sie mit."""
    namen, gesperrt, eingetragen = autoren(bewertung), gesperrte_namen(settings), cfg.get("projects") or {}
    plugin_liste, plugin_teile, plugin_hooks, plugin_mods = plugins(cfg, settings, namen)
    plugin_updates_markieren(plugin_liste)
    skills = persoenliche_skills(cfg, settings, lock, namen) + plugin_teile
    updates_markieren(skills, lock)
    projekte = projekt_skills(cfg, settings, namen, eingetragen.keys())
    ordner_hooks_, ordner_mods = ordner_plugins(settings)
    hooks = eigene_hooks(settings, CLAUDE / "settings.json") + plugin_hooks + ordner_hooks_
    mods = plugin_mods + ordner_mods
    dateien = transkripte()
    messen(hooks, *hook_ausgaben(dateien))
    for p in plugin_liste:
        p["tokens"] += sum(h["tokens"] for h in hooks if h.get("plugin") == f"{p['name']}@{p['marktplatz']}")
    live = mcp_live()
    # erst die Server, dann die Messung: Welcher Server ein Werkzeug anbietet, lässt sich nur an ihren Namen ablesen
    praefixe = [werkzeug_praefix(s.get("schluessel", s["name"])) for s in mcp_server(cfg, gesperrt, plugin_liste, live)]
    speicher = werkzeuge.Zwischenspeicher(harness.DATEN, datetime.now().date())
    mcp = mcp_server(cfg, gesperrt, plugin_liste, live, mcp_messungen(dateien, praefixe), lambda c: mcp_direkt.gemessen(c, speicher))
    speicher.sichern()
    mcp_projekte = projekt_mcp(gesperrt, eingetragen)
    hooks_projekte, mods_projekte = projekt_hooks(eingetragen.keys())
    for h in hooks_projekte + mods_projekte:  # nicht in `alle`: keine Bewertung, keine Aktion, nur der Text zum Kopieren
        h["befehl"] = befehl(h, "entfernen")
    alle = mcp + plugin_liste + skills + projekte + mcp_projekte + hooks + mods
    eindeutige_ids(alle)
    bewerte(alle, bewertung, befehl)
    for e in alle:
        e["aktionen"] = aktionen(e)
        e["umgesetzt"] = umgesetzt(e)

    eingebaut = eingebaute(cfg, bewertung)

    unbewertet, entfallen = abgleich(alle, bewertung)
    # Overrides gelten nach Namen, auch für Projekt- und eingebaute Skills
    verwaist = sorted(set(settings.get("skillOverrides", {})) - {e.get("schluessel", e["name"]) for e in skills + projekte + eingebaut})
    return {
        "live_status": live is not None,
        "bewertung_stand": bewertung.get("stand"),
        "overrides_verwaist": verwaist,
        "mcp": mcp,
        "plugins": plugin_liste,
        "skills": skills,
        "projekt_skills": projekte,
        "projekt_mcp": mcp_projekte,
        "projekt_hooks": hooks_projekte,
        "hooks": hooks,
        "mods": mods,
        "projekt_mods": mods_projekte,
        "mess_tage": MESS_TAGE,
        "sammelordner": sammelordner(eingetragen.keys()),
        "eingebaut": eingebaut,
        "gruppen": bewertung.get("gruppen", []),
        "hinweise": bewertung.get("hinweise", []),
        "unbewertet": unbewertet,
        "entfallen": entfallen,
    }
