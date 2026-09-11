# CLAUDE.md

Hinweise für Claude Code beim Arbeiten in diesem Repository.

## Projekt

Dashboard der installierten MCP-Server, Plugins und Skills von Claude Code, mit Bewertung von Überschneidungen und Empfehlungen, was entfernt werden kann.

## Aufbau

- `build.py` liest den Bestand aus `~/.claude`, `~/.claude.json` und `~/.agents` und schreibt `dashboard.html` und `inventar.json`. Beide sind erzeugt: nicht von Hand bearbeiten, nicht einchecken.
- `bewertung.json` enthält die Urteile: Beschreibung, Empfehlung, Überschneidungsgruppen, Hinweise. Gepflegt über den globalen Skill `/tool-inventar` (`~/.claude/skills/tool-inventar`).
- `template.html` ist die Oberfläche. Farben und Bausteine nur aus dem 3zero-Brand-Kit (`../3zero-Design-Kit/BRAND.md`). Signalfarben sind dort noch offen, deshalb unterscheiden sich die Empfehlungen per Form und Symbol statt per Farbe.
