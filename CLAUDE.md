# CLAUDE.md

Hinweise für Claude Code beim Arbeiten in diesem Repository.

## Projekt

Inventur der Claude-Code-Laufzeitumgebung: MCP-Server, Plugins, Skills, Befehle und Agenten, mit Bewertung von Überschneidungen, Empfehlungen und Umsetzung per Dashboard. Die Skills aus `~/.agents` gelten auch für andere Agenten, das Inventar reicht also über Claude Code hinaus.

## Aufbau

- `build.py` liest den Bestand aus `~/.claude`, `~/.claude.json` und `~/.agents` und schreibt `dashboard.html` und `inventar.json`. Beide sind erzeugt: nicht von Hand bearbeiten, nicht einchecken.
- `bewertung.json` enthält die Urteile: Beschreibung, Empfehlung, Überschneidungsgruppen, Hinweise. Gepflegt über den globalen Skill `/3zero-harness-inventur` (`~/.claude/skills/3zero-harness-inventur`).
- `server.py` zeigt das Dashboard auf http://127.0.0.1:8765 und führt die dort gewählten Aktionen aus (deinstallieren, deaktivieren, nur manuell). Welche Aktion ein Eintrag anbietet und mit welchem Befehl, legt allein `aktionen()` in `build.py` fest; der Server führt nur aus, was so in `inventar.json` steht. Als Datei geöffnet bleibt das Dashboard nur lesbar. Der Knopf „Bewertung nachziehen“ startet über `/api/bewerten` einen eigenen Claude-Lauf (`claude -p "/3zero-harness-inventur"`, Opus) mit fester Werkzeugliste: Lesen, Schreiben und `python3`, sonst nichts.
- Tests: `python3 -m unittest`. Die Server-Tests arbeiten mit harmlosen Befehlen und Temp-Dateien, nie gegen die echte Konfiguration.
- `template.html` ist die Oberfläche. Farben und Bausteine nur aus dem 3zero-Brand-Kit (`../3zero-Design-Kit/BRAND.md`); das Logo bindet `build.py` beim Bauen aus `../3zero-Design-Kit/logo/wordmark.svg` ein (farbig wie auf 3zero.de, ohne den Zusatz „IT- Consulting“: `LOGO_MASSE` kürzt dafür die viewBox). Die Klasse `kopf` ist für Gruppenköpfe vergeben; der feste Seitenkopf heißt `seitenkopf`. Signalfarben sind dort noch offen, deshalb unterscheiden sich die Empfehlungen per Form und Symbol statt per Farbe.
