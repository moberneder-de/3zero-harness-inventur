# CLAUDE.md

Hinweise für Claude Code beim Arbeiten in diesem Repository.

## Projekt

Dashboard der installierten MCP-Server, Plugins und Skills von Claude Code, mit Bewertung von Überschneidungen und Empfehlungen, was entfernt werden kann.

## Aufbau

- `build.py` liest den Bestand aus `~/.claude`, `~/.claude.json` und `~/.agents` und schreibt `dashboard.html` und `inventar.json`. Beide sind erzeugt: nicht von Hand bearbeiten, nicht einchecken.
- `bewertung.json` enthält die Urteile: Beschreibung, Empfehlung, Überschneidungsgruppen, Hinweise. Gepflegt über den globalen Skill `/3zero-tool-inventar` (`~/.claude/skills/3zero-tool-inventar`).
- `server.py` zeigt das Dashboard auf http://127.0.0.1:8765 und führt die dort gewählten Aktionen aus (deinstallieren, deaktivieren, nur manuell). Welche Aktion ein Eintrag anbietet und mit welchem Befehl, legt allein `aktionen()` in `build.py` fest; der Server führt nur aus, was so in `inventar.json` steht. Als Datei geöffnet bleibt das Dashboard nur lesbar.
- Tests: `python3 -m unittest`. Die Server-Tests arbeiten mit harmlosen Befehlen und Temp-Dateien, nie gegen die echte Konfiguration.
- `template.html` ist die Oberfläche. Farben und Bausteine nur aus dem 3zero-Brand-Kit (`../3zero-Design-Kit/BRAND.md`); das Logo bindet `build.py` beim Bauen aus `../3zero-Design-Kit/logo/wordmark-invers.svg` ein. Signalfarben sind dort noch offen, deshalb unterscheiden sich die Empfehlungen per Form und Symbol statt per Farbe.
