---
name: 3zero-harness-inventur
description: Aktualisiert das HTML-Dashboard der installierten MCP-Server, Plugins, Skills, Projekt-Skills und Hooks und zieht die Bewertung nach (Beschreibung, Überschneidungen, Empfehlungen). Mit „suche <Bedarf>“ sucht er neue Skills, Plugins und MCP-Server, gleicht sie mit dem Bestand ab und installiert nach Bestätigung.
disable-model-invocation: true
argument-hint: "[suche <Bedarf>]"
---

Das Dashboard hat zwei Schichten. `build.py` liest den Bestand ohne Urteil aus der Konfiguration: was installiert ist, ob es aktiv ist, woher es stammt, wie oft es genutzt wurde. Die Bewertungsdatei (siehe Harness) enthält, was Urteil braucht: deutsche Kurzbeschreibung, Empfehlung, Begründung, Überschneidungsgruppen, Hinweise. Dieser Skill bringt die zweite Schicht auf den Stand der ersten.

Ordner: `/Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur`

## Harness

Der Skill läuft in Claude Code (Aufruf `/3zero-harness-inventur`) und in Codex (Aufruf `$3zero-harness-inventur`). Inventarisiert wird immer der Harness, in dem du gerade läufst, und das Dashboard zeigt den zuletzt genannten.

| | Claude Code | Codex |
|---|---|---|
| `<harness>` | `claude-code` | `codex` |
| Bewertung | `bewertung.json` | `bewertung-codex.json` |
| Inventar | `inventar.json` | `inventar-codex.json` |
| Dashboard | `dashboard.html` | `dashboard-codex.html` |

Setze in allen Befehlen unten `<harness>` ein und arbeite mit den Dateien aus deiner Spalte. Die Bewertungen der beiden Harnesses sind getrennt: Ein Skill in `~/.agents/skills` steht in beiden und wird in beiden bewertet.

Verlangt der Benutzer eine **komplette Neubewertung**, gehst du in Schritt 2 alle Einträge durch, nicht nur die neuen.

Beginnt die Anfrage mit **suche** (etwa `/3zero-harness-inventur suche Mermaid-Diagramme`), folgst du stattdessen dem Abschnitt „Suchen und installieren“ am Ende.

## 1. Bestand einlesen

```bash
python3 /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/build.py --harness <harness>
```

Die Ausgabe nennt bis zu zwei Listen: „Noch nicht bewertet“ (neu installiert) und „Bewertet, aber nicht mehr installiert“ (inzwischen entfernt). Unter Codex schreibt `build.py` damit außerhalb des Arbeitsordners und braucht für die Update-Prüfung (`gh api`) Netz; Codex fragt dann nach Freigabe. Ohne Netz fehlt nur die Update-Angabe.

Fertig, wenn: das Skript ohne Fehler durchlief. Sind beide Listen leer und will der Benutzer keine Neubewertung, weiter mit Schritt 4.

## 2. Neue Einträge bewerten

Schlage jede ID in dem Inventar deines Harness nach (Feld `id`) und lies die Datei unter `pfad`. Bei Plugins reichen Beschreibung und Bestandteile. Projekt-Skills (`projekt_skills`, ID `skill:<Projekt>/<Name>`) und Projekt-MCP-Server (`projekt_mcp`, ID `mcp:<Projekt>/<Name>`) laden nur in ihrem Projekt. Bei Hooks (`hooks`, ID `hook:<Quelle>:<Ereignis>`) lies die Skripte hinter `befehle`; `ausgabe` sagt, was sie laut Transkripten an Claude zurückgaben. In Codex gibt es keine Befehle und Agenten, dafür Apps als Plugin-Bestandteil; Plugins mit Status `im Konto` kommen aus dem ChatGPT-Konto, `wirkungslos` sind openai-curated-Einträge, die Codex wegen des Online-Katalogs ignoriert. Trage dann in deiner Bewertungsdatei unter `eintraege` ein:

- `was`: ein Satz auf Deutsch, was der Eintrag tut. Keine Übersetzung der Beschreibung, sondern ihr Kern.
- `empfehlung`: nur für Plugins, eigenständige Skills (auch in Projekten), eigene Hooks und MCP-Server. Plugin-Bestandteile, auch Plugin-Hooks, bekommen nur dann eine, wenn sie einzeln abgeschaltet werden sollen; sonst übernimmt das Dashboard die des Plugins. Projekt-Skills und Projekt-MCP-Server bekommen nur `behalten`, `prüfen` oder `entfernen`: Overrides und Sperren gelten global nach Namen. Werte:
  - `behalten`
  - `prüfen`: hängt an einer Entscheidung, die nur der Benutzer treffen kann. Nenne diese Entscheidung im `grund`.
  - `entfernen`: veraltet oder unfertig und ungenutzt, doppelt zu einem genutzten Eintrag, defekt, oder ohne Bezug zu Martins Arbeit (IT-Beratung, Requirements Engineering, eigene Web- und Supabase-Projekte).
  - `abschalten`: einzelner Skill eines Plugins, das bleibt (Claude Code: `skillOverrides: off`; Codex: `[[skills.config]] enabled = false`, läuft unter Codex jetzt auch über das Dashboard).
  - `nur manuell`: gelegentlich nützlich, lädt aber seine Beschreibung in jede Sitzung (Claude Code: `skillOverrides: user-invocable-only`; Codex: `policy.allow_implicit_invocation: false` in `agents/openai.yaml`, läuft unter Codex jetzt auch über das Dashboard).
- `grund`: ein bis zwei Sätze mit Belegen aus dem Inventar deines Harness: Nutzung, Überschneidung mit welchem Eintrag, `reife` (Upstream-Status), `tokens` (Kontextkosten; bei Hooks nur SessionStart je Sitzung, sonst `ausgabe`). In Codex zählt `nutzung` Sitzungen, nicht Aufrufe.
- `autor`, `repo`, `url`: nur wenn die Herkunft im Dashboard als „unbekannt“ erscheint und du sie belegen kannst.

Keine Empfehlung ohne Beleg. „Nie genutzt“ trägt nicht, wenn der Eintrag erst vor wenigen Tagen installiert wurde (`installiert`).

Fertig, wenn: jede ID aus der Liste einen Eintrag mit `was` hat.

## 3. Entfernte Einträge austragen

Lösche die IDs aus „nicht mehr installiert“ aus `eintraege` und aus allen `gruppen[].mitglieder`. Eine Gruppe mit weniger als zwei Mitgliedern löst du auf.

Fertig, wenn: keine dieser IDs mehr in deiner Bewertungsdatei vorkommt.

## 4. Gruppen, Eingebautes und Hinweise nachziehen

- Gruppen: Gehört ein neuer Eintrag in eine bestehende Überschneidungsgruppe, oder bildet er mit anderen eine neue? Passe `einschaetzung` und `empfehlung` der betroffenen Gruppen an. Mitglieder stehen als ID in der Liste, eingebaute Skills als `builtin:<name>`.
- `eingebaut`: In Claude Code gleichst du die Liste mit den eingebauten Skills ab, die Claude Code dir in dieser Sitzung anbietet (ohne Plugin-Präfix und nicht in `~/.claude/skills`). In Codex liest `build.py` die eingebauten Skills selbst aus `~/.codex/skills/.system`; du trägst nur je Name ein deutsches `was` ein.
- `hinweise`: Prüfe jeden Hinweis nach (liegt die Datei noch da, tritt der Fehler noch auf?). Erledigte Hinweise löschen, neue Funde ergänzen.
- Begründungen, die Zahlen oder Daten nennen, gegen das Inventar deines Harness prüfen und angleichen.
- `stand` auf das heutige Datum setzen.

Fertig, wenn: `python3 -m json.tool /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/<Bewertungsdatei> > /dev/null` ohne Fehler durchläuft.

## 5. Neu erzeugen und öffnen

```bash
python3 /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/build.py --harness <harness> && if curl -sf -o /dev/null --max-time 3 http://127.0.0.1:8765/; then open http://127.0.0.1:8765/; else open /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/<Dashboard>; fi
```

Nur über `server.py` zeigt das Dashboard die Knöpfe zum Ausführen; als Datei geöffnet bleibt es lesbar ohne Aktionen. Lief der Server nicht, nenne dem Benutzer in Schritt 6 den Start: `! python3 /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/server.py`. Unter Codex zeigt das Dashboard noch keine Knöpfe zum Ausführen; Befehle stehen zum Kopieren da.

Fertig, wenn: die Ausgabe weder unbewertete noch entfallene Einträge nennt.

## 6. Melden

Drei kurze Punkte an den Benutzer: was neu ist, was weggefallen ist, welche Empfehlungen sich geändert haben. Höchstens zehn Zeilen Markdown, keine Tabellen, keine Aufzählung aller Einträge: Aus dem Dashboard gestartet steht dieser Text als Karte im Seitenkopf. Deinstalliere nichts selbst. Das Dashboard empfiehlt, der Benutzer entscheidet.

## Suchen und installieren

Der Wert liegt im Abgleich: Nichts soll dazukommen, was ein installierter Eintrag schon kann. Skills und MCP-Server laufen mit den vollen Rechten des Benutzers, deshalb installierst du nur nach seinem ausdrücklichen Ja in dieser Sitzung. Läufst du ohne Benutzer (`claude -p` oder `codex exec`), schlägst du nur vor.

