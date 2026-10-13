#!/usr/bin/env python3
"""Harness-Inventur für Claude Code und Codex: der Startbefehl.

Lässt den Bestand des Harness lesen, in dem der Skill zuletzt gestartet wurde (Claude Code über claude.py,
Codex über codex.py), ergänzt, was für beide gilt (Empfehlungen, Versionen, Berechtigungen, Modelle, Anweisungen,
Verlauf), und schreibt inventar.json und dashboard.html, für Codex inventar-codex.json und dashboard-codex.html.

Alles Geschriebene landet in ~/.3zero-harness-inventur, nicht im Projektordner: Es gehört zum Rechner
des Benutzers, nicht zum Werkzeug. Dort liegt auch die fachliche Bewertung (Beschreibung auf Deutsch,
Überschneidungen, Empfehlungen), bewertung-claude-code.json bzw. bewertung-codex.json. Die pflegt der
Skill /3zero-harness-inventur, dieses Skript liest sie nur.

Aufruf: python3 build.py [--harness claude-code|codex]   (ohne Angabe gilt der zuletzt genannte)
"""

import argparse
import base64
import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import anweisungen
import berechtigungen
import claude
import codex
import harness
import modelle
import werkzeuge
from bestand import HOOK_TAGE, LESEFEHLER, frontmatter, lesefehler, lies_json, werkzeug_praefix, zeitgrenze

HIER = Path(__file__).resolve().parent
DATEN = harness.DATEN  # Bewertung, Inventar, Dashboard und harness.json: alles außerhalb des Repos
LOGO = HIER / "wordmark.svg"  # Kopie aus dem 3zero-Design-Kit, viewBox schon gekürzt; siehe Kommentar in der Datei
ICON = HIER / "app-icon.svg"  # Favicon und Zeichen im Seitenkopf
APP_ICON = HIER / "app-icon.png"  # fürs Dock, gerendert aus ICON mit render-app-icon.py
UNGENUTZT_TAGE = 30  # so lange ohne Nutzung gilt ein Skill, der in jede Sitzung lädt, als ungenutzt


