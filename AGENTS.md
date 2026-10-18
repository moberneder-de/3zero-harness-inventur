# AGENTS.md

Hinweise für Agenten (Claude Code und Codex) beim Arbeiten in diesem Repository. Claude Code liest diese Datei über `CLAUDE.md` (`@AGENTS.md`), Codex direkt.

## Projekt

Inventur von MCP-Servern, Plugins, Skills, Befehlen, Agenten und Hooks, global und in Projekten, für Claude Code und Codex (dazu die Skills aus `~/.agents`), mit Bewertung von Überschneidungen, Empfehlungen und Umsetzung per Dashboard.

## Befehle

```bash
python3 -m unittest -v                   # alle Tests, wie in CI
python3 -m unittest tests.test_claude.Mods  # eine Testklasse
python3 -m unittest tests.test_claude.Mods.<methode>  # eine Methode
python3 build.py                         # Bestand neu einlesen, schreibt in ~/.3zero-harness-inventur
python3 server.py                        # Dashboard mit Knöpfen auf http://127.0.0.1:8765
python3 seitenvergleich.py               # nach einem Umbau an inventur/template.html: gleicher Text, gleiche Klicks wie in HEAD?
python3 seitenvergleich.py --gegen main  # gegen einen anderen Git-Stand; ein eigenes Inventar als Argument geht auch
python3 render-app-icon.py               # inventur/app-icon.svg → inventur/app-icon.png (Dock-Icon), Einmalschritt
python3 wochenlauf.py einrichten         # wöchentlicher Lauf, montags 9 Uhr; nur nach Bestätigung
python3 wochenlauf.py entfernen
```

- Voraussetzung: Python 3.11 oder neuer (`tomllib`), keine fremden Pakete. Ausnahmen: `render-app-icon.py` braucht Pillow und `qlmanage` (nur macOS), `seitenvergleich.py` einen Chromium-Browser.
- Kein Linter oder Formatter im Projekt: nichts installieren oder ausführen.
- Der Server lehnt http://127.0.0.1:8765 ohne Schlüssel ab (403): zum erneuten Öffnen `~/.3zero-harness-inventur/start.html` nehmen.
- `build.py --harness codex` (ebenso `server.py --harness codex`) merkt sich Codex als Harness: Ein laufender Server zeigt danach das Codex-Dashboard. Zum Ausprobieren stattdessen `codex.inventar()` oder eine einzelne Funktion in einem Skript aufrufen; `codex.inventar()` liest dabei den echten Bestand aus `~/.codex` und `~/.agents` und schreibt nur den Tageszwischenspeicher `werkzeuge.json` in den Datenordner.
- Oberfläche prüfen, ohne den Datenordner zu ändern (vorher einmal `python3 build.py`, damit `inventar-claude-code.json` im Datenordner liegt): `build.seite()` füllt das Template mit einem Inventar aus dem Datenordner, das Ergebnis geht nach `$TMPDIR` (ohne die Variable nach `/tmp`) und wird aufgenommen:

  ```bash
  TMP="${TMPDIR:-/tmp}"
  python3 - "$TMP" <<'PY'
  import json, pathlib, sys
  from inventur import build, harness
  v = (harness.PROJEKT / "inventur/template.html").read_text()
  d = json.loads((harness.DATEN / "inventar-claude-code.json").read_text())
  pathlib.Path(sys.argv[1], "vorschau.html").write_text(build.seite(v, d))
  PY
  BROWSER_BIN="${BROWSER_BIN:-/Applications/Brave Browser.app/Contents/MacOS/Brave Browser}"  # gilt auch für seitenvergleich.py
  "$BROWSER_BIN" --headless=new --screenshot="$TMP/vorschau.png" --window-size=1440,2000 "file://$TMP/vorschau.html"
  ```

  Knöpfe erscheinen nur, wenn `<script id="token">` nicht `null` enthält.
- Änderungen laufen über Branch und PR; gemergt wird, wenn beide Jobs bei GitHub grün sind (`gh pr merge --rebase --delete-branch`).

## Aufbau

