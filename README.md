# 3zero-Harness-Inventur

Inventur der Laufzeitumgebung von Claude Code oder Codex: MCP-Server, Plugins, Skills, Befehle, Agenten und Hooks, global und in Projekten, mit Bewertung von Überschneidungen, Empfehlungen und Umsetzung per Dashboard.

## Wozu

Eine gewachsene Agenten-Umgebung wird unübersichtlich. Skills kommen über Plugins, über `npx skills`, als Symlink aus eigenen Repos oder als Kopie ohne Beleg. Mehrere tun dasselbe. Manche laden ihre Beschreibung in jede Sitzung und kosten Kontext, ohne je benutzt zu werden. Und was in `~/.claude` steht, gilt nicht in `~/.codex`.

Das Werkzeug beantwortet drei Fragen:

- **Was ist überhaupt installiert**, woher kam es, ist es aktiv, wie oft wurde es benutzt?
- **Was überschneidet sich**, und was kann deshalb weg?
- **Wie setze ich das um**, ohne die Befehle selbst zusammenzusuchen?

## Zwei Schichten

Das ist die zentrale Idee, und sie erklärt den Aufbau:

| Schicht | Inhalt | Wer pflegt sie |
|---|---|---|
| **Bestand** | Was installiert ist, Status, Herkunft, Nutzung, Kontextkosten | `build.py`, ohne Urteil, jederzeit neu erzeugbar |
| **Bewertung** | Kurzbeschreibung auf Deutsch, Empfehlung, Begründung, Überschneidungsgruppen, Hinweise | Handarbeit über den Skill, teuer neu zu erzeugen |

Der Bestand ist eine Messung. Die Bewertung ist ein Urteil. Beides getrennt zu halten heißt: Der Bestand darf jederzeit neu gelesen werden, ohne das Urteil zu verlieren.

## Schnellstart

```bash
python3 build.py                 # Bestand einlesen und Dashboard bauen
python3 server.py                # Dashboard auf http://127.0.0.1:8765 öffnen
```

### Jede Woche von selbst

```bash
python3 wochenlauf.py einrichten   # jeden Montag um 9 Uhr (macOS: launchd, Linux: systemd-Timer)
python3 wochenlauf.py entfernen
```

Der Lauf startet nur `build.py` für den zuletzt genannten Harness, ohne Bewertung und ohne Suche, und ändert nichts an der Konfiguration. Eine Mitteilung kommt nur, wenn es seit der letzten Meldung Neues zu tun gibt: ein Eintrag ist neu oder weg, ein Skill ist neu ungenutzt, ein Werkzeug ist veraltet, ohne Pflege oder fehlt, oder es gibt eine Sicherheitsmeldung. Was schon gemeldet war, kommt nicht jede Woche wieder. Ein Klick auf die Mitteilung öffnet unter macOS den Skripteditor, nicht das Dashboard; das öffnest du mit `/3zero-harness-inventur` oder `python3 server.py`. Hat der Rechner zur Uhrzeit geschlafen, läuft er beim Aufwachen.

Der Server lauscht nur auf `127.0.0.1` und zeigt die Seite nur mit dem Schlüssel des laufenden Servers in der Adresse: `127.0.0.1` erreicht jeder Benutzer des Rechners. Den Browser öffnet er selbst; zum erneuten Öffnen dient `~/.3zero-harness-inventur/start.html` (nur für dich lesbar) oder die Adresse aus dem Terminal. Ohne ihn lässt sich das erzeugte HTML auch direkt öffnen — dann ist es lesbar, aber ohne Aktionsknöpfe.

## Was das Dashboard zeigt

Sieben Tabs, drei davon Gruppen mit einer zweiten Zeile: **Überblick**, **Aufräumen** (Empfehlungen, Überschneidungen, Hinweise), **Bestand** (MCP-Server, Plugins, Skills, Hooks, Anweisungen), **Projekte**, **Einstellungen** (Berechtigungen, Modelle, Versionen), **Suche**, **Hilfe**.

Die Tabs unter **Bestand** zeigen Globales. **Projekte** zeigt alles zu einem Projekt an einer Stelle: was nur dort lädt (Skills, MCP-Server und Hooks aus den Einstellungen des Projekts), seine Regeln und die Modelle, die dort liefen. Projekt-Hooks sind nur angezeigt, ohne Bewertung und Knopf: Sie gehören oft zum Repo. Über jeder Tabelle steht ein Filter mit Suchfeld und, wo es passt, einer Auswahl je Spalte (Status, Empfehlung, Herkunft); er bleibt beim Scrollen stehen. Ein Klick auf eine Zeile klappt die Details auf: Beschreibung, Begründung, Autor, Quelle, Datei und den Befehl zum Umsetzen.

