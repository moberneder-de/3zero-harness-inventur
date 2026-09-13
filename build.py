#!/usr/bin/env python3
"""Harness-Inventur für Claude Code.

Liest MCP-Server, Plugins und Skills aus ~/.claude, ~/.claude.json und
~/.agents, holt den Live-Status der MCP-Server über `claude mcp list` und
schreibt dashboard.html sowie inventar.json.

Die fachliche Bewertung (Beschreibung auf Deutsch, Überschneidungen,
Empfehlungen) steht in bewertung.json. Die pflegt Claude über den Skill
/3zero-harness-inventur, dieses Skript liest sie nur.

Aufruf: python3 build.py
"""

import base64
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

HOME = Path.home()
CLAUDE = HOME / ".claude"
PLUGINS = CLAUDE / "plugins"
HIER = Path(__file__).resolve().parent
LOGO = HIER.parent / "3zero-Design-Kit" / "logo" / "wordmark.svg"
# Nur die Wortmarke: „IT- Consulting“ steht unterhalb von y=163 und fällt aus der viewBox
LOGO_MASSE = ('width="789" height="298" viewBox="21 29 789 298"', 'width="789" height="141" viewBox="21 29 789 141"')


def lies_json(pfad):
    try:
        return json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


CFG = lies_json(HOME / ".claude.json")
SETTINGS = lies_json(CLAUDE / "settings.json")
BEWERTUNG = lies_json(HIER / "bewertung.json")
NUTZUNG = CFG.get("skillUsage", {})
OVERRIDES = SETTINGS.get("skillOverrides", {})
LOCK = lies_json(HOME / ".agents" / ".skill-lock.json").get("skills", {})


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


def persoenliche_skills():
    ergebnis = []
    autoren = BEWERTUNG.get("autoren", {})
    for ordner in sorted((CLAUDE / "skills").iterdir()):
        datei = ordner / "SKILL.md"
        if not datei.is_file():
            continue
        e = skill_eintrag(ordner.name, "Skill", datei)
        lock = LOCK.get(ordner.name)
        if lock:
            repo = lock.get("source", "")
            besitzer = repo.split("/")[0]
            pfad = lock.get("skillPath", "")
            e["quelle"] = {
                "art": "skills-CLI",
                "autor": autoren.get(besitzer, besitzer),
                "repo": repo,
                "url": f"https://github.com/{repo}",
            }
            e["installiert"] = (lock.get("installedAt") or "")[:10] or None
            if "/deprecated/" in pfad:
                e["reife"] = "upstream veraltet"
            elif "/in-progress/" in pfad:
                e["reife"] = "upstream unfertig"
        else:
            e["quelle"] = {"art": "lokal", "autor": e["meta_autor"] or "unbekannt"}
        ergebnis.append(e)
    for datei in sorted((CLAUDE / "commands").glob("*.md")):
        e = skill_eintrag(datei.stem, "Befehl", datei)
        e["quelle"] = {"art": "lokal", "autor": "unbekannt"}
        ergebnis.append(e)
    return ergebnis


def mcp_definitionen(wurzel, manifest):
    roh = lies_json(wurzel / ".mcp.json")
    server = roh.get("mcpServers", roh)
    if isinstance(manifest.get("mcpServers"), dict):
        server = {**server, **manifest["mcpServers"]}
    return {n: c for n, c in server.items() if isinstance(c, dict)}