- Ordner: Im Hauptordner stehen nur die Startbefehle `build.py`, `server.py` und `wochenlauf.py` (je ein paar Zeilen, sie rufen `main()` im Paket) und die Werkzeuge `seitenvergleich.py` und `render-app-icon.py`. Der Code liegt im Paket `inventur/` mit Template, Icons und Logo, die Tests in `tests/`. Die Module importieren einander relativ (`from . import harness`), die Tests über `from inventur import build`. Was den Projektordner braucht (`build.py` starten, `skill/` kopieren), nimmt `harness.PROJEKT`, nie den Ordner des Moduls.
- Datenordner: Alles, was zum Rechner des Benutzers gehört, liegt in `~/.3zero-harness-inventur` (`harness.DATEN`): beide Bewertungen, beide Inventare, beide Dashboards, beide Verläufe und `harness.json`. Der Projektordner enthält nur das Werkzeug und ist deshalb veröffentlichungsfähig. `build.py` legt den Ordner beim ersten Lauf an; fehlt eine Bewertung, gilt sie als leer und alles erscheint als „unbewertet“. In `server.py` ist `ordner` das Werkzeug und `daten` der Datenordner — wer beides verwechselt, lässt den Server wieder ins Repo schreiben.
- Harness: Der Skill nennt beim Start seinen Harness (`build.py --harness claude-code|codex`), `build.py` merkt ihn sich in `harness.json`, der letzte Start zählt. Die Dateinamen je Harness (Inventar, Dashboard, Bewertung, Verlauf) stehen in `harness.HARNESSE`.
- Bewertung: `bewertung-<harness>.json` im Datenordner enthält die Urteile: Beschreibung, Empfehlung, Überschneidungsgruppen, Hinweise.
- Skill: `/3zero-harness-inventur` in `skill/SKILL.md` pflegt die Bewertung; verfügbar über die Symlinks `~/.claude/skills/3zero-harness-inventur` und `~/.codex/skills/3zero-harness-inventur` auf diesen Ordner. `suche <Bedarf>` sucht neue Skills, Plugins und MCP-Server, gleicht sie mit dem Bestand ab und installiert nur nach Bestätigung.
- Tests: Wer feste Daten in Transkripten verwendet, setzt `zeitgrenze` selbst, sonst fallen sie nach 14 Tagen aus dem Messfenster. Auf GitHub laufen sie bei jedem Push (`.github/workflows/tests.yml`, nur Linux, Python 3.11 und 3.13). Den Mac-Weg (Papierkorb, Browser) prüft nur der lokale Lauf: vor jedem PR `python3 -m unittest` auf dem Mac ausführen. Die Server-Tests arbeiten mit harmlosen Befehlen und Temp-Dateien, nie gegen die echte Konfiguration.

## Was immer gilt

Diese Vorgaben stehen auch in den Regeln der Module, mit ihrem Grund. Hier stehen sie, weil eine Regel erst lädt, wenn ihre Datei angefasst wird.

- `dashboard*.html` und `inventar*.json` im Datenordner sind erzeugt: nicht von Hand bearbeiten.
- Welche Aktion ein Eintrag anbietet und mit welchem Befehl, legen `aktionen()` in `claude.py` bzw. `codex.py` fest, für Regeln und Anweisungen `rechte_aktionen()` und `anweisungen_aktionen()` in `build.py`. Der Server führt nur aus, was so im Inventar steht. Text schreibt er nur an zwei Stellen in eine neue Datei: einen Agenten, Skill, Befehl oder Output-Style aus dem Tab „Erstellen“ (`anlegen()`) und einen Agenten aus der Suche, nachdem der Benutzer den Text gesehen hat (`agent_vorschau()`).
- Den Befehl zum Umsetzen baut immer `befehl()`, nie die Bewertung: Sie stammt aus einem Lauf, der fremde Skill-Texte liest, und der Befehl steht im Dashboard neben dem Kopierknopf. Aus demselben Grund übernimmt `eingebaute()` von `eingebaut` nur `name`, `was` und `tokens`, und Hinweise zeigen keine Befehle.
- Ein Absturz in `build.py` verhindert den Serverstart: Fehler je Quelle abfangen und über `lesefehler()` melden.
- `SKILLS_CLI` in `harness.py`: eine neue Version erst prüfen, dann eintragen.
- Im Template: Farben nur aus dem Brand-Kit, neue Flächen auf `var(--bg)` statt `#fff`, Bestätigungen über `frage()`, Erläuterungen über `erklaert()`.