def data_uri(inhalt, mime):
    """Bettet Bytes in die Seite ein, damit das Dashboard eine einzelne Datei bleibt."""
    return f"data:{mime};base64," + base64.b64encode(inhalt).decode()



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
    Dashboards gleich und meinte Verschiedenes, und die beiden Verläufe wären nicht vergleichbar. Dazu die globalen
    Anweisungen (CLAUDE.md, Regeln, der gewählte Output-Style, unter Codex AGENTS.md): Sie laden genauso in jede Sitzung."""
    return (sum(e.get("tokens") or 0 for art in ("skills", "hooks", "mcp") for e in inhalt.get(art, []))
            + sum(z["tokens"] for z in inhalt.get("anweisungen", []) if z["bereich"] == "global" and z["laedt"]))



def rechte(h, inhalt, cfg):
    """Der Tab „Berechtigungen“ des jeweiligen Harness. Verwaist ist eine MCP-Regel, deren Server nicht mehr eingetragen ist."""
    if h == "codex":
        return rechte_aktionen(berechtigungen.codex(codex.lies_toml(codex.CODEX / "config.toml"), codex.CODEX / "rules", codex.HOME))
    praefixe = {werkzeug_praefix(e.get("schluessel", e["name"])) for e in inhalt.get("mcp", []) + inhalt.get("projekt_mcp", [])}
    return rechte_aktionen(berechtigungen.claude(claude.HOME, (cfg.get("projects") or {}).keys(), praefixe))



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



def modelle_lesen(h, inhalt, cfg):
    """Der Tab „Modelle“ des jeweiligen Harness: was eingestellt ist und was lief, im selben Zeitfenster wie die Hooks."""
    if h == "codex":
        grenze = datetime.now().timestamp() - HOOK_TAGE * 86400
        frisch = [d for d in codex.sitzungen() if d.is_file() and d.stat().st_mtime >= grenze]
        ergebnis = modelle.codex(codex.lies_toml(codex.CODEX / "config.toml"), frisch, zeitgrenze(), codex.HOME)
    else:
        dateien = [{"bereich": e.get("projekt") or "global", "eintrag": f"{e.get('typ', 'Skill')} {e['name']}", "pfad": e["pfad"],
                    "kopf": frontmatter(e["pfad"])} for e in inhalt.get("skills", []) + inhalt.get("projekt_skills", []) if e.get("pfad")]
        ergebnis = modelle.claude(claude.HOME, (cfg.get("projects") or {}).keys(), dateien, claude.transkripte(), zeitgrenze())
    return {**ergebnis, "tage": HOOK_TAGE}



def anweisungen_lesen(h, inhalt, cfg, settings):
    """Der Tab „Anweisungen“ des jeweiligen Harness: was als Text in die Sitzung lädt."""
    if h == "codex":
        return anweisungen_aktionen(anweisungen.codex(codex.HOME))
    return anweisungen_aktionen(anweisungen.claude(claude.HOME, settings, (cfg.get("projects") or {}).keys()), bool(inhalt.get("pruefskill")))



def anweisungen_aktionen(zeilen, pruefbar=False):
    """Was sich im Dashboard ab- und wieder anschalten lässt, beides nur in ~/.claude/settings.json: eine CLAUDE.md,
    Regel oder AGENTS.md über `claudeMdExcludes` (die Datei selbst bleibt, wie sie ist — sie liegt oft in einem Repo),
    ein Output-Style über `outputStyle`. Wieder anschalten nur, wo genau diese Datei dort eingetragen ist: Ein Muster
    oder ein Eintrag im Projekt träfe mehr als diese Zeile. Verwaltete Dateien gehören dem Administrator, MEMORY.md
    schaltet `autoMemoryEnabled` für alles zusammen, und unter Codex gibt es keinen Schalter.
    `pruefen` bekommt die CLAUDE.md eines Projekts, wenn der Skill dafür da ist (`pruefbar`): Der Server lässt Claude
    sie dann lesend bewerten. Nicht die globale (das Raster des Skills fragt nach Befehlen und Aufbau eines Projekts),
    keine geerbte (sie gehört zum Ordner darüber) und keine verwaltete."""
    for z in zeilen:
        z["id"] = f"anweisung:{z['bereich']}:{z['pfad'] or z['name']}"
        z["aktionen"] = {}
        if pruefbar and z["art"] == "CLAUDE.md" and z["bereich"] != "global" and not z.get("geerbt") and not z.get("verwaltet"):
            z["pruefen"] = {"datei": z["pfad"], "projekt": z["bereich"]}
        if z.get("verwaltet") or z["pfad"] and ".codex" in Path(z["pfad"]).parts:
            continue
        if z["art"] == "Output-Style":
            if z["gewaehlt"]:
                z["aktionen"] = {"deaktivieren": {"output_style": None}}
            elif z["pfad"]:
                z["aktionen"] = {"einschalten": {"output_style": z["name"]}}
        elif z["art"] in ("CLAUDE.md", "Regel", "AGENTS.md"):
            if z.get("ausgeschlossen") == "eigen":
                z["aktionen"] = {"einschalten": {"einschliessen": z["pfad"]}}
            elif not z.get("ausgeschlossen"):
                z["aktionen"] = {"deaktivieren": {"ausschliessen": z["pfad"]}}
    return zeilen



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



ID_ARTEN = ("mcp", "plugins", "skills", "hooks", "projekt_skills", "projekt_mcp", "projekt_hooks")



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



BEWERTETE_ARTEN = ("mcp", "plugins", "skills", "projekt_skills", "projekt_mcp", "hooks")  # was bewerte() bewertet

SPAREN = ("entfernen", "abschalten", "nur manuell")  # nicht „prüfen“: Dort fehlt noch die Entscheidung



def empfehlungen_ableiten(inhalt):
    """Für beide Harnesses, nach dem Leser: je Eintrag `wirksam`, die Empfehlung, die für ihn gilt, und oben `ersparnis`.
    Bestandteile eines Plugins ohne eigene Empfehlung übernehmen die ihres Plugins (`geerbt`).
    Die Ersparnis zählt jede Tokenzahl genau einmal: Die eines Plugins ist schon die Summe seiner Skills und Hooks.
    Geht das Plugin ganz, zählt seine Summe und seine Teile zählen nicht mehr; bleibt es, zählen nur die einzeln
    empfohlenen Teile. Was nur in einem Projekt lädt, zählt nicht zur Kontextlast und spart deshalb nichts; was schon
    umgesetzt ist (`umgesetzt` vom Leser), kostet heute nichts mehr.
    Das Template rechnete beides selbst, in der einzigen Datei ohne Tests."""
    eigene = lambda e: e["bewertung"].get("empfehlung") if isinstance(e.get("bewertung"), dict) else None
    plugins = {p["id"]: p for p in inhalt.get("plugins", [])}
    for art in BEWERTETE_ARTEN:
        for e in inhalt.get(art, []):
            pid = e.get("plugin") or (e.get("quelle") if e["id"].startswith("mcp:") and e.get("art") == "Plugin" else None)
            vom_plugin = eigene(plugins[f"plugin:{pid}"]) if isinstance(pid, str) and f"plugin:{pid}" in plugins else None
            e["wirksam"] = ({"empfehlung": eigene(e), "geerbt": False} if eigene(e)
                            else {"empfehlung": vom_plugin, "geerbt": True} if vom_plugin else None)
    geht_weg = lambda e: eigene(e) in SPAREN and not e.get("projekt") and not e.get("umgesetzt")
    ganz = [p for p in inhalt.get("plugins", []) if geht_weg(p)]
    plugins_weg = {f"{p['name']}@{p['marktplatz']}" for p in ganz}
    inhalt["ersparnis"] = (sum(p.get("tokens") or 0 for p in ganz)
                           + sum(e.get("tokens") or 0 for art in ("skills", "hooks", "mcp") for e in inhalt.get(art, [])
                                 if geht_weg(e) and e.get("plugin") not in plugins_weg))



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



def seite(vorlage, daten):
    """Das Dashboard: die Vorlage mit Logo, Icons und dem Inventar gefüllt. Die Daten zuletzt, damit ein Platzhalter in
    einem Namen nicht ersetzt wird."""
    # jedes < escapen, nicht nur </: Mit <!--<script aus einem Namen beendete das folgende </script> das Skript nicht mehr
    einbettung = json.dumps(daten, ensure_ascii=False).replace("<", "\\u003c")
    return (vorlage.replace("__LOGO__", data_uri(LOGO.read_bytes(), "image/svg+xml"))
            .replace("__ICON__", data_uri(ICON.read_bytes(), "image/svg+xml"))
            .replace("__APPICON__", data_uri(APP_ICON.read_bytes(), "image/png"))
            .replace("__DATEN__", einbettung))



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
    # die einzige Stelle, die die Konfiguration von Claude Code liest; unter Codex bleiben beide ungenutzt
    cfg, settings = lies_json(claude.HOME / ".claude.json"), lies_json(claude.CLAUDE / "settings.json")
    if h == "codex":
        inhalt = codex.inventar()
    else:
        inhalt = claude.inventar(cfg, settings, lies_json(DATEN / angaben["bewertung"]),
                                 lies_json(claude.AGENTS / ".skill-lock.json").get("skills", {}))
    inhalt = fuers_dashboard(inhalt)
    empfehlungen_ableiten(inhalt)
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
    inhalt["versionen_offen"] = werkzeuge.offen(inhalt["werkzeuge"], inhalt["quellcode"])
    try:
        inhalt["berechtigungen"] = rechte(h, inhalt, cfg)
    except Exception as fehler:
        lesefehler("Berechtigungen", fehler)
        inhalt["berechtigungen"] = []
    try:
        inhalt["modelle"] = modelle_lesen(h, inhalt, cfg)
    except Exception as fehler:
        lesefehler("Modelle", fehler)
        inhalt["modelle"] = {"eingestellt": [], "gemessen": [], "tage": HOOK_TAGE}
    if h != "codex":  # unter Codex sind gemessene Ordner oft keine Projekte, und [projects] ohne Ordner fällt schon in codex.py weg
        inhalt["ordner_fehlt"] = claude.fehlende_ordner(inhalt, (cfg.get("projects") or {}).keys())
    try:
        inhalt["pruefskill"] = None if h == "codex" else claude.pruefskill(inhalt)
        inhalt["anweisungen"] = anweisungen_lesen(h, inhalt, cfg, settings)
    except Exception as fehler:
        lesefehler("Anweisungen", fehler)
        inhalt["anweisungen"] = []
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

    harness.schreiben(DATEN / angaben["dashboard"], seite((HIER / "template.html").read_text(encoding="utf-8"), daten))

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