### S1. Bestand und Bedarf

Führe `build.py` aus (Schritt 1) und fasse den Bedarf in einem Satz. Ist er zu vage für eine Suche, stelle eine Rückfrage.

Fertig, wenn: der Bedarf in einem Satz feststeht.

### S2. Abgleich vorab

Durchsuche das Inventar deines Harness (`bewertung.was`, `beschreibung`, auch Plugin-Bestandteile, Projekt-Skills, MCP-Server und `eingebaut`) und die `gruppen` in deiner Bewertungsdatei nach Einträgen, die den Bedarf ganz oder teilweise abdecken. Deckt ein Eintrag ihn ab, nenne ihn mit ID und Beleg und frage, ob trotzdem gesucht werden soll.

Fertig, wenn: feststeht, was der Bestand schon abdeckt und was fehlt.

### S3. Suchen

- Skills: `npx skills find <Stichwörter>` (zeigt Installationen je Skill), mehrere Formulierungen. Was ein Repo sonst enthält: `npx skills add <owner/repo> -l`.
- Plugins: In Claude Code `claude plugin list --json --available` und in `available` nach Name und Beschreibung filtern. In Codex die Marktplatz-Dateien lesen: `~/.codex/.tmp/plugins/.agents/plugins/marketplace.json`, `~/.codex/.tmp/bundled-marketplaces/*/.agents/plugins/marketplace.json` und den Online-Katalog `~/.codex/cache/remote_plugin_catalog/*.json` (groß: mit `jq` oder `grep` nach Name und Beschreibung filtern).
- MCP-Server: Die CLI hat keine Suche. Suche im Web nach einem offiziellen Server des Anbieters und nimm den Installationsbefehl aus dessen Doku.

Führe die Suchbefehle aus einem Ordner außerhalb von `~` aus.

Fertig, wenn: je Art gesucht ist oder feststeht, dass es sie für den Bedarf nicht gibt.

### S4. Kandidaten prüfen

Belege je Kandidat, bevor du ihn nennst:

- Herkunft: Original statt Kopie. Dieselbe Datei liegt oft in vielen Repos; nimm das Repo, aus dem sie stammt.
- Verbreitung: ab 1.000 Installationen, unter 100 skeptisch. Bekannte Quellen (`anthropics`, `vercel-labs`, `microsoft`, offizielle Marktplätze, Anbieter selbst) vor unbekannten.
- Pflege: `gh api repos/<owner>/<repo> -q '.stargazers_count, .pushed_at'` (ohne `gh`: `curl -s https://api.github.com/repos/<owner>/<repo>`). Unter 100 Sternen oder ohne Commit seit Monaten skeptisch.
- Reife: liegt der Skill in `in-progress`, `experimental` oder `deprecated`?
- Kosten: Beschreibung ÷ 4 Tokens je Sitzung, `disable-model-invocation` kostet nichts. Bei MCP-Servern: worauf sie zugreifen und welche Anmeldung sie brauchen.
- Überschneidung: mit welchen IDs aus dem Bestand.

Fertig, wenn: höchstens drei Kandidaten mit Belegen übrig sind, oder keiner.

### S5. Vorschlagen

Nenne je Kandidat: was er tut, die Belege aus S4, die Überschneidung mit dem Bestand, den Installationsbefehl. Gib eine Empfehlung; „nichts installieren“ ist eine gültige. Dann warte auf die Antwort.

Fertig, wenn: der Benutzer entschieden hat.

### S6. Installieren

Nur, was der Benutzer bestätigt hat:

- Skill: `npx skills add <owner/repo> --skill <name> -g -y --agent <agent>` mit `<agent>` = `claude-code` bzw. `codex`
- Plugin: in Claude Code `claude plugin install <name>@<marktplatz>`, in Codex `codex plugin add <name>@<marktplatz>`
- MCP-Server: den Befehl aus der Doku des Anbieters, in Claude Code mit `-s user`, in Codex `codex mcp add …`

Fertig, wenn: der Eintrag in `npx skills list -g`, `claude plugin list` / `codex plugin list` oder `claude mcp list` / `codex mcp list` erscheint.

### S7. Inventur nachziehen

Gehe die Schritte 1 bis 6 oben durch: Der neue Eintrag erscheint als unbewertet, bekommt `was`, Empfehlung und, wo nötig, eine Gruppe. Neue Plugins und Skills wirken ab der nächsten Sitzung.

Fertig, wenn: Schritt 5 weder unbewertete noch entfallene Einträge nennt.
