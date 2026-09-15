"""Harness-Inventur für Codex.

Liest ~/.codex und ~/.agents in dieselbe Datenform, die build.py für Claude Code erzeugt, und wird von
build.py mit `--harness codex` aufgerufen. Umgebungswerte von MCP-Servern (env, Tokens, Header) übernimmt
es nie. Codex führt keine Nutzungszähler; die Nutzung stammt aus den Sitzungsprotokollen in ~/.codex/sessions.
"""

import json
import re
import tomllib
from datetime import datetime
from pathlib import Path

from build import frontmatter, hook_eintraege, lies_json, ziel_text

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


LAEDT = ("aktiv", "im Konto")  # Plugin-Status, bei denen Codex die Bestandteile lädt
MCP_FELDER = ("command", "args", "url", "cwd")  # alles andere kann Zugangsdaten enthalten


def manifest(wurzel):
    for rel in ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        m = lies_json(wurzel / rel)
        if m:
            return m
    return {}


def version_ordner(plugin_ordner):
    """Die geladene Version: `local`, sonst der zuletzt geschriebene Versionsordner (Versionen sind teils Git-SHAs)."""
    if (plugin_ordner / "local").is_dir():
        return plugin_ordner / "local"
    return max((o for o in plugin_ordner.iterdir() if o.is_dir()), key=lambda o: o.stat().st_mtime, default=None)


def teil(wurzel, m, schluessel, standard):
    """Ein Plugin-Bestandteil: im Manifest direkt als Objekt, als relativer Pfad oder am Standardort."""
    wert = m.get(schluessel)
    if isinstance(wert, dict):
        return wert
    return lies_json(wurzel / (wert if isinstance(wert, str) else standard))


def lesbare_hooks(roh):
    """Codex-Hooks können ein MCP-Werkzeug statt eines Befehls aufrufen; für die Anzeige wird daraus `MCP server.tool`."""
    def handler(h):
        if isinstance(h, dict) and h.get("type") == "mcp_tool":
            return {**h, "command": f"MCP {h.get('server')}.{h.get('tool')}"}
        return h
    return {ereignis: [{**g, "hooks": [handler(h) for h in g.get("hooks", [])]} for g in gruppen if isinstance(g, dict)]
            for ereignis, gruppen in (roh or {}).items() if isinstance(gruppen, list)}


def plugin_status(pid, marktplatz, cfg, remote):
    """Remote-Plugins kommen aus dem ChatGPT-Konto, ihr Schalter liegt nicht lokal. Solange der Remote-Katalog an ist,
    ignoriert Codex die Einträge für openai-curated in config.toml."""
    if remote:
        return "im Konto"
    eintrag = (cfg.get("plugins") or {}).get(pid)
    if not isinstance(eintrag, dict):
        return "nicht aktiviert"
    if marktplatz == "openai-curated" and (cfg.get("features") or {}).get("remote_plugin", True) is not False:
        return "wirkungslos"
    return "aktiv" if eintrag.get("enabled", True) is not False else "deaktiviert"


def plugins(cfg, aus, autoren):
    liste, bestandteile, hooks = [], [], []
    cache = CODEX / "plugins" / "cache"
    for plugin_ordner in sorted(cache.glob("*/*")) if cache.is_dir() else []:
        wurzel = version_ordner(plugin_ordner) if plugin_ordner.is_dir() else None
        if wurzel is None:
            continue
        name, mkt = plugin_ordner.name, plugin_ordner.parent.name
        pid = f"{name}@{mkt}"
        m = manifest(wurzel)
        status = plugin_status(pid, mkt, cfg, (plugin_ordner / ".codex-remote-plugin-install.json").is_file())
        aktiv = status in LAEDT
        autor = m.get("author")
        autor = autor.get("name") if isinstance(autor, dict) else (autor or "unbekannt")
        autor = autoren.get(autor, autor)
        quelle = {"art": "Plugin", "autor": autor, "repo": pid}

        skills_ordner = wurzel / (m["skills"] if isinstance(m.get("skills"), str) else "skills")
        teile = []
        for d in sorted(skills_ordner.glob("*/SKILL.md")):
            t = skill_eintrag(f"{name}:{d.parent.name}", d, aus, aktiv)
            t.update(plugin=pid, quelle=dict(quelle))
            teile.append(t)
        bestandteile += teile

        mcp_roh = teil(wurzel, m, "mcpServers", ".mcp.json")
        mcp_roh = mcp_roh.get("mcpServers", mcp_roh)
        mcp = {n: {k: c[k] for k in MCP_FELDER if k in c} for n, c in mcp_roh.items() if isinstance(c, dict)}
        apps = teil(wurzel, m, "apps", ".app.json").get("apps")
        hooks_roh = lesbare_hooks(teil(wurzel, m, "hooks", "hooks/hooks.json").get("hooks"))
        for h in hook_eintraege(hooks_roh, pid, quelle, wurzel, "aktiv" if aktiv else "aus"):
            hooks.append({**h, "plugin": pid})

        liste.append({
            "id": f"plugin:{pid}",
            "name": name,
            "marktplatz": mkt,
            "marktplatz_repo": None,
            "autor": autor,
            "homepage": m.get("homepage") or m.get("repository"),
            "version": m.get("version") or wurzel.name,
            "installiert": None,
            "aktualisiert": datetime.fromtimestamp(wurzel.stat().st_mtime).strftime("%Y-%m-%d"),
            "status": status,
            "beschreibung": m.get("description") or "",
            "skills": [t["name"] for t in teile],
            "befehle": [],
            "agenten": [],
            "apps": sorted(apps) if isinstance(apps, dict) else [],
            "hooks": sorted(hooks_roh),
            "mcp": mcp,
            "tokens": sum(t["tokens"] for t in teile),
            "nutzung": None,
            "aktivitaet": None,
            "pfad": str(wurzel),
        })
    return liste, bestandteile, hooks


