---
paths:
  - "inventur/berechtigungen.py"
  - "tests/test_berechtigungen.py"
---

# berechtigungen.py

`berechtigungen.py` liest für den Tab „Berechtigungen“, was der Harness ohne Rückfrage darf; `rechte()` in `build.py` ruft es nach dem Inventar auf, ein Fehler kostet nur den Tab (`lesefehler`).
Claude Code: `permissions` aus `VERWALTET`, `~/.claude/settings.json` und je Projekt aus `.claude/settings.json` und `.claude/settings.local.json`; `~` ist auch ein Projekt, dessen `.claude/settings.json` ist die Benutzerdatei und wird nicht doppelt gelesen.
Codex: `approval_policy` und `sandbox_mode` (auch in `profiles`), `prefix_rule(…)` aus `~/.codex/rules/*.rules` (je Zeile, was nicht parst, steht als „unlesbar“ da) und `projects.*.trust_level`.
Marken: „breit“ nur bei Erlaubnissen (ganzes Werkzeug aus `GANZ`, ganzer MCP-Server, ein Programm aus `INTERPRETER` mit beliebigen Argumenten, `bypassPermissions`, `~` oder `/` als Ordner, `never`, `danger-full-access`, eine Codex-Regel für ein ganzes Programm oder eine Shell mit `-c`), „verwaist“ (MCP-Regel ohne eingetragenen Server, vertrauter Ordner, den es nicht gibt), „widerspricht“ (dieselbe Regel mit anderer Art im selben Bereich oder global), „doppelt“ (eine Projektregel, die mit derselben Art schon global gilt).
Was markiert ist, lässt sich über den Server entfernen: `rechte_aktionen()` in `build.py` gibt der Zeile eine ID und die Aktion `entfernen`, der Server nimmt mit `regel_entfernen` genau diese Regel aus `permissions` ihrer Datei (jede Zeile trägt dafür `datei`; `quelle` ist nur der Anzeigename) und unter Codex mit `toml_tabelle_entfernen` einen verwaisten vertrauten Ordner aus `config.toml`, jeweils mit Sicherung.
Damit schreibt der Server erstmals in Projektordner; er nimmt deshalb nur `.claude/settings.json` und `.claude/settings.local.json` an, denn der Pfad kommt aus dem Inventar.
Die ID nennt die Stelle (`recht:<Datei>:<Art>:<Regel>`), keine Nummer: Nach einem neuen Lauf träfe ein Klick sonst die Nachbarzeile.
Keine Aktion für verwaltete Einstellungen, für `defaultMode` und Ordner (keine Liste) und für die Regeln in `~/.codex/rules`.
Eine breite Regel enger zu fassen bleibt Handarbeit, das braucht ein Urteil.
Im Dashboard zeigt der Reiter „Auffällig“ nur, was markiert ist; der zweite je Projekt eine zugeklappte Zeile, aufgeklappt nach Werkzeug gebündelt (`Bash(git push *)` steht in der Zeile „Bash“ als `git push *`), weil ein Projekt Dutzende Regeln sammelt, die sich nur im Befehl unterscheiden.
Unter Codex ist das Werkzeug das Programm, auch mit Pfad davor (`/bin/zsh -lc …` zählt zu `zsh`): Als ganze Regel in der ersten Spalte sprengte es die Tabelle.
Die Tests von `build.main` ersetzen beide Lesefunktionen, sonst läsen sie die echten Einstellungen.