**Hooks** zeigt neben Befehlen auch **Mods** (Claude Code): Code eines Plugins, der in der Sitzung mitläuft und Werkzeug-Aufrufe, Prompt und System-Prompt ändern kann. In welche Ereignisse ein Mod eingreift, liest die Inventur am Text seines Moduls ab, ohne es auszuführen. Hooks und Mods kommen auch aus Ordnern, die ohne Installation laden: Skill-Ordner mit eigener `plugin.json`, `CLAUDE_CODE_PLUGIN_DIRS` und `~/.claude/dev-mods`.

**Anweisungen** zeigt, was der Harness als Text in die Sitzung lädt, zusätzlich zu seinem eingebauten Systemprompt: unter Claude Code `CLAUDE.md`, Regeln aus `rules`, den gewählten Output-Style, `MEMORY.md` und `AGENTS.md`, global und je Projekt, mit geschätzten Tokens; unter Codex die globale `AGENTS.md`. Über den Server lässt sich eine `CLAUDE.md` oder Regel ab- und wieder anschalten (`claudeMdExcludes`) und ein Output-Style wählen oder abwählen (`outputStyle`), beides nur in `~/.claude/settings.json`: Die Datei selbst bleibt, wie sie ist, denn sie liegt oft in einem Repo. Den eingebauten Systemprompt zeigt die Inventur nicht, er steht in keiner Datei. Ist das Plugin `claude-md-management` installiert, bietet jede `CLAUDE.md` eines Projekts außerdem **prüfen** an: Claude bewertet sie mit dem Skill `claude-md-improver` und zeigt Note, Befunde und Vorschläge als Karte. Der Lauf liest nur, und nur im Projekt. Umgesetzt wird in einer Sitzung dort: Der Bericht liegt als Datei im Datenordner, und der Befehl unter der Karte lässt Claude ihn umsetzen, ohne neu zu prüfen.

Der **Überblick** zählt auch, was **ungenutzt** ist: Skills, die ihre Beschreibung in jede Sitzung laden, seit 30 Tagen nicht benutzt wurden und länger installiert sind — die besten Kandidaten zum Aufräumen. Im Tab **Skills** zeigt der Statusfilter nur diese; Filter und Sortierung bleiben beim Neuladen erhalten.

**Versionen** zeigt, was die Inventur selbst und deine MCP-Server und Hooks laut ihrem Befehl brauchen (etwa Python, Node, Bun, `gh`, Claude Code oder Codex): installierte und neueste Version, Pflegeende der Versionslinie und, wo die Installationsart am Pfad erkennbar ist, den Befehl zum Aktualisieren (Homebrew, npm, `bun upgrade`, `claude update`). Aktualisiert wird nichts automatisch, auch nicht die feste Version von `skills`. Die neuesten Versionen kommen von endoflife.date, npm und GitHub, einmal am Tag. Ist ein Programm über Homebrew installiert, gilt es erst als veraltet, wenn Homebrew die neuere Version auch anbietet; bis dahin steht dort „wartet auf Homebrew“, ohne Befehl und ohne Meldung. Darunter stehen **MCP-Server aus Quellcode**, meist ein Git-Klon unter `~`: ob upstream Neueres vorliegt (verglichen mit demselben Zweig auf `origin`), ob das Projekt ruht (über 180 Tage ohne Commit) und welche Sicherheitsmeldungen die GitHub Advisory Database für die installierten npm-Abhängigkeiten kennt, mit `git pull` bzw. `bun update …`/`npm update …` zum Kopieren. Ein ruhendes Projekt hebt seine Abhängigkeiten nicht mehr selbst an.