def eigene_hooks(cfg):
    """Hooks aus config.toml ([hooks], ohne die Vertrauensliste `state`) und ~/.codex/hooks.json, dazu `notify`."""
    quelle = {"art": "Einstellungen", "autor": "eigene Einstellungen"}
    roh = {k: v for k, v in (cfg.get("hooks") or {}).items() if k != "state"}
    hooks = hook_eintraege(lesbare_hooks(roh), "config", quelle, CODEX / "config.toml")
    hooks += hook_eintraege(lesbare_hooks(lies_json(CODEX / "hooks.json").get("hooks")), "hooks.json", quelle, CODEX / "hooks.json")
    if isinstance(cfg.get("notify"), list) and cfg["notify"]:
        befehl = " ".join(str(t) for t in cfg["notify"])
        (notify,) = hook_eintraege({"notify": [{"hooks": [{"command": befehl}]}]}, "config", quelle, CODEX / "config.toml")
        hooks.append(notify)
    return hooks


def mcp_server(cfg, plugin_liste, mcp_tage):
    """Eigene Server aus config.toml und die der Plugins. Status nur aus der Konfiguration: Codex prüft keine Verbindung."""
    def eintrag(eid, name, art, quelle, c, status, nutzungsname):
        return {"id": eid, "name": name, "art": art, "quelle": quelle, "ziel": ziel_text(c), "status": status,
                "gesperrt": False, "aus_in_projekten": [], "nutzung": nutzung(mcp_tage.get(nutzungsname))}

    ergebnis = []
    for name, c in sorted((cfg.get("mcp_servers") or {}).items()):
        if isinstance(c, dict):
            status = "aktiv" if c.get("enabled", True) is not False else "deaktiviert"
            ergebnis.append(eintrag(f"mcp:{name}", name, "User", "~/.codex/config.toml", c, status, name))
    for pl in plugin_liste:
        pid = f"{pl['name']}@{pl['marktplatz']}"
        schalter = ((cfg.get("plugins") or {}).get(pid) or {}).get("mcp_servers") or {}
        for sname, c in sorted(pl["mcp"].items()):
            if pl["status"] not in LAEDT:
                status = "inaktiv (Plugin aus)"
            elif (schalter.get(sname) or {}).get("enabled", True) is False:
                status = "deaktiviert"
            else:
                status = "aktiv"
            ergebnis.append(eintrag(f"mcp:plugin:{pl['name']}:{sname}", f"plugin:{pl['name']}:{sname}", "Plugin", pid, c, status, sname))
    return ergebnis


def projekte(cfg, aus, autoren):
    """Was nur in Projekten aus [projects] gilt: Skills aus .agents/skills und .codex/skills laden immer,
    MCP-Server aus .codex/config.toml nur in vertrauten Projekten."""
    skills, mcp = [], []
    for pfad, p in sorted((cfg.get("projects") or {}).items()):
        ordner = Path(pfad)
        if not ordner.is_dir() or ordner.resolve() == HOME.resolve():
            continue
        vertraut = isinstance(p, dict) and p.get("trust_level") == "trusted"
        lock = lies_json(ordner / "skills-lock.json").get("skills", {})
        for wurzel in (ordner / ".agents" / "skills", ordner / ".codex" / "skills"):
            for datei in sorted(wurzel.glob("*/SKILL.md")):
                name = datei.parent.name
                e = skill_eintrag(name, datei, aus)
                repo = (lock.get(name) or {}).get("source")
                e.update(id=f"skill:{ordner.name}/{name}", projekt=pfad,
                         quelle=quelle_cli(repo, autoren) if repo else {"art": "lokal", "autor": e["meta_autor"] or "unbekannt"})
                skills.append(e)
        datei = ordner / ".codex" / "config.toml"
        for name, c in sorted((lies_toml(datei).get("mcp_servers") or {}).items()):
            if not isinstance(c, dict):
                continue
            if not vertraut:
                status = "nicht vertraut"
            else:
                status = "eingetragen" if c.get("enabled", True) is not False else "deaktiviert"
            mcp.append({"id": f"mcp:{ordner.name}/{name}", "name": name, "projekt": pfad, "art": ".codex/config.toml",
                        "quelle": str(datei), "ziel": ziel_text(c), "status": status, "gesperrt": False, "aus_in_projekten": []})
    return skills, mcp
