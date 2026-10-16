---
paths:
  - "wochenlauf.py"
  - "inventur/wochenlauf.py"
  - "tests/test_wochenlauf.py"
---

# wochenlauf.py

`wochenlauf.py` richtet einen wöchentlichen Lauf ein (`einrichten`: launchd-Agent `KENNUNG` in `~/Library/LaunchAgents`, unter Linux ein systemd-Timer mit `Persistent=true`; `entfernen`), montags um 9 Uhr.
Der Lauf ruft nur `build.py` auf, ohne Bewertung und Suche, und meldet per Mitteilung nur, was `offen()` findet und `wochenlauf.json` im Datenordner noch nicht als gemeldet führt; dort steht danach nur, was noch offen ist, sodass Erledigtes, das wiederkehrt, erneut gemeldet wird.
Die Namen gehen als Argument an `osascript` (`on run argv`), nie in den Skripttext.
`python3` kommt beim Einrichten aus dem PATH (Homebrew-Link statt versioniertem Pfad), PATH selbst steht im Agent, weil launchd nur `/usr/bin:/bin` setzt.
Ein Klick auf eine osascript-Mitteilung öffnet den Skripteditor; die Mitteilung nennt deshalb den Weg zum Dashboard.
Einrichten über den Skill (`wochenlauf einrichten`) nur nach Bestätigung.