**Berechtigungen** zeigt, was der Harness ohne Rückfrage darf, global und je Projekt: unter Claude Code `allow`, `ask`, `deny`, `defaultMode` und `additionalDirectories` aus den verwalteten Einstellungen, `~/.claude/settings.json` und `.claude/settings*.json` der Projekte; unter Codex `approval_policy` und `sandbox_mode` (auch in Profilen), die Regeln in `~/.codex/rules` und die vertrauten Ordner. Markiert ist, was sehr viel erlaubt (**breit**: ein ganzes Werkzeug, ein ganzer MCP-Server, ein Interpreter wie `Bash(python3:*)`, `bypassPermissions`, `danger-full-access`), was ins Leere zeigt (**verwaist**: Regel für einen Server, der nicht mehr eingetragen ist, oder ein vertrauter Ordner, den es nicht mehr gibt) was sich **widerspricht** und was im Projekt steht, obwohl es schon global gilt (**doppelt**). Der Reiter „Auffällig“ zeigt nur das Markierte; über den Server nimmt „entfernen“ eine solche Regel aus ihrer Datei (unter Codex einen verwaisten Ordner aus `config.toml`), mit Sicherung daneben. Der zweite Reiter zeigt alle Regeln je Projekt, nach Werkzeug gebündelt. Alles andere änderst du selbst.

**Modelle** zeigt, welches Modell und welcher Aufwand eingestellt sind und was wirklich lief. Eingestellt: unter Claude Code `model`, `effortLevel` und verwandte Schlüssel aus den verwalteten Einstellungen, `~/.claude/settings.json` und `.claude/settings*.json` der Projekte, auch im `env`-Block, dazu `model` und `effort` im Kopf von Agenten, Skills und Befehlen; unter Codex die Modell-Schlüssel in `~/.codex/config.toml`, auch in Profilen. **überschreibt** heißt: Der Wert weicht vom globalen ab. Gemessen: aus den Transkripten der letzten 14 Tage, je Projekt und Modell die Zahl der Antworten, die Tokens (Eingabe, Cache geschrieben und gelesen, Ausgabe) und der Aufwand, unter Claude Code getrennt nach Hauptsitzung und Subagent. Arbeitsordner von Subagenten und Unterordner zählen zu ihrem Projekt. Ein Subagent bekommt sein Modell oft erst beim Aufruf; das steht in keiner Datei, nur in der Messung. Nur Anzeige.

Lässt sich ein einzelnes Plugin oder Projekt nicht einlesen, fehlt nur diese Quelle; ein Banner oben nennt sie mit dem Fehler.

Die **Hilfe** im Dashboard erklärt jede Spalte und jede Zahl, passend zum gerade gezeigten Harness.

## Die fünf Empfehlungen

| Empfehlung | Bedeutung |
|---|---|
| `behalten` | Nützt und überschneidet sich nicht |
| `prüfen` | Hängt an einer Entscheidung, die nur du treffen kannst — die Begründung nennt sie |
| `entfernen` | Veraltet, unfertig und ungenutzt, doppelt, defekt oder ohne Bezug zur Arbeit |
| `abschalten` | Einzelner Skill eines Plugins, das bleibt |
| `nur manuell` | Gelegentlich nützlich, lädt aber seine Beschreibung in jede Sitzung |

Keine Empfehlung ohne Beleg aus dem Bestand. „Nie benutzt" trägt nicht, wenn etwas erst vor wenigen Tagen installiert wurde.

## Aktionen ausführen

Mit `server.py` bietet jede Zeile die Aktionen an, die zu ihr passen: **aktualisieren**, **deinstallieren**, **deaktivieren**, **nur manuell** und, für alles, was aus ist, die Gegen-Aktionen **einschalten** und **wieder automatisch**. Vorbelegt ist, was die Empfehlung nahelegt. Nach gelungenen Aktionen liest die Seite den Bestand selbst neu ein; schlägt eine fehl, bleibt die Meldung stehen. Bewertung und Suche laufen im Hintergrund, lassen sich abbrechen und halten Aktionen nicht auf.

Welche Aktion ein Eintrag anbietet und mit welchem Befehl, legt allein `aktionen()` in `claude.py` bzw. `codex.py` fest. Der Server führt nur aus, was so im Inventar steht — er baut keine Befehle aus Freitext. `npx skills` steht dort in fester Version (`SKILLS_CLI` in `harness.py`) und läuft mit leerem `--prefix`, damit ein Projektordner kein eigenes `skills` und keine eigene `.npmrc` unterschiebt. Einen Namen, der mit `-` beginnt, setzt der Server in keinen Befehl ein.

Vor der ersten Änderung an einer Konfigurationsdatei legt er eine Sicherung `<datei>.bak-<Zeitstempel>` an (eine je Serverlauf, die letzten 10 bleiben), schreibt atomar und nimmt die Änderung zurück, wenn die Datei danach nicht mehr parst. Eigene Skills und Befehle verschiebt er in den Papierkorb des Systems (macOS: `~/.Trash`, Linux: XDG-Papierkorb), unter einem freien Namen; unter Windows bleibt dafür nur das Löschen von Hand. Alles wirkt ab der nächsten Sitzung.

