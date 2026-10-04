"""Tab „Modelle“: welches Modell und welcher Aufwand eingestellt sind und was wirklich lief. Nur Anzeige.

Eingestellt, Claude Code: die Schlüssel aus `SCHLUESSEL` und die Umgebungswerte aus `UMGEBUNG` in den verwalteten
Einstellungen, ~/.claude/settings.json und je Projekt in .claude/settings.json und .claude/settings.local.json, dazu
`model` und `effort` im Kopf von Agenten, Skills und Befehlen. Codex: die Modell-Schlüssel in ~/.codex/config.toml,
auch in Profilen.

Gemessen: aus den Transkripten bzw. Sitzungen, je Ordner und Modell. Welches Modell am Ende gilt, rechnet das Modul
nicht aus: Ein Subagent bekommt es oft erst beim Aufruf, das steht in keiner Datei. Was lief, zeigt die Messung.
„überschreibt“ heißt deshalb nur: Der Wert weicht vom globalen ab.
"""

import json
import os
from pathlib import Path

from berechtigungen import VERWALTET, kurz

SCHLUESSEL = ("model", "availableModels", "deniedModels", "fallbackModel", "effortLevel", "maxEffortLevel", "modelSettings",
              "alwaysThinkingEnabled", "fastMode")
UMGEBUNG = ("ANTHROPIC_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL", "CLAUDE_CODE_EFFORT_LEVEL", "MAX_THINKING_TOKENS")
TEMP = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
FELDER = ("tokens", "eingabe", "cache_schreiben", "cache_lesen")  # tokens: Ausgabe; eingabe: neu, ohne Cache
GLOBAL = {"model": "model", "effortLevel": "effortLevel", "effort": "effortLevel"}  # womit „überschreibt“ vergleicht


