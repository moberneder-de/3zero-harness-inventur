# CLAUDE.md

Hinweise für Claude Code beim Arbeiten in diesem Repository.

## Projekt

Inventur der Claude-Code-Laufzeitumgebung: MCP-Server, Plugins, Skills, Befehle, Agenten und Hooks, global und in Projekten, mit Bewertung von Überschneidungen, Empfehlungen und Umsetzung per Dashboard, für Claude Code oder Codex. Die Skills aus `~/.agents` gelten auch für andere Agenten, das Inventar reicht also über Claude Code hinaus.

## Befehle

```bash
python3 -m unittest                      # alle Tests
python3 -m unittest test_build.Mods      # eine Testklasse
python3 build.py                         # Bestand neu einlesen, schreibt in ~/.3zero-harness-inventur
python3 server.py                        # Dashboard mit Knöpfen auf http://127.0.0.1:8765
python3 wochenlauf.py einrichten         # wöchentlicher Lauf, montags 9 Uhr; nur nach Bestätigung
python3 wochenlauf.py entfernen
```

- Voraussetzung: Python 3.11 oder neuer (`tomllib`), keine fremden Pakete. Ausnahme: `render-app-icon.py` braucht Pillow und `qlmanage` (nur macOS).
- `build.py --harness codex` merkt sich Codex als Harness: Ein laufender Server zeigt danach das Codex-Dashboard. Zum Ausprobieren stattdessen `codex.inventar()` oder eine einzelne Funktion in einem Skript aufrufen.
- Oberfläche prüfen, ohne den Datenordner zu ändern: `template.html` mit einem Inventar aus dem Datenordner füllen (`__DATEN__`, `__LOGO__`, `__ICON__`, `__APPICON__` ersetzen), als `$TMPDIR/vorschau.html` schreiben und aufnehmen:

  ```bash
  "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" --headless=new --screenshot="$TMPDIR/vorschau.png" --window-size=1440,2000 "file://$TMPDIR/vorschau.html"
  ```

  Knöpfe erscheinen nur, wenn `<script id="token">` nicht `null` enthält.
- Änderungen laufen über Branch und PR; gemergt wird, wenn beide Jobs bei GitHub grün sind (`gh pr merge --rebase --delete-branch`).

## Aufbau

- Datenordner: Alles, was zum Rechner des Benutzers gehört, liegt in `~/.3zero-harness-inventur` (`harness.DATEN`): beide Bewertungen, beide Inventare, beide Dashboards, beide Verläufe und `harness.json`. Der Projektordner enthält nur das Werkzeug und ist deshalb veröffentlichungsfähig. `build.py` legt den Ordner beim ersten Lauf an; fehlt eine Bewertung, gilt sie als leer und alles erscheint als „unbewertet“. In `server.py` ist `ordner` das Werkzeug und `daten` der Datenordner — wer beides verwechselt, lässt den Server wieder ins Repo schreiben.
- Harness: Der Skill nennt beim Start seinen Harness (`build.py --harness claude-code|codex`), `harness.py` merkt ihn sich in `harness.json`, der letzte Start zählt. Claude Code nutzt `inventar.json`, `dashboard.html`, `bewertung-claude-code.json`, `verlauf-claude-code.json`; Codex `inventar-codex.json`, `dashboard-codex.html`, `bewertung-codex.json`, `verlauf-codex.json`.
- `bewertung-claude-code.json` enthält die Urteile: Beschreibung, Empfehlung, Überschneidungsgruppen, Hinweise. Den Befehl zum Umsetzen baut immer `befehl()`, nie die Bewertung: Sie stammt aus einem Lauf, der fremde Skill-Texte liest, und der Befehl steht im Dashboard neben dem Kopierknopf. Aus demselben Grund übernimmt `eingebaute()` von `eingebaut` nur `name`, `was` und `tokens`, und Hinweise zeigen keine Befehle. Gepflegt über den Skill `/3zero-harness-inventur` in `skill/SKILL.md`; global verfügbar über den Symlink `~/.claude/skills/3zero-harness-inventur` auf diesen Ordner. Mit `suche <Bedarf>` sucht der Skill neue Skills, Plugins und MCP-Server, gleicht sie mit dem Bestand ab und installiert nur nach Bestätigung. Unter Codex gilt `bewertung-codex.json`, verfügbar über den Symlink `~/.codex/skills/3zero-harness-inventur`.
- Tests: Wer feste Daten in Transkripten verwendet, setzt `zeitgrenze` selbst, sonst fallen sie nach 14 Tagen aus dem Messfenster. Auf GitHub laufen sie bei jedem Push (`.github/workflows/tests.yml`, nur Linux, Python 3.11 und 3.13). Den Mac-Weg (Papierkorb, Browser) prüft nur der lokale Lauf: vor jedem PR `python3 -m unittest` auf dem Mac ausführen. Die Server-Tests arbeiten mit harmlosen Befehlen und Temp-Dateien, nie gegen die echte Konfiguration.

