---
name: 3zero-harness-inventur
description: Aktualisiert das HTML-Dashboard der installierten MCP-Server, Plugins und Skills und zieht die Bewertung nach (Beschreibung, Überschneidungen, Empfehlungen).
disable-model-invocation: true
---

Das Dashboard hat zwei Schichten. `build.py` liest den Bestand ohne Urteil aus der Konfiguration: was installiert ist, ob es aktiv ist, woher es stammt, wie oft es genutzt wurde. `bewertung.json` enthält, was Urteil braucht: deutsche Kurzbeschreibung, Empfehlung, Begründung, Überschneidungsgruppen, Hinweise. Dieser Skill bringt die zweite Schicht auf den Stand der ersten.

Ordner: `/Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur`

Verlangt der Benutzer eine **komplette Neubewertung**, gehst du in Schritt 2 alle Einträge durch, nicht nur die neuen.

## 1. Bestand einlesen

```bash
python3 /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/build.py
```

Die Ausgabe nennt bis zu zwei Listen: „Noch nicht bewertet“ (neu installiert) und „Bewertet, aber nicht mehr installiert“ (inzwischen entfernt).

Fertig, wenn: das Skript ohne Fehler durchlief. Sind beide Listen leer und will der Benutzer keine Neubewertung, weiter mit Schritt 4.

## 2. Neue Einträge bewerten

Schlage jede ID in `inventar.json` nach (Feld `id`) und lies die Datei unter `pfad`. Bei Plugins reichen Beschreibung und Bestandteile. Trage dann in `bewertung.json` unter `eintraege` ein:

- `was`: ein Satz auf Deutsch, was der Eintrag tut. Keine Übersetzung der Beschreibung, sondern ihr Kern.
- `empfehlung`: nur für Plugins, eigenständige Skills und MCP-Server. Plugin-Bestandteile bekommen nur dann eine, wenn sie einzeln abgeschaltet werden sollen; sonst übernimmt das Dashboard die des Plugins. Werte:
  - `behalten`
  - `prüfen`: hängt an einer Entscheidung, die nur der Benutzer treffen kann. Nenne diese Entscheidung im `grund`.
  - `entfernen`: veraltet oder unfertig und ungenutzt, doppelt zu einem genutzten Eintrag, defekt, oder ohne Bezug zu Martins Arbeit (IT-Beratung, Requirements Engineering, eigene Web- und Supabase-Projekte).
  - `abschalten`: einzelner Skill eines Plugins, das bleibt (`skillOverrides: off`).
  - `nur manuell`: gelegentlich nützlich, lädt aber seine Beschreibung in jede Sitzung (`skillOverrides: user-invocable-only`).
- `grund`: ein bis zwei Sätze mit Belegen aus `inventar.json`: Nutzung, Überschneidung mit welchem Eintrag, `reife` (Upstream-Status), `tokens` (Kontextkosten).
- `autor`, `repo`, `url`: nur wenn die Herkunft im Dashboard als „unbekannt“ erscheint und du sie belegen kannst.

Keine Empfehlung ohne Beleg. „Nie genutzt“ trägt nicht, wenn der Eintrag erst vor wenigen Tagen installiert wurde (`installiert`).

Fertig, wenn: jede ID aus der Liste einen Eintrag mit `was` hat.

## 3. Entfernte Einträge austragen

Lösche die IDs aus „nicht mehr installiert“ aus `eintraege` und aus allen `gruppen[].mitglieder`. Eine Gruppe mit weniger als zwei Mitgliedern löst du auf.

Fertig, wenn: keine dieser IDs mehr in `bewertung.json` vorkommt.

## 4. Gruppen, Eingebautes und Hinweise nachziehen

- Gruppen: Gehört ein neuer Eintrag in eine bestehende Überschneidungsgruppe, oder bildet er mit anderen eine neue? Passe `einschaetzung` und `empfehlung` der betroffenen Gruppen an. Mitglieder stehen als ID in der Liste, eingebaute Skills als `builtin:<name>`.
- `eingebaut`: Gleiche die Liste mit den eingebauten Skills ab, die Claude Code dir in dieser Sitzung anbietet (ohne Plugin-Präfix und nicht in `~/.claude/skills`).
- `hinweise`: Prüfe jeden Hinweis nach (liegt die Datei noch da, tritt der Fehler noch auf?). Erledigte Hinweise löschen, neue Funde ergänzen.
- Begründungen, die Zahlen oder Daten nennen, gegen `inventar.json` prüfen und angleichen.
- `stand` auf das heutige Datum setzen.

Fertig, wenn: `python3 -m json.tool /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/bewertung.json > /dev/null` ohne Fehler durchläuft.

## 5. Neu erzeugen und öffnen

```bash
python3 /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/build.py && if curl -sf -o /dev/null http://127.0.0.1:8765/; then open http://127.0.0.1:8765/; else open /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/dashboard.html; fi
```

Nur über `server.py` zeigt das Dashboard die Knöpfe zum Ausführen; als Datei geöffnet bleibt es lesbar ohne Aktionen. Lief der Server nicht, nenne dem Benutzer in Schritt 6 den Start: `! python3 /Users/moberneder/Daten/600_Projekte/3zero-Harness-Inventur/server.py`.

Fertig, wenn: die Ausgabe weder unbewertete noch entfallene Einträge nennt.

## 6. Melden

Drei kurze Punkte an den Benutzer: was neu ist, was weggefallen ist, welche Empfehlungen sich geändert haben. Deinstalliere nichts selbst. Das Dashboard empfiehlt, der Benutzer entscheidet.