## Module

Die Einzelheiten zu jedem Modul stehen in `.claude/rules/<Modul>.md`: wie es arbeitet und warum es so gebaut ist, meist mit dem Fehler, der dazu führte. Claude Code lädt eine Regel, sobald es eine ihrer Dateien mit Read, Edit oder Write anfasst (nachgemessen mit `claude -p`), aber nicht über die Shell (`grep`, `sed`, ein Skript). Vor einer Änderung an einem Modul deshalb seine Regel lesen, wenn sie nicht schon im Kontext steht. Neues Wissen zu einem Modul gehört in seine Regel, ein Satz je Zeile; hierher nur, was für jede Sitzung gilt. Codex lädt `.claude/rules/` nie von selbst: Vor einer Änderung an einem Modul seine Regel selbst lesen. Die Module liegen alle in `inventur/`; die gleichnamigen Dateien im Hauptordner sind nur Startbefehle.

- `build.py`: lässt den Bestand lesen und schreibt Inventar, Verlauf und Dashboard für einen Harness (`--harness` oder den zuletzt gemerkten). → `.claude/rules/build.md`
- `claude.py` liest den Bestand von Claude Code, das Gegenstück zu `codex.py`; `mcp_direkt.py` fragt HTTP-MCP-Server selbst. Beide → `.claude/rules/claude-py.md` (nicht `claude.md`: Auf dem Mac gälte die Datei als `CLAUDE.md` und lüde bei jeder Regel mit)
- `harness.py`: Datenordner (`DATEN`), gemerkter Harness (`harness.json`) und die feste Version in `SKILLS_CLI`; von `build.py`, `claude.py`, `codex.py` und `server.py` importiert. Keine eigene Regel, die Vorgaben stehen oben unter „Was immer gilt“.
- `codex.py` liest denselben Bestand aus `~/.codex` und `~/.agents`, in derselben Datenform. → `.claude/rules/codex.md`
- `bestand.py`: was beide Leser brauchen (lesen ohne Absturz, IDs, Zugangsdaten verdecken, `bewerte()`); importiert weder `build` noch `claude` noch `codex`. → `.claude/rules/bestand.md`
- `server.py` zeigt das Dashboard und führt die gewählten Aktionen aus, startet „neu bewerten“ und die Suche und legt im Tab „Erstellen“ eigene Bausteine an. → `.claude/rules/server.md`
- `konfig.py` ändert für den Server die Konfigurationsdateien: ein Eingang, ein Schreibweg mit Sicherung und Rücknahme. → `.claude/rules/konfig.md`
- `inventur/template.html` ist die Oberfläche, dazu Icons und Logo. → `.claude/rules/template.md`
- `anweisungen.py`: Tab „Anweisungen“, was als Text in die Sitzung lädt. → `.claude/rules/anweisungen.md`
- `berechtigungen.py`: Tab „Berechtigungen“, was ohne Rückfrage erlaubt ist. → `.claude/rules/berechtigungen.md`
- `modelle.py`: Tab „Modelle“, was eingestellt ist und was lief. → `.claude/rules/modelle.md`
- `werkzeuge.py`: Tab „Versionen“, Laufzeiten und MCP-Server aus Quellcode. → `.claude/rules/werkzeuge.md`
- `wochenlauf.py`: wöchentlicher Lauf mit Mitteilung. → `.claude/rules/wochenlauf.md`
- `docs/`: vier Diagramme, erzeugt mit dem Skill `archify`. Die JSON anpassen und neu ausliefern, nicht das HTML bearbeiten. → `.claude/rules/docs.md`