## Was immer gilt

Diese Vorgaben stehen auch in den Regeln der Module, mit ihrem Grund. Hier stehen sie, weil eine Regel erst lädt, wenn ihre Datei angefasst wird.

- `dashboard*.html` und `inventar*.json` im Datenordner sind erzeugt: nicht von Hand bearbeiten.
- Welche Aktion ein Eintrag anbietet und mit welchem Befehl, legt allein `aktionen()` in `build.py` bzw. `codex.py` fest. Der Server führt nur aus, was so im Inventar steht.
- Ein Absturz in `build.py` verhindert den Serverstart: Fehler je Quelle abfangen und über `lesefehler()` melden.
- `SKILLS_CLI` in `harness.py`: eine neue Version erst prüfen, dann eintragen.
- Im Template: Farben nur aus dem Brand-Kit, neue Flächen auf `var(--bg)` statt `#fff`, Bestätigungen über `frage()`, Erläuterungen über `erklaert()`.

## Module

Die Einzelheiten zu jedem Modul stehen in `.claude/rules/<Modul>.md`: wie es arbeitet und warum es so gebaut ist, meist mit dem Fehler, der dazu führte. Claude Code lädt eine Regel, sobald es eine ihrer Dateien mit Read, Edit oder Write anfasst (nachgemessen mit `claude -p`), aber nicht über die Shell (`grep`, `sed`, ein Skript). Vor einer Änderung an einem Modul deshalb seine Regel lesen, wenn sie nicht schon im Kontext steht. Neues Wissen zu einem Modul gehört in seine Regel, ein Satz je Zeile; hierher nur, was für jede Sitzung gilt.

- `build.py` liest den Bestand von Claude Code und schreibt Inventar, Verlauf und Dashboard; `mcp_direkt.py` fragt HTTP-MCP-Server selbst. → `.claude/rules/build.md`
- `harness.py`: Datenordner (`DATEN`), gemerkter Harness (`harness.json`) und die feste Version in `SKILLS_CLI`; von `build.py`, `codex.py` und `server.py` importiert. Keine eigene Regel, die Vorgaben stehen oben unter „Was immer gilt“.
- `codex.py` liest denselben Bestand aus `~/.codex` und `~/.agents`, in derselben Datenform. → `.claude/rules/codex.md`
- `bestand.py`: was beide Leser brauchen (lesen ohne Absturz, IDs, Zugangsdaten verdecken, `bewerte()`); importiert weder `build` noch `codex`. → `.claude/rules/bestand.md`
- `server.py` zeigt das Dashboard und führt die gewählten Aktionen aus, startet „neu bewerten“ und die Suche. → `.claude/rules/server.md`
- `konfig.py` ändert für den Server die Konfigurationsdateien: ein Eingang, ein Schreibweg mit Sicherung und Rücknahme. → `.claude/rules/konfig.md`
- `template.html` ist die Oberfläche, dazu Icons und Logo. → `.claude/rules/template.md`
- `anweisungen.py`: Tab „Anweisungen“, was als Text in die Sitzung lädt. → `.claude/rules/anweisungen.md`
- `berechtigungen.py`: Tab „Berechtigungen“, was ohne Rückfrage erlaubt ist. → `.claude/rules/berechtigungen.md`
- `modelle.py`: Tab „Modelle“, was eingestellt ist und was lief. → `.claude/rules/modelle.md`
- `werkzeuge.py`: Tab „Versionen“, Laufzeiten und MCP-Server aus Quellcode. → `.claude/rules/werkzeuge.md`
- `wochenlauf.py`: wöchentlicher Lauf mit Mitteilung. → `.claude/rules/wochenlauf.md`
- `docs/`: vier Diagramme, erzeugt mit dem Skill `archify`. Die JSON anpassen und neu ausliefern, nicht das HTML bearbeiten. → `.claude/rules/docs.md`
