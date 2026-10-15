"""Werkzeuge, die die Inventur, die MCP-Server und die Hooks brauchen: installierte und neueste Version,
Pflegeende und der Befehl zum Aktualisieren. Dazu MCP-Server, die als Quellcode unter ~ liegen (meist ein Git-Klon):
Pflegezustand upstream und ihre npm-Abhängigkeiten mit Sicherheitsmeldungen.

Nur zur Anzeige: Das Dashboard aktualisiert nichts selbst, schon gar nicht die feste Version von skills
(harness.SKILLS_CLI) — die soll erst geprüft werden. Die neuesten Versionen fragt es einmal am Tag im Netz ab
und merkt sie sich in werkzeuge.json im Datenordner; ohne Netz gilt der letzte Stand.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import urllib.request
from datetime import timedelta
from pathlib import Path

import harness

HOME = Path.home()
ZU_TUN = ("veraltet", "ohne Pflege", "fehlt")  # Status eines Werkzeugs, bei denen etwas zu tun ist; nicht „wartet auf Homebrew“
RUHEND_TAGE = 180  # so lange ohne Commit upstream gilt ein Projekt als ruhend
# Einstellungen aus der .git/config eines fremden Klons, die beim Lesen ein Programm starten könnten, abschalten
GIT_SICHER = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
NPM_NAME = re.compile(r"(@[A-Za-z0-9._~-]+/)?[A-Za-z0-9._~-]+")
CELLAR = re.compile(r"/Cellar/([^/]+)/")  # Homebrew: die Formel steht im echten Pfad

# Name im PATH → (Anzeigename, Quelle der neuesten Version)
WERKZEUGE = {
    "python3": ("Python", "eol:python"),
    "node": ("Node.js", "eol:nodejs"),
    "bun": ("Bun", "eol:bun"),
    "uv": ("uv", "github:astral-sh/uv"),
    "deno": ("Deno", "github:denoland/deno"),
    "docker": ("Docker", None),
    "gh": ("GitHub CLI", "github:cli/cli"),
    "claude": ("Claude Code", "npm:@anthropic-ai/claude-code"),
    "codex": ("Codex", "npm:@openai/codex"),
}
# Aufrufname → Werkzeug: npx kommt mit Node, bunx mit Bun, uvx mit uv
ALIAS = {"python": "python3", "npx": "node", "npm": "node", "bunx": "bun", "uvx": "uv"}
# Was die Inventur selbst braucht, unabhängig von MCP-Servern und Hooks
GRUNDBEDARF = {"python3": "Inventur (build.py, server.py)", "gh": "Update-Prüfung der Skills", "node": "npx für Skill-Aktionen"}


def version_aus(text):
    m = re.search(r"\d+(?:\.\d+)+", text or "")
    return m.group() if m else None


def zahlen(version):
    return tuple(int(x) for x in version.split("."))


def bewerten(installiert, quelle, heute):
    """`quelle` ist die neueste Version als Text, die Linien von endoflife.date (neueste zuerst) oder None.
    Veraltet heißt: In der eigenen Linie gibt es Neueres. Eine neuere Linie allein macht nichts veraltet."""
    ergebnis = {"status": "unbekannt", "neueste": None, "pflege_bis": None, "neue_linie": None}
    if isinstance(quelle, str):
        ergebnis["neueste"] = quelle
    elif quelle:
        passend = [l for l in quelle if installiert and (installiert == l["name"] or installiert.startswith(l["name"] + "."))]
        linie = max(passend, key=lambda l: len(l["name"]), default=None)
        if linie:
            ergebnis.update(neueste=linie["latest"], pflege_bis=linie["eol"],
                            neue_linie=quelle[0]["latest"] if quelle[0] is not linie else None)
    if installiert is None:
        return {**ergebnis, "status": "fehlt"}
    if ergebnis["pflege_bis"] and ergebnis["pflege_bis"] <= heute.isoformat():
        return {**ergebnis, "status": "ohne Pflege"}
    if ergebnis["neueste"]:
        ergebnis["status"] = "veraltet" if zahlen(installiert) < zahlen(ergebnis["neueste"]) else "aktuell"
    return ergebnis


def werkzeuge_in(befehl):
    """Welche Werkzeuge ein Befehl startet. Ein Pfad zählt nur, wenn er auf dasselbe Programm zeigt wie der Name im PATH:
    Codex bringt ein eigenes Node mit, dessen Pflege Sache der App ist. Ein .py-Argument braucht Python, auch hinter bash."""
    try:
        teile = shlex.split(befehl)
    except ValueError:
        teile = befehl.split()
    if not teile or "://" in befehl:
        return []
    name = os.path.basename(teile[0])
    name = ALIAS.get(name, name)
    gefunden = []
    if name in WERKZEUGE:
        im_pfad = shutil.which(name)
        if "/" not in teile[0] or (im_pfad and os.path.realpath(im_pfad) == os.path.realpath(teile[0])):
            gefunden.append(name)
    if any(t.endswith(".py") for t in teile[1:]) and "python3" not in gefunden:
        gefunden.append("python3")
    return gefunden


def gebraucht(inhalt):
    """Werkzeug → wer es braucht, aus den Befehlen der MCP-Server und Hooks."""
    bedarf = {}

    def eintragen(befehl, wer):
        for w in werkzeuge_in(befehl or ""):
            if wer not in bedarf.setdefault(w, []):
                bedarf[w].append(wer)
    for m in inhalt.get("mcp", []) + inhalt.get("projekt_mcp", []):
        eintragen(m.get("ziel"), f"MCP-Server {m['name']}" + (f" ({Path(m['projekt']).name})" if m.get("projekt") else ""))
    for h in inhalt.get("hooks", []):
        for b in h.get("befehle", []):
            eintragen(b.get("befehl"), f"Hook {h['name']}")
    return bedarf


def update_befehl(pfad):
    """Der Befehl zum Aktualisieren, abgelesen am echten Pfad des Programms; None, wenn die Installationsart unklar ist."""
    if m := CELLAR.search(pfad):
        return f"brew upgrade {m.group(1)}"
    if m := re.search(r"/lib/node_modules/((?:@[^/]+/)?[^/]+)/", pfad):
        return f"npm install -g {m.group(1)}@latest"
    if "/.bun/" in pfad:
        return "bun upgrade"
    if "/.local/share/claude/" in pfad:
        return "claude update"
    return None


def homebrew_version(formel):
    """Die Version, die Homebrew für die Formel anbietet, aus seinem lokalen Stand; None ohne brew oder Antwort."""
    brew = shutil.which("brew")
    if not brew:
        return None
    try:
        r = subprocess.run([brew, "info", "--json=v2", formel], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                           env={**os.environ, "HOMEBREW_NO_AUTO_UPDATE": "1"})
        return json.loads(r.stdout)["formulae"][0]["versions"]["stable"]
    except (OSError, subprocess.TimeoutExpired, ValueError, LookupError, TypeError):
        return None


def installiert(werkzeug):
    """(Version, echter Pfad) oder (None, None)."""
    pfad = shutil.which(werkzeug)
    if not pfad:
        return None, None
    try:
        r = subprocess.run([pfad, "--version"], capture_output=True, text=True, errors="replace", timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return None, os.path.realpath(pfad)
    return version_aus(r.stdout + r.stderr), os.path.realpath(pfad)


def abrufen(url):
    try:
        anfrage = urllib.request.Request(url, headers={"User-Agent": "3zero-harness-inventur", "Accept": "application/json"})
        with urllib.request.urlopen(anfrage, timeout=5) as antwort:
            return json.load(antwort)
    except Exception:  # Netz, Zeitgrenze, kein JSON: dann fehlt nur diese Angabe
        return None


def neueste(quelle):
    art, _, was = (quelle or "").partition(":")
    try:
        if art == "eol":
            daten = abrufen(f"https://endoflife.date/api/v1/products/{was}/")
            return [{"name": r["name"], "latest": (r.get("latest") or {}).get("name"), "eol": r.get("eolFrom")}
                    for r in daten["result"]["releases"]]
        if art == "npm":
            return abrufen(f"https://registry.npmjs.org/{was}/latest")["version"]
        if art == "github":
            return version_aus(abrufen(f"https://api.github.com/repos/{was}/releases/latest")["tag_name"])
    except (TypeError, KeyError):
        return None
    return None


class Zwischenspeicher:
    """werkzeuge.json im Datenordner: die letzten Antworten aus dem Netz. Je Schlüssel ein Versuch am Tag, auch ein
    erfolgloser; ein neuer Schlüssel wird am selben Tag noch gefragt; ohne Antwort gilt der letzte Stand."""

    def __init__(self, daten, heute):
        self.datei, self.heute = Path(daten) / "werkzeuge.json", heute.isoformat()
        try:
            gemerkt = json.loads(self.datei.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            gemerkt = {}
        gemerkt = gemerkt if isinstance(gemerkt, dict) else {}
        self.stand = gemerkt.get("neueste") if isinstance(gemerkt.get("neueste"), dict) else {}
        self.versucht = set(gemerkt.get("versucht") or []) if gemerkt.get("datum") == self.heute else set()

    def hole(self, schluessel, frage):
        if schluessel not in self.versucht:
            self.versucht.add(schluessel)
            wert = frage()
            if wert is not None:
                self.stand[schluessel] = wert
        return self.stand.get(schluessel)

    def sichern(self):
        harness.schreiben(self.datei, json.dumps({"datum": self.heute, "versucht": sorted(self.versucht), "neueste": self.stand},
                                                 ensure_ascii=False, indent=1))


def pruefen(inhalt, harness_id, daten, heute):
    speicher = Zwischenspeicher(daten, heute)

    bedarf = {w: [grund] for w, grund in GRUNDBEDARF.items()}
    bedarf[harness.HARNESSE[harness_id]["cli"]] = ["Harness"]
    for w, wer in gebraucht(inhalt).items():
        bedarf.setdefault(w, []).extend(x for x in wer if x not in bedarf[w])

    def nachschlagen(quelle):
        return speicher.hole(quelle, lambda: neueste(quelle)) if quelle else None

    liste = []
    for w, wer in bedarf.items():
        name, quelle = WERKZEUGE[w]
        version, pfad = installiert(w)
        b = bewerten(version, nachschlagen(quelle), heute)
        befehl = update_befehl(pfad) if pfad and b["status"] in ("veraltet", "ohne Pflege") else None
        # endoflife.date kennt eine Version oft Tage vor Homebrew; bis dahin täte `brew upgrade` nichts
        if b["status"] == "veraltet" and (m := CELLAR.search(pfad or "")):
            angebot = version_aus(homebrew_version(m.group(1)))
            if angebot and zahlen(angebot) <= zahlen(version):
                b["status"], befehl = "wartet auf Homebrew", None
        liste.append({"id": w, "name": name, "installiert": version, "pfad": pfad, **b, "befehl": befehl, "gebraucht_von": wer})

    fest = harness.SKILLS_CLI.split("@", 1)[1]
    b = bewerten(fest, nachschlagen("npm:skills"), heute)
    liste.append({"id": "skills", "name": "skills (npx)", "installiert": fest, "pfad": None, **b,
                  "befehl": f'harness.py → SKILLS_CLI = "skills@{b["neueste"]}" (erst prüfen, dann eintragen)' if b["status"] == "veraltet" else None,
                  "gebraucht_von": ["Aktionen im Dashboard (npx skills)"]})

    speicher.sichern()
    return liste


def git_aus(wurzel, *argv, timeout=10):
    """Ausgabe eines git-Befehls im Klon, None bei Fehler. Ohne Rückfrage nach Zugangsdaten und ohne Programme aus der Git-Konfiguration."""
    try:
        r = subprocess.run(["git", "-C", str(wurzel), *GIT_SICHER, *argv], capture_output=True, text=True, errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def wurzel_von(pfad):
    """Der nächste Ordner mit .git, sonst mit package.json, unterhalb von ~. Außerhalb (etwa /opt/homebrew, selbst ein
    Git-Repo) und ~ selbst zählen nicht; Plugin-Ordner pflegt das Plugin-System."""
    kandidaten = [o for o in ([pfad] if pfad.is_dir() else []) + list(pfad.parents) if o != HOME and o.is_relative_to(HOME)]
    for merkmal in (".git", "package.json"):
        for o in kandidaten:
            if (o / merkmal).exists():
                return None if any(o.is_relative_to(HOME / p) for p in (".claude/plugins", ".codex/plugins")) else o
    return None


def quellen(inhalt):
    """Ordner → MCP-Server, die aus ihm starten. Nur eigene und Projekt-Server: Plugin-Server und Connectoren pflegt jemand anderes."""
    gefunden = {}
    for m in inhalt.get("mcp", []) + inhalt.get("projekt_mcp", []):
        if m.get("art") in ("Plugin", "claude.ai-Connector"):
            continue
        ziel = m.get("ziel") or ""
        try:
            teile = shlex.split(ziel)
        except ValueError:
            teile = ziel.split()
        name = m["name"] + (f" ({Path(m['projekt']).name})" if m.get("projekt") else "")
        for teil in teile:
            if "/" not in teil or "://" in teil:
                continue
            pfad = Path(teil).expanduser()
            if not pfad.is_absolute():
                if not m.get("projekt"):
                    continue
                pfad = Path(m["projekt"]) / pfad
            wurzel = wurzel_von(pfad) if pfad.exists() else None
            if wurzel:
                namen = gefunden.setdefault(str(wurzel), [])
                if name not in namen:
                    namen.append(name)
                break
    return gefunden


def upstream(wurzel, holen):
    """Stand gegenüber demselben Zweig auf origin; ein eigenes Projekt steht oft auf einem Arbeitszweig, und gegen den
    Hauptzweig verglichen hieße das „neuere Commits“. Bei GitHub mit Datum des letzten Commits über die API (`holen`);
    antwortet sie nicht (privates Repo), per ls-remote mit den Zugangsdaten von git. Ohne fetch: Kennt der Klon den
    entfernten Commit als Vorfahren, ist er voraus, sonst gibt es upstream Neueres."""
    ergebnis = {"status": "unbekannt", "remote": None, "repo": None, "letzter_commit": None, "lokale_aenderungen": False, "befehl": None}
    kopf = git_aus(wurzel, "rev-parse", "HEAD")
    if kopf is None:
        return {**ergebnis, "status": "kein Git"}
    ergebnis["lokale_aenderungen"] = bool(git_aus(wurzel, "status", "--porcelain", "--untracked-files=no"))
    ergebnis["remote"] = remote = git_aus(wurzel, "remote", "get-url", "origin")
    if not remote:
        return ergebnis
    zweig = git_aus(wurzel, "rev-parse", "--abbrev-ref", "HEAD")
    zweig = zweig if zweig and zweig != "HEAD" else None  # ohne Zweig (detached) gilt der Hauptzweig
    entfernt = None
    if m := re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", remote):
        ergebnis["repo"] = m.group(1)
        try:
            letzter = holen(f"https://api.github.com/repos/{m.group(1)}/commits?" + (f"sha={zweig}&" if zweig else "") + "per_page=1")[0]
            entfernt, ergebnis["letzter_commit"] = letzter["sha"], letzter["commit"]["committer"]["date"][:10]
        except (TypeError, KeyError, IndexError):
            pass
    if entfernt is None:
        zeile = git_aus(wurzel, "ls-remote", "origin", f"refs/heads/{zweig}" if zweig else "HEAD", timeout=15)
        if zeile == "":
            return {**ergebnis, "status": "nur lokal"}  # der Zweig liegt nicht auf origin
        entfernt = zeile.split()[0] if zeile else None
    if entfernt is None:
        return ergebnis
    if entfernt == kopf:
        ergebnis["status"] = "aktuell"
    elif git_aus(wurzel, "merge-base", "--is-ancestor", entfernt, "HEAD") is not None:
        ergebnis["status"] = "lokal voraus"
    else:
        ergebnis.update(status="neuere Commits", befehl=f"git -C {shlex.quote(str(wurzel))} pull")
    return ergebnis


def ruht(u, heute):
    return bool(u.get("letzter_commit")) and u["letzter_commit"] < (heute - timedelta(days=RUHEND_TAGE)).isoformat()


def aelter(installiert, neueste):
    try:
        return zahlen(version_aus(installiert)) < zahlen(version_aus(neueste))
    except (AttributeError, TypeError, ValueError):
        return False


def pakete(wurzel, holen):
    """Die direkten npm-Abhängigkeiten mit installierter und neuester Version und Sicherheitsmeldungen zur installierten,
    dazu der Befehl zum Aktualisieren nach der Lock-Datei; None, wenn nichts zu tun ist."""
    def lies(pfad):
        try:
            daten = json.loads(Path(pfad).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return {}
        return daten if isinstance(daten, dict) else {}
    abhaengig = lies(wurzel / "package.json").get("dependencies")
    liste = []
    for name in sorted(abhaengig) if isinstance(abhaengig, dict) else []:
        version = lies(wurzel / "node_modules" / name / "package.json").get("version") if NPM_NAME.fullmatch(name) else None
        if not isinstance(version, str):
            continue  # nicht installiert oder kein gültiger Paketname
        neueste = holen(f"https://registry.npmjs.org/{name}/latest")
        meldungen = []
        for a in holen(f"https://api.github.com/advisories?ecosystem=npm&affects={name}@{version}") or []:
            v = next((x for x in a.get("vulnerabilities") or [] if (x.get("package") or {}).get("name") == name), {})
            meldungen.append({"id": a.get("ghsa_id"), "schwere": a.get("severity"), "titel": a.get("summary"),
                              "betroffen": v.get("vulnerable_version_range"), "behoben": v.get("first_patched_version"), "url": a.get("html_url")})
        liste.append({"name": name, "installiert": version, "neueste": neueste.get("version") if isinstance(neueste, dict) else None,
                      "meldungen": meldungen})
    zu_tun = [p["name"] for p in liste if p["meldungen"] or aelter(p["installiert"], p["neueste"])]
    if not zu_tun:
        return liste, None
    werkzeug = next((w for datei, w in (("bun.lock", "bun update"), ("bun.lockb", "bun update"), ("pnpm-lock.yaml", "pnpm update"),
                                        ("yarn.lock", "yarn upgrade")) if (wurzel / datei).exists()), "npm update")
    return liste, f"cd {shlex.quote(str(wurzel))} && {werkzeug} {' '.join(shlex.quote(n) for n in zu_tun)}"


def offen(werkzeuge, quellcode):
    """Die Zahl am Tab „Versionen“: Werkzeuge mit einem Status aus ZU_TUN und MCP-Server aus Quellcode, für die es
    einen Befehl zum Aktualisieren gibt (der eigene Stand oder upstream)."""
    return (sum(1 for w in werkzeuge if w.get("status") in ZU_TUN)
            + sum(1 for q in quellcode if q.get("befehl") or (q.get("upstream") or {}).get("befehl")))


def quellcode_pruefen(inhalt, daten, heute):
    """MCP-Server aus Quellcode: Pflegezustand upstream und Abhängigkeiten. Antworten aus dem Netz über den Zwischenspeicher."""
    speicher = Zwischenspeicher(daten, heute)
    holen = lambda url: speicher.hole(url, lambda: abrufen(url))
    liste = []
    for ordner, server in quellen(inhalt).items():
        u = upstream(Path(ordner), holen)
        p, befehl = pakete(Path(ordner), holen)
        liste.append({"id": ordner, "name": Path(ordner).name, "server": server, "upstream": u, "ruht": ruht(u, heute),
                      "pakete": p, "befehl": befehl})
    speicher.sichern()
    return liste