def lies(pfad):
    try:
        d = json.loads(Path(pfad).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def text(wert):
    return wert if isinstance(wert, str) else json.dumps(wert, ensure_ascii=False)


def zeile(bereich, quelle, name, wert, marken=(), **extra):
    return {"bereich": bereich, "quelle": quelle, "name": name, "wert": text(wert), "marken": list(marken), **extra}


def zeilen_lesen(dateien):
    """Die Zeilen eines Protokolls als Objekte. Eine Sitzung, die gerade läuft, endet mitten in einer Zeile."""
    for datei in dateien:
        try:
            with open(datei, encoding="utf-8", errors="replace") as f:
                for roh in f:
                    if '"model"' not in roh and '"token_count"' not in roh:
                        continue
                    try:
                        d = json.loads(roh)
                    except ValueError:
                        continue
                    if isinstance(d, dict):
                        yield datei, d
        except OSError:
            continue


def zaehlen(summen, schluessel, antworten=0, **werte):
    summe = summen.setdefault(schluessel, dict.fromkeys(("antworten", *FELDER), 0))
    summe["antworten"] += antworten
    for feld in FELDER:
        summe[feld] += werte[feld] if isinstance(werte.get(feld), int) else 0


def tabelle(summen, aufwand, home):
    """Je (Ordner, Modell, Subagent) eine Zeile, die größten Verbraucher eines Ordners zuerst."""
    ergebnis = [{"projekt": kurz(ordner, home), "modell": modell, "subagent": sub, **summe,
                 "aufwand": sorted(aufwand.get((ordner, modell, sub), ()))} for (ordner, modell, sub), summe in summen.items()]
    return sorted(ergebnis, key=lambda z: (z["projekt"], -z["tokens"], z["modell"]))


def projekt_von(ordner, projekte, home):
    """Das Projekt, zu dem ein Arbeitsordner gehört. Ein Subagent arbeitet oft in <Projekt>/.claude/worktrees/…,
    eine Sitzung wechselt in Unterordner: Beides stünde sonst als eigenes Projekt da. ~ ist auch ein Projekt,
    zählt aber nur selbst, sonst schluckte es jeden Ordner darunter. Läufe in Temp-Ordnern (etwa „neu bewerten“)
    heißen jedes Mal anders und stehen zusammen in einer Zeile."""
    def unter(oben):
        return ordner == oben or ordner.startswith(oben.rstrip("/") + "/")
    ordner = ordner.split("/.claude/worktrees/")[0]
    darueber = [p for p in projekte if p != str(home) and unter(p)]
    if darueber:
        return max(darueber, key=len)
    return "temporäre Ordner" if ordner.startswith(TEMP) and not unter(str(home)) else ordner


def claude_gemessen(transkripte, grenze, home, projekte):
    """Eine Antwort steht je Inhaltsblock in einer eigenen Zeile und nach dem Fortsetzen einer Sitzung in zwei
    Dateien: Sie zählt einmal, mit dem letzten Stand ihrer Tokens."""
    antworten, aufwand = {}, {}
    for nr, (datei, d) in enumerate(zeilen_lesen(transkripte)):
        m = d.get("message")
        if d.get("type") != "assistant" or not isinstance(m, dict) or not isinstance(m.get("model"), str) or m["model"] == "<synthetic>":
            continue
        if not isinstance(d.get("timestamp"), str) or d["timestamp"] < grenze:
            continue
        ordner = projekt_von(d["cwd"], projekte, home) if isinstance(d.get("cwd"), str) else "unbekannt"
        schluessel = (ordner, m["model"], bool(d.get("isSidechain")))
        nutzung = m.get("usage") if isinstance(m.get("usage"), dict) else {}
        antworten[m.get("id") or (str(datei), nr)] = (schluessel, {
            "tokens": nutzung.get("output_tokens"), "eingabe": nutzung.get("input_tokens"),
            "cache_schreiben": nutzung.get("cache_creation_input_tokens"), "cache_lesen": nutzung.get("cache_read_input_tokens")})
        if isinstance(d.get("effort"), str):
            aufwand.setdefault(schluessel, set()).add(d["effort"])
    summen = {}
    for schluessel, werte in antworten.values():
        zaehlen(summen, schluessel, 1, **werte)
    return tabelle(summen, aufwand, home)


def claude(home, projekte, dateien, transkripte, grenze):
    """`dateien` sind Agenten, Skills und Befehle mit ihrem Kopf: bereich, eintrag, pfad, kopf (build.frontmatter)."""
    home = Path(home)
    benutzer = lies(home / ".claude/settings.json")

    def marken(bereich_global, name, wert):
        weicht_ab = name in GLOBAL and wert != "inherit" and wert != benutzer.get(GLOBAL[name])
        return ["überschreibt"] if weicht_ab and not bereich_global else []

    quellen = [(d, "global", True) for d in VERWALTET if d.exists()] + [(home / ".claude/settings.json", "global", False)]
    for projekt in sorted(projekte):
        quellen += [(Path(projekt) / ".claude" / f, projekt, False) for f in ("settings.json", "settings.local.json")]
    zeilen, gelesen = [], set()
    for datei, bereich, verwaltet in quellen:
        echt = os.path.realpath(datei)
        if echt in gelesen:  # ~ als Projekt: dessen .claude/settings.json ist die Benutzerdatei
            continue
        gelesen.add(echt)
        d, quelle = lies(datei), kurz(datei, home)
        extra = {"verwaltet": True} if verwaltet else {}
        zeilen += [zeile(bereich, quelle, k, d[k], marken(bereich == "global", k, d[k]), **extra) for k in SCHLUESSEL if k in d]
        env = d.get("env") if isinstance(d.get("env"), dict) else {}
        zeilen += [zeile(bereich, quelle, f"env.{k}", env[k], **extra) for k in UMGEBUNG if k in env]
    for e in dateien:
        zeilen += [zeile(e["bereich"], kurz(e["pfad"], home), k, e["kopf"][k], marken(False, k, e["kopf"][k]), eintrag=e["eintrag"])
                   for k in ("model", "effort") if isinstance(e["kopf"].get(k), str)]
    return {"eingestellt": zeilen, "gemessen": claude_gemessen(transkripte, grenze, home, list(projekte))}


def codex_gemessen(sitzungen, grenze, home):
    """Je Turn nennt `turn_context` Ordner, Modell und Aufwand; die Tokens danach gehören zu ihm. `input_tokens`
    enthält bei Codex auch, was aus dem Cache kam: Neu ist nur der Rest."""
    summen, aufwand, lauf = {}, {}, (None, None)
    for datei, d in zeilen_lesen(sitzungen):
        p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
        if lauf[0] != datei:
            lauf = (datei, None)
        if d.get("type") == "turn_context" and isinstance(p.get("model"), str):
            frisch = isinstance(d.get("timestamp"), str) and d["timestamp"] >= grenze
            lauf = (datei, (p["cwd"] if isinstance(p.get("cwd"), str) else "unbekannt", p["model"], False) if frisch else None)
            if frisch:
                zaehlen(summen, lauf[1], 1)
                if isinstance(p.get("effort"), str):
                    aufwand.setdefault(lauf[1], set()).add(p["effort"])
        elif p.get("type") == "token_count" and lauf[1]:
            info = p.get("info") if isinstance(p.get("info"), dict) else {}
            letzte = info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
            zahl = lambda k: letzte[k] if isinstance(letzte.get(k), int) else 0
            zaehlen(summen, lauf[1], tokens=zahl("output_tokens"), eingabe=max(zahl("input_tokens") - zahl("cached_input_tokens"), 0),
                    cache_schreiben=zahl("cache_write_input_tokens"), cache_lesen=zahl("cached_input_tokens"))
    return tabelle(summen, aufwand, home)


def codex(cfg, sitzungen, grenze, home):
    toml = "~/.codex/config.toml"

    def werte(tabelle):
        return [(k, v) for k, v in tabelle.items() if (k.startswith("model") or k.endswith("_model")) and isinstance(v, (str, int, float))]

    zeilen = [zeile("global", toml, k, v) for k, v in werte(cfg)]
    profile = cfg.get("profiles") if isinstance(cfg.get("profiles"), dict) else {}
    for name, profil in sorted(profile.items()):
        if isinstance(profil, dict):
            zeilen += [zeile("global", f"{toml} → profiles.{name}", k, v, ["überschreibt"] if v != cfg.get(k) else []) for k, v in werte(profil)]
    return {"eingestellt": zeilen, "gemessen": codex_gemessen(sitzungen, grenze, Path(home))}