## Beide Harness

Inventarisiert wird der Harness, in dem der Skill zuletzt gestartet wurde:

```bash
python3 build.py --harness claude-code
python3 build.py --harness codex
```

Ohne Angabe gilt der zuletzt genannte. Die beiden Bewertungen sind getrennt, denn ein Skill kann in einem Harness sinnvoll und im anderen wirkungslos sein. `~/.agents/skills` liest allerdings jeder Harness — ein Skill von dort steht deshalb in beiden Beständen.

**Achtung bei den Nutzungszahlen:** Claude Code zählt jeden Aufruf, Codex nur eine Sitzung je Skill. Dieselbe Zahl bedeutet in den beiden Dashboards nicht dasselbe.

## Wo was liegt

Der Projektordner enthält **nur das Werkzeug**. Alles, was den Rechner beschreibt, liegt daneben:

```
~/.3zero-harness-inventur/
  bewertung-claude-code.json     Urteile für Claude Code
  bewertung-codex.json           Urteile für Codex
  inventar.json                  Bestand, erzeugt
  inventar-codex.json            Bestand, erzeugt
  dashboard.html                 Oberfläche, erzeugt
  dashboard-codex.html           Oberfläche, erzeugt
  harness.json                   welcher Harness zuletzt lief
  verlauf-claude-code.json       Kontextlast und Bestand je Tag, erzeugt
  verlauf-codex.json             Kontextlast und Bestand je Tag, erzeugt
  start.html                     Einstieg mit Schlüssel, nur solange server.py läuft
  werkzeuge.json                 Antworten aus dem Netz (Versionen, MCP-Messungen), einmal am Tag
  wochenlauf.json                was der wöchentliche Lauf schon gemeldet hat
  wochenlauf.log                 Ausgabe des wöchentlichen Laufs (macOS)
  pruefung-<Projekt>-<Kürzel>.md Bericht zur CLAUDE.md eines Projekts, vom Knopf „prüfen“
```

Fehlt eine Bewertung, gilt sie als leer und alles erscheint als „unbewertet". Das Werkzeug läuft also auch frisch geklont.

## Neu bewerten und Suchen

Drei Knöpfe starten einen Agenten im Hintergrund. Alle sind eng eingezäunt, weil sie fremde Texte lesen, die Anweisungen enthalten könnten.

**Neu bewerten** zieht die Bewertung auf den Stand des Bestands nach. Beide Harnesses arbeiten auf einer Arbeitskopie der Bewertung in einem Temp-Ordner; der Server prüft das Ergebnis als JSON-Objekt und schreibt es erst dann atomar zurück. Unter Claude Code läuft das als `claude -p --restricted` (Einstellungen und MCP-Server des Benutzers bleiben draußen), `--tools` gibt nur Lesen, Suchen und `Edit`, und `deny` in einer eigenen Einstellungsdatei (`--settings`) sperrt das Schreiben im ganzen Home. Den Skill bekommt der Lauf als Kopie per `--plugin-dir`. Unter Codex läuft es als `codex exec` in der Sandbox, ohne Netz. Liefert der Lauf Unsinn, bleibt die alte Bewertung stehen.

**Suchen** nimmt einen Bedarf in einem Satz, gleicht ihn zuerst mit dem Bestand ab und schlägt höchstens drei geprüfte Kandidaten vor — mit Herkunft, Verbreitung, Pflegezustand und Kontextkosten; Installationen, Sterne, letzter Push und Lizenz stehen als Zahlen am Kandidaten. „Nichts installieren" ist eine gültige Empfehlung. Der Lauf selbst darf nichts schreiben und nichts installieren; installiert wird erst nach ausdrücklicher Bestätigung, mit einem Befehl, den der Server aus geprüften Feldern baut.

**Prüfen** (nur Claude Code) bewertet die `CLAUDE.md` eines Projekts; der Knopf steht im Tab **Anweisungen** und ist dort beschrieben. Der Lauf liest nur, und nur im Projekt.

Der Skill dahinter heißt `/3zero-harness-inventur` (in Codex `$3zero-harness-inventur`) und liegt in `skill/SKILL.md`.

## Woher die Zahlen kommen