def plugins():
    installiert = lies_json(PLUGINS / "installed_plugins.json").get("plugins", {})
    aktiviert = SETTINGS.get("enabledPlugins", {})
    marktplaetze = lies_json(PLUGINS / "known_marketplaces.json")
    liste, bestandteile = [], []
    for pid, installationen in sorted(installiert.items()):
        inst = next((i for i in installationen if i.get("scope") == "user"), installationen[0])
        name, _, mkt = pid.partition("@")
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
                teile.append(skill_eintrag(f"{name}:{d.parent.name}", "Skill", d, aktiv))
        for d in sorted(wurzel.glob("commands/*.md")):
            teile.append(skill_eintrag(f"{name}:{d.stem}", "Befehl", d, aktiv))
        for d in sorted(wurzel.glob("agents/*.md")):
            teile.append(skill_eintrag(f"{name}:{d.stem}", "Agent", d, aktiv))
        for t in teile:
            t["plugin"] = pid
            t["quelle"] = {"art": "Plugin", "autor": autor, "repo": pid}
        bestandteile += teile

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
            "hooks": sorted(lies_json(wurzel / "hooks" / "hooks.json").get("hooks", {}).keys()),
            "mcp": mcp_definitionen(wurzel, manifest),
            "tokens": sum(t["tokens"] for t in teile),
            "nutzung": {
                "anzahl": sum(n["anzahl"] for n in genutzt),
                "zuletzt": max((n["zuletzt"] for n in genutzt if n["zuletzt"]), default=None),
            } if genutzt else None,
            "aktivitaet": pnutzung.get("usageCount"),
        })
    return liste, bestandteile


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


def mcp_server(plugin_liste, live):
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
        for name, c in (p.get("mcpServers") or {}).items():
            eintrag = server.setdefault(name, {"art": "Projekt", "quelle": pfad, "ziel": ziel_text(c)})
            eintrag.setdefault("projekte", []).append(pfad)
        for name in p.get("disabledMcpServers") or []:
            aus_in.setdefault(name, []).append(pfad)

    ergebnis = []
    for name, s in sorted(server.items()):
        if live is not None and name in live:
            status = zustand_deutsch(live[name]["zustand"])
            s["ziel"] = s["ziel"] or live[name]["ziel"]
        elif s.get("plugin_status") and s["plugin_status"] != "aktiv":
            status = "inaktiv (Plugin aus)"
        elif s["art"] == "Projekt":
            status = "nur im Projekt"
        else:
            status = "unbekannt"
        ergebnis.append({"id": f"mcp:{name}", "name": name, "status": status,
                         "aus_in_projekten": aus_in.get(name, []), **s})
    return ergebnis


def befehl(e, empfehlung):
    """Der Befehl, mit dem sich die Empfehlung umsetzen ließe."""
    eid = e["id"]
    if empfehlung in ("abschalten", "nur manuell"):
        wert = "off" if empfehlung == "abschalten" else "user-invocable-only"
        return f"~/.claude/settings.json → \"skillOverrides\": {{\"{e['name']}\": \"{wert}\"}}"
    if eid.startswith("plugin:"):
        return f"claude plugin uninstall {e['name']}@{e['marktplatz']}"
    if eid.startswith("mcp:"):
        if e["art"] == "claude.ai-Connector":
            return "claude.ai → Einstellungen → Connectors → trennen"
        if e["art"] == "Plugin":
            return f"claude plugin uninstall {e['quelle']}"
        if e["art"] == "User":
            return f"claude mcp remove \"{e['name']}\" -s user"
        return f"cd \"{e['quelle']}\" && claude mcp remove \"{e['name']}\" -s local"
    if e.get("plugin"):
        return f"claude plugin uninstall {e['plugin']}"
    if e["quelle"]["art"] == "skills-CLI":
        return f"npx skills remove --global {e['name']}"
    if e["typ"] == "Befehl":
        return f"mv ~/.claude/commands/{e['name']}.md ~/.Trash/"
    return f"mv ~/.claude/skills/{e['name']} ~/.Trash/"


