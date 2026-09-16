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

import harness
from build import abgleich, bewerte, frontmatter, hook_eintraege, lies_json, updates_markieren, ziel_text

HOME = Path.home()
CODEX = HOME / ".codex"
AGENTS = HOME / ".agents"
CLAUDE = HOME / ".claude"  # nur für die Frage, ob ein Skill auch in Claude Code lädt
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
            # Verweist ~/.claude/skills auf denselben Ordner, lädt der Skill auch in Claude Code
            verweis = CLAUDE / "skills" / ordner.name / "SKILL.md"
            if verweis.is_file() and verweis.resolve() == datei.resolve():
                e["geteilt"] = True
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
    Eine Sitzung zählt ganz nicht mit, wenn sie selbst diesen Skill wählt oder im Projektordner lief: sonst zählte
    jede Bewertung ihre eigenen SKILL.md-Lesezugriffe als Nutzung. Ergebnis: ({aufgelöster SKILL.md-Pfad: [Tag, …]}, {Server: [Tag, …]})."""
    hier = str(HIER.resolve())
    skills, mcp = {}, {}
    for datei in dateien:
        gelesen, mcp_gelesen, ueberspringen = {}, {}, False
        try:
            f = open(datei, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with f:
            for zeile in f:
                if "skills.selected_skill_instructions" in zeile and "<name>3zero-harness-inventur</name>" in zeile:
                    ueberspringen = True
                if '"session_meta"' in zeile and '"cwd"' in zeile:
                    try:
                        meta = json.loads(zeile)
                    except ValueError:
                        meta = {}
                    cwd = (meta.get("payload") or {}).get("cwd") if meta.get("type") == "session_meta" else None
                    if isinstance(cwd, str) and str(Path(cwd).resolve()) == hier:
                        ueberspringen = True
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
                    mcp_gelesen.setdefault(item["server"], []).append(tag)
                    continue
                if payload.get("type") == "item_completed" and item.get("type") == "CommandExecution":
                    pfade = SKILL_PFAD.findall(str(item.get("command")))
                elif "skills.selected_skill_instructions" in zeile:
                    pfade = re.findall(r"<path>([^<]+/SKILL\.md)</path>", str(payload))
                else:
                    pfade = []
                for pfad in pfade:
                    gelesen[pfad] = max(tag, gelesen.get(pfad, ""))
        if ueberspringen:
            continue
        for pfad, tag in gelesen.items():
            skills.setdefault(str(Path(pfad).resolve()), []).append(tag)
        for server, tage in mcp_gelesen.items():
            mcp.setdefault(server, []).extend(tage)
    return skills, mcp


def nutzung(tage):
    return {"anzahl": len(tage), "zuletzt": max(tage) or None} if tage else None


LAEDT = ("aktiv", "im Konto")  # Plugin-Status, bei denen Codex die Bestandteile lädt
MCP_FELDER = ("command", "args", "url", "cwd")  # alles andere kann Zugangsdaten enthalten


def manifest(wurzel):
    for rel in ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        m = lies_json(wurzel / rel)
        if isinstance(m, dict) and m:
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
    ergebnis = lies_json(wurzel / (wert if isinstance(wert, str) else standard))
    return ergebnis if isinstance(ergebnis, dict) else {}


def lesbare_hooks(roh):
    """Codex-Hooks können ein MCP-Werkzeug statt eines Befehls aufrufen; für die Anzeige wird daraus `MCP server.tool`."""
    def handler(h):
        if isinstance(h, dict) and h.get("type") == "mcp_tool":
            return {**h, "command": f"MCP {h.get('server')}.{h.get('tool')}"}
        return h
    roh = roh if isinstance(roh, dict) else {}
    return {ereignis: [{**g, "hooks": [handler(h) for h in g.get("hooks", [])]} for g in gruppen if isinstance(g, dict)]
            for ereignis, gruppen in roh.items() if isinstance(gruppen, list)}


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
        mcp_roh = mcp_roh if isinstance(mcp_roh, dict) else {}
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
    hooks_json = lies_json(CODEX / "hooks.json")
    hooks_roh = hooks_json.get("hooks") if isinstance(hooks_json, dict) else None
    hooks += hook_eintraege(lesbare_hooks(hooks_roh), "hooks.json", quelle, CODEX / "hooks.json")
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

    mcp_servers = cfg.get("mcp_servers")
    mcp_servers = mcp_servers if isinstance(mcp_servers, dict) else {}
    ergebnis = []
    for name, c in sorted(mcp_servers.items()):
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
    projekte_cfg = cfg.get("projects")
    projekte_cfg = projekte_cfg if isinstance(projekte_cfg, dict) else {}
    for pfad, p in sorted(projekte_cfg.items()):
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
        mcp_servers = lies_toml(datei).get("mcp_servers")
        mcp_servers = mcp_servers if isinstance(mcp_servers, dict) else {}
        for name, c in sorted(mcp_servers.items()):
            if not isinstance(c, dict):
                continue
            if not vertraut:
                status = "nicht vertraut"
            else:
                status = "eingetragen" if c.get("enabled", True) is not False else "deaktiviert"
            mcp.append({"id": f"mcp:{ordner.name}/{name}", "name": name, "projekt": pfad, "art": ".codex/config.toml",
                        "quelle": str(datei), "ziel": ziel_text(c), "status": status, "gesperrt": False, "aus_in_projekten": []})
    return skills, mcp


def befehl(e, empfehlung):
    """Wie sich eine Empfehlung in Codex umsetzen ließe. Nur zum Kopieren: Knöpfe gibt es für Codex noch nicht."""
    eid = e["id"]
    if eid.startswith("hook:"):
        return f"codex plugin remove {e['plugin']}" if e.get("plugin") else f'{e["pfad"]} → "{e["ereignis"]}" entfernen'
    if eid.startswith("plugin:"):
        return f"codex plugin remove {e['name']}@{e['marktplatz']}"
    if eid.startswith("mcp:"):
        if e.get("projekt"):
            return f'{e["quelle"]} → [mcp_servers.{e["name"]}] entfernen'
        return f"codex plugin remove {e['quelle']}" if e["art"] == "Plugin" else f"codex mcp remove {e['name']}"
    if empfehlung == "nur manuell":
        return f"{Path(e['pfad']).parent}/agents/openai.yaml → policy: allow_implicit_invocation: false"
    if empfehlung == "abschalten":
        return f'~/.codex/config.toml → [[skills.config]] path = "{e["pfad"]}", enabled = false'
    if e.get("plugin"):
        return f"codex plugin remove {e['plugin']}"
    if e["quelle"]["art"] == "skills-CLI":
        return f"cd \"{e['projekt']}\" && npx skills remove {e['name']}" if e.get("projekt") else f"npx skills remove --global {e['name']}"
    return f"mv \"{Path(e['pfad']).parent}\" ~/.Trash/"


def toml_schluessel(name):
    """Ein Tabellenname in TOML: schlichte Namen bleiben nackt, alles andere kommt in Anführungszeichen."""
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else json.dumps(name, ensure_ascii=False)


def aktionen(e):
    """Was sich über das Dashboard umsetzen lässt: Befehl (`argv`), ein Wert in config.toml (`toml`),
    ein [[skills.config]]-Block (`skill_aus`) oder agents/openai.yaml (`nur_manuell`). Was schon gilt, fehlt."""
    eid = e["id"]
    if eid.startswith("hook:"):
        return {}
    if eid.startswith("plugin:"):
        pid = f"{e['name']}@{e['marktplatz']}"
        a = {"deinstallieren": {"argv": ["codex", "plugin", "remove", pid]}}
        if e["status"] == "aktiv":
            # „im Konto“ steht im Konto, nicht in config.toml; „wirkungslos“ und „nicht aktiviert“ laden ohnehin nicht
            a["deaktivieren"] = {"toml": {"tabelle": f"plugins.{toml_schluessel(pid)}", "schluessel": "enabled", "wert": "false"}}
        return a
    if eid.startswith("mcp:"):
        if e.get("projekt"):
            return {}  # steht in der config.toml des Projekts, nicht in der globalen
        if e["art"] == "Plugin":
            if e["status"] != "aktiv":
                return {}
            tabelle = f'plugins.{toml_schluessel(e["quelle"])}.mcp_servers.{toml_schluessel(e["name"].rsplit(":", 1)[-1])}'
            return {"deaktivieren": {"toml": {"tabelle": tabelle, "schluessel": "enabled", "wert": "false"}}}
        a = {"deinstallieren": {"argv": ["codex", "mcp", "remove", e["name"]]}}
        if e["status"] == "aktiv":
            a["deaktivieren"] = {"toml": {"tabelle": f"mcp_servers.{toml_schluessel(e['name'])}", "schluessel": "enabled", "wert": "false"}}
        return a
    if e.get("projekt"):
        # Pfade in skills.config gelten global; im Projekt bleibt nur deinstallieren
        if e["quelle"]["art"] == "skills-CLI":
            return {"deinstallieren": {"argv": ["npx", "skills", "remove", "-y", e["name"]], "cwd": e["projekt"]}}
        return {"deinstallieren": {"argv": ["mv", str(Path(e["pfad"]).parent), str(HOME / ".Trash") + "/"]}}
    a = {}
    if not e.get("plugin"):
        if e.get("update"):
            a["aktualisieren"] = {"argv": ["npx", "skills", "update", e["name"], "-g", "-y"]}
        if e["quelle"]["art"] == "skills-CLI":
            a["deinstallieren"] = {"argv": ["npx", "skills", "remove", "--global", "-y", e["name"]]}
        else:
            a["deinstallieren"] = {"argv": ["mv", str(Path(e["pfad"]).parent), str(HOME / ".Trash") + "/"]}
    if e["status"] != "aus":
        a["deaktivieren"] = {"skill_aus": e["pfad"]}
    if e["aufruf"] != "nur $" and not e.get("plugin"):
        # agents/openai.yaml im Plugin-Ordner überschriebe das Plugin beim nächsten Update
        a["nur manuell"] = {"nur_manuell": str(Path(e["pfad"]).parent)}
    return a


def inventar():
    """Der Codex-Bestand in der Form, die template.html erwartet. build.main ergänzt Zeitpunkt, Harness und Version."""
    cfg = lies_toml(CODEX / "config.toml")
    bewertung = lies_json(HIER / harness.HARNESSE["codex"]["bewertung"])
    autoren = bewertung.get("autoren", {})
    aus = abgeschaltete(cfg)
    lock = lies_json(AGENTS / ".skill-lock.json").get("skills", {})

    plugin_liste, plugin_teile, plugin_hooks = plugins(cfg, aus, autoren)
    skills = eigene_skills(aus, lock, autoren) + plugin_teile
    updates_markieren(skills, lock)
    projekt_skills, projekt_mcp = projekte(cfg, aus, autoren)
    eingebaut = eingebaute(aus, bewertung)
    skill_tage, mcp_tage = nutzung_zaehlen(sitzungen())
    for e in skills + projekt_skills + eingebaut:
        e["nutzung"] = nutzung(skill_tage.get(str(Path(e["pfad"]).resolve())))
    for p in plugin_liste:
        genutzt = [t["nutzung"] for t in plugin_teile if t["plugin"] == f"{p['name']}@{p['marktplatz']}" and t["nutzung"]]
        if genutzt:
            p["nutzung"] = {"anzahl": sum(n["anzahl"] for n in genutzt), "zuletzt": max(n["zuletzt"] or "" for n in genutzt) or None}
    hooks = eigene_hooks(cfg) + plugin_hooks
    mcp = mcp_server(cfg, plugin_liste, mcp_tage)

    alle = mcp + plugin_liste + skills + projekt_skills + projekt_mcp + hooks
    bewerte(alle, bewertung, befehl)
    for e in alle:
        e["aktionen"] = aktionen(e)
    unbewertet, entfallen = abgleich(alle, bewertung)
    return {
        "live_status": None,
        "bewertung_stand": bewertung.get("stand"),
        "overrides_verwaist": [],
        "mcp": mcp,
        "plugins": plugin_liste,
        "skills": skills,
        "projekt_skills": projekt_skills,
        "projekt_mcp": projekt_mcp,
        "hooks": hooks,
        "hook_tage": None,
        "eingebaut": eingebaut,
        "gruppen": bewertung.get("gruppen", []),
        "hinweise": bewertung.get("hinweise", []),
        "unbewertet": unbewertet,
        "entfallen": entfallen,
    }