| Angabe | Quelle | Verlässlichkeit |
|---|---|---|
| Nutzung | `skillUsage` in `~/.claude.json`, bzw. `~/.codex/sessions` | gezählt |
| Kontextkosten | Länge von Name und Beschreibung ÷ 4 | Schätzung |
| Anweisungen | Länge von `CLAUDE.md`, Regeln, Output-Style und `MEMORY.md` ÷ 4; eingebundene Dateien (`@pfad`) zählen nicht mit. Die globalen zählen zur Kontextlast | Schätzung |
| Hook-Wirkung | Transkripte unter `~/.claude/projects` der letzten Tage, ÷ 4 | Schätzung |
| Hook-Laufzeit | `durationMs` in denselben Transkripten, Median und Maximum | gemessen |
| MCP-Server | Aufrufe aus denselben Transkripten. Werkzeuge und Kontext (Anweisungsblock und Werkzeugnamen, ÷ 4): bei HTTP-Servern ohne Anmeldung direkt per `initialize` und `tools/list`, einmal am Tag; sonst aus den Transkripten. Schemata laden erst bei Bedarf und stehen nur in den Details | gezählt |
| Update verfügbar | Skills: `gh api` gegen den Stand bei der Installation (`skillFolderHash`). Plugins (Claude Code): Version aus `plugin.json`, fester `sha` im Marktplatz oder, ohne beides, ob sich ihr Ordner seit dem installierten Stand geändert hat | genau |
| Herkunft | `plugin.json`, `.skill-lock.json`, Symlink-Erkennung | belegt |
| Ungenutzt | Nutzung und Installationsdatum, 30 Tage; unter Codex gilt für Plugins mangels Datum der Stand ihres Ordners | abgeleitet |

Umgebungswerte von MCP-Servern übernimmt das Inventar nie. Was in URL oder Argumenten eines Servers nach Zugangsdaten aussieht (Passwort in der URL, Werte in der Query, `--token …`, `…_KEY=…`, `Bearer …`), ersetzt es durch „…“, bevor das Ziel im Inventar und im Dashboard steht. Das erkennt Muster, keine Bedeutung.

Mods und Projekt-Hooks misst die Inventur nicht: Die Transkripte führen Mods gar nicht und Hooks nicht je Projekt. Codex protokolliert weder Werkzeuglisten noch Hook-Laufzeiten; dort steht „nicht gemessen“, außer bei HTTP-Servern ohne Anmeldung, die die Inventur selbst fragt. Server, die per Befehl starten, fragt sie nie: Das hieße fremde Programme auszuführen.

## Voraussetzungen

- **Python 3.11 oder neuer** (`tomllib`), keine fremden Pakete
- Optional: **`gh`** für die Update-Erkennung, **`npx`** für Skill-Aktionen (lädt `skills` in der festen Version), **`claude`** bzw. **`codex`** für die Hintergrundläufe
- Nur für `render-app-icon.py`: **Pillow** und `qlmanage` (macOS)
- Nur für `seitenvergleich.py`: ein Chromium-Browser (Brave, Chrome oder Chromium)

Sonst nichts. Das Projekt liest keine Datei außerhalb von sich selbst und dem Home-Verzeichnis.

## Tests

```bash
python3 -m unittest
```

Die Server-Tests arbeiten mit harmlosen Befehlen in Temp-Ordnern, nie gegen die echte Konfiguration. Auf GitHub laufen sie bei jedem Push unter Linux mit Python 3.11 und 3.13 (`.github/workflows/tests.yml`); den Mac-Weg (Papierkorb, Browser) prüft der lokale Lauf.

Für das JavaScript in `template.html` gibt es keine Tests. Nach einem Umbau dort vergleicht `python3 seitenvergleich.py` das Dashboard mit dem aus dem letzten Commit: den sichtbaren Text aller Tabs und was die Knöpfe an Regeln und Anweisungen auslösen.

## Diagramme

In `docs/` liegen vier eigenständige HTML-Diagramme mit ihren Quellen: der **Aufbau**, der **Bewertungslauf** (für beide Harnesses, mit dem Abbrechen), die **Herkunft der Kennzahlen** und die **sechs Schritte des Skills**. Einfach im Browser öffnen.

## Hinweis

Das Werkzeug läuft in jedem Ordner: Es liest `~/.claude`, `~/.codex` und `~/.agents`, weil die Werkzeuge dort liegen, und schreibt nach `~/.3zero-harness-inventur`. Sonst kennt es nur seinen eigenen Ordner.

Zwei Stellen bleiben auf 3zero zugeschnitten: das Logo in `wordmark.svg` und die Frage in `skill/SKILL.md`, ob ein Eintrag zur Arbeit des Benutzers passt. Beides ist Inhalt, nicht Technik.
