---
paths:
  - "modelle.py"
  - "test_modelle.py"
---

# modelle.py

`modelle.py` liest für den Tab „Modelle“, welches Modell und welcher Aufwand eingestellt sind und was lief; `modelle_lesen()` in `build.py` ruft es nach den Berechtigungen auf, ein Fehler kostet nur den Tab (`lesefehler`).
Eingestellt, Claude Code: `SCHLUESSEL` und im `env`-Block `UMGEBUNG` aus denselben Dateien wie die Berechtigungen (`VERWALTET`, `~/.claude/settings.json`, je Projekt `.claude/settings.json` und `.claude/settings.local.json`), dazu `model` und `effort` im Kopf der Agenten, Skills und Befehle, die das Inventar schon führt (`build.py` reicht Pfad und Kopf durch, das Modul sucht keine Dateien selbst).
Codex: die Schlüssel mit `model` in `~/.codex/config.toml`, auch in `profiles`.
Gemessen im Fenster `MESS_TAGE`: unter Claude Code je Antwort `message.model`, `cwd`, `isSidechain` (Subagent), `effort` und aus `usage` Ausgabe, Eingabe und beide Cache-Zähler (`FELDER`); eine Antwort steht je Inhaltsblock in einer eigenen Zeile und nach dem Fortsetzen einer Sitzung in zwei Dateien, sie zählt deshalb einmal je `message.id`, mit dem letzten Stand ihrer Tokens.
Den Ordner schlägt `projekt_von()` seinem Projekt zu: Ein Subagent arbeitet oft in `<Projekt>/.claude/worktrees/…`, eine Sitzung wechselt in Unterordner, und beides stand sonst als eigenes Projekt da (86 Zeilen statt 8).
Es gilt das längste eingetragene Projekt über dem Ordner; `~` zählt nur selbst, sonst schluckte es alles darunter.
Läufe in Temp-Ordnern (`TEMP`, etwa „neu bewerten“) heißen jedes Mal anders und stehen zusammen als „temporäre Ordner“.
Dazu zählen die Arbeitsordner der Desktop-App für Sitzungen ohne Projektordner (`SCRATCH`, unter `~/Library/Application Support/Claude/scratch-workspaces`), und zwar vor den eingetragenen Projekten: Claude Code trägt so einen Ordner als Projekt ein, und er stand im Tab „Projekte“ wie ein eigenes.
Unter Codex je `turn_context` Ordner, Modell und Aufwand, die `token_count`-Ereignisse danach (`last_token_usage`) gehören zu diesem Turn, und `input_tokens` enthält dort auch, was aus dem Cache kam (neu ist der Rest); gelesen werden nur Sitzungsdateien, die im Fenster geändert wurden.
Welches Modell am Ende gilt, rechnet das Modul bewusst nicht aus: Die Rangfolge der Quellen ist nicht sicher belegt, und ein Subagent bekommt sein Modell oft erst beim Aufruf.
„überschreibt“ heißt deshalb nur, dass ein Wert vom globalen abweicht (`inherit` nie).
Keine Marken wie „veraltet“ oder „teuer“: Sie bräuchten eine Modell-Liste im Code, die selbst veraltet.
Nur Anzeige, keine Aktionen.
Die Tests von `build.main` ersetzen auch diese beiden Lesefunktionen.
