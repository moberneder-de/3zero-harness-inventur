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

Der Server lauscht nur auf `127.0.0.1` und zeigt die Seite nur mit dem Schlüssel des laufenden Servers in der Adresse: `127.0.0.1` erreicht jeder Benutzer des Rechners. Den Browser öffnet er selbst; zum erneuten Öffnen dient `~/.3zero-harness-inventur/start.html` (nur für dich lesbar) oder die Adresse aus dem Terminal. Ohne ihn lässt sich das erzeugte HTML auch direkt öffnen — dann ist es lesbar, aber ohne Aktionsknöpfe.

## Was das Dashboard zeigt

Elf Tabs: **Überblick**, **Empfehlungen**, **Überschneidungen**, **MCP-Server**, **Plugins**, **Skills**, **Hooks**, **Projekte**, **Hinweise**, **Suche**, **Hilfe**.

Alle Tabs zeigen Globales, außer **Projekte** — dort steht, was nur in einzelnen Projektordnern gilt. Ein Klick auf eine Zeile klappt die Details auf: Beschreibung, Begründung, Autor, Quelle, Datei und den Befehl zum Umsetzen.

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

Mit `server.py` bietet jede Zeile die Aktionen an, die zu ihr passen: **aktualisieren**, **deinstallieren**, **deaktivieren**, **nur manuell** und, für alles, was aus ist, die Gegen-Aktionen **einschalten** und **wieder automatisch**. Vorbelegt ist, was die Empfehlung nahelegt. Bewertung und Suche laufen im Hintergrund und lassen sich abbrechen.

Welche Aktion ein Eintrag anbietet und mit welchem Befehl, legt allein `aktionen()` in `build.py` bzw. `codex.py` fest. Der Server führt nur aus, was so im Inventar steht — er baut keine Befehle aus Freitext.

Vor der ersten Änderung an einer Konfigurationsdatei legt er eine Sicherung `<datei>.bak-<Zeitstempel>` an (eine je Serverlauf, von Hand aufzuräumen), schreibt atomar und nimmt die Änderung zurück, wenn die Datei danach nicht mehr parst. Eigene Skills und Befehle verschiebt er in den Papierkorb des Systems (macOS: `~/.Trash`, Linux: XDG-Papierkorb), unter einem freien Namen; unter Windows bleibt dafür nur das Löschen von Hand. Alles wirkt ab der nächsten Sitzung.

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
```

Fehlt eine Bewertung, gilt sie als leer und alles erscheint als „unbewertet". Das Werkzeug läuft also auch frisch geklont.

## Neu bewerten und Suchen

Zwei Knöpfe starten einen Agenten im Hintergrund. Beide sind eng eingezäunt, weil sie fremde Texte lesen, die Anweisungen enthalten könnten.

**Neu bewerten** zieht die Bewertung auf den Stand des Bestands nach. Unter Claude Code läuft das als `claude -p` mit fester Werkzeugliste. Unter Codex als `codex exec` ohne Netz, auf einer Arbeitskopie in einem Temp-Ordner; der Server prüft das Ergebnis als JSON-Objekt und schreibt es erst dann atomar zurück. Liefert der Lauf Unsinn, bleibt die alte Bewertung stehen.

**Suchen** nimmt einen Bedarf in einem Satz, gleicht ihn zuerst mit dem Bestand ab und schlägt höchstens drei geprüfte Kandidaten vor — mit Herkunft, Verbreitung, Pflegezustand und Kontextkosten. „Nichts installieren" ist eine gültige Empfehlung. Der Lauf selbst darf nichts schreiben und nichts installieren; installiert wird erst nach ausdrücklicher Bestätigung, mit einem Befehl, den der Server aus geprüften Feldern baut.

Der Skill dahinter heißt `/3zero-harness-inventur` (in Codex `$3zero-harness-inventur`) und liegt in `skill/SKILL.md`.

## Woher die Zahlen kommen

| Angabe | Quelle | Verlässlichkeit |
|---|---|---|
| Nutzung | `skillUsage` in `~/.claude.json`, bzw. `~/.codex/sessions` | gezählt |
| Kontextkosten | Länge von Name und Beschreibung ÷ 4 | Schätzung |
| Hook-Wirkung | Transkripte unter `~/.claude/projects` der letzten Tage, ÷ 4 | Schätzung |
| Update verfügbar | `gh api` gegen den Stand bei der Installation (`skillFolderHash`) | genau |
| Herkunft | `plugin.json`, `.skill-lock.json`, Symlink-Erkennung | belegt |

Umgebungswerte und Tokens von MCP-Servern übernimmt das Inventar nie.

## Voraussetzungen

- **Python 3.11 oder neuer** (`tomllib`), keine fremden Pakete
- Optional: **`gh`** für die Update-Erkennung, **`npx`** für Skill-Aktionen, **`claude`** bzw. **`codex`** für die Hintergrundläufe
- Nur für `render-app-icon.py`: **Pillow** und `qlmanage` (macOS)

Sonst nichts. Das Projekt liest keine Datei außerhalb von sich selbst und dem Home-Verzeichnis.

## Tests

```bash
python3 -m unittest
```

Die Server-Tests arbeiten mit harmlosen Befehlen in Temp-Ordnern, nie gegen die echte Konfiguration.

## Diagramme

In `docs/` liegen vier eigenständige HTML-Diagramme mit ihren Quellen: der **Aufbau**, der **Codex-Bewertungslauf**, die **Herkunft der Kennzahlen** und die **sechs Schritte des Skills**. Einfach im Browser öffnen.

## Hinweis

Das Werkzeug läuft in jedem Ordner: Es liest `~/.claude`, `~/.codex` und `~/.agents`, weil die Werkzeuge dort liegen, und schreibt nach `~/.3zero-harness-inventur`. Sonst kennt es nur seinen eigenen Ordner.

Zwei Stellen bleiben auf 3zero zugeschnitten: das Logo in `wordmark.svg` und die Frage in `skill/SKILL.md`, ob ein Eintrag zur Arbeit des Benutzers passt. Beides ist Inhalt, nicht Technik.