def aktionen(e):
    """Was sich über das Dashboard umsetzen lässt: Aktion → Befehl (`argv`, ggf. `cwd`) oder skillOverrides-Wert.
    Aktionen, die schon gelten, fehlen."""
    eid = e["id"]
    if eid.startswith("plugin:"):
        pid = f"{e['name']}@{e['marktplatz']}"
        a = {"deinstallieren": {"argv": ["claude", "plugin", "uninstall", pid]}}
        if e["status"] == "aktiv":
            a["deaktivieren"] = {"argv": ["claude", "plugin", "disable", pid]}
        return a
    if eid.startswith("mcp:"):
        if e["art"] == "User":
            return {"deinstallieren": {"argv": ["claude", "mcp", "remove", e["name"], "-s", "user"]}}
        if e["art"] == "Projekt":
            return {"deinstallieren": {"argv": ["claude", "mcp", "remove", e["name"], "-s", "local"], "cwd": e["quelle"]}}
        return {}
    if e["typ"] == "Agent":
        return {}
    a = {}
    if not e.get("plugin"):
        if e["quelle"]["art"] == "skills-CLI":
            a["deinstallieren"] = {"argv": ["npx", "skills", "remove", "--global", "-y", e["name"]]}
        else:
            ziel = e["pfad"] if e["typ"] == "Befehl" else str(Path(e["pfad"]).parent)
            a["deinstallieren"] = {"argv": ["mv", ziel, str(HOME / ".Trash") + "/"]}
    if e["override"] != "off":
        a["deaktivieren"] = {"override": "off"}
    if e["aufruf"] != "nur /":
        a["nur manuell"] = {"override": "user-invocable-only"}
    return a


def bewerte(eintraege):
    bewertungen = BEWERTUNG.get("eintraege", {})
    for e in eintraege:
        b = bewertungen.get(e["id"])
        e["bewertung"] = b
        if b and e.get("quelle") and isinstance(e["quelle"], dict):
            for feld in ("autor", "repo", "url"):
                if b.get(feld):
                    e["quelle"][feld] = b[feld]
        b = b or {}
        e["befehl"] = b.get("befehl") or befehl(e, b.get("empfehlung"))


def claude_version():
    try:
        return subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def main():
    plugin_liste, plugin_teile = plugins()
    skills = persoenliche_skills() + plugin_teile
    live = mcp_live()
    mcp = mcp_server(plugin_liste, live)
    alle = mcp + plugin_liste + skills
    bewerte(alle)
    for e in alle:
        e["aktionen"] = aktionen(e)

    eingebaut = []
    for b in BEWERTUNG.get("eingebaut", []):
        eingebaut.append({**b, "nutzung": nutzung(b["name"])})

    ids = {e["id"] for e in alle}
    unbewertet = [e["id"] for e in alle if not e["bewertung"]]
    entfallen = sorted(set(BEWERTUNG.get("eintraege", {})) - ids)

    daten = {
        "erstellt": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "claude_version": claude_version(),
        "live_status": live is not None,
        "bewertung_stand": BEWERTUNG.get("stand"),
        "skill_overrides": OVERRIDES,
        "mcp": mcp,
        "plugins": plugin_liste,
        "skills": skills,
        "eingebaut": eingebaut,
        "gruppen": BEWERTUNG.get("gruppen", []),
        "hinweise": BEWERTUNG.get("hinweise", []),
        "unbewertet": unbewertet,
        "entfallen": entfallen,
    }
    (HIER / "inventar.json").write_text(json.dumps(daten, ensure_ascii=False, indent=1), encoding="utf-8")

    einbettung = json.dumps(daten, ensure_ascii=False).replace("</", "<\\/")
    svg = LOGO.read_text(encoding="utf-8")
    if LOGO_MASSE[0] not in svg:
        raise SystemExit(f"{LOGO} hat andere Maße als erwartet, Zuschnitt in LOGO_MASSE prüfen.")
    logo = "data:image/svg+xml;base64," + base64.b64encode(svg.replace(*LOGO_MASSE).encode()).decode()
    vorlage = (HIER / "template.html").read_text(encoding="utf-8").replace("__LOGO__", logo)
    (HIER / "dashboard.html").write_text(vorlage.replace("__DATEN__", einbettung), encoding="utf-8")

    print(f"dashboard.html geschrieben: {len(mcp)} MCP-Server, {len(plugin_liste)} Plugins, {len(skills)} Skills/Befehle/Agenten")
    if live is None:
        print("Hinweis: `claude mcp list` nicht erreichbar, MCP-Status nur aus der Konfiguration.")
    if unbewertet:
        print(f"Noch nicht bewertet ({len(unbewertet)}): " + ", ".join(unbewertet))
    if entfallen:
        print(f"Bewertet, aber nicht mehr installiert ({len(entfallen)}): " + ", ".join(entfallen))


if __name__ == "__main__":
    main()
