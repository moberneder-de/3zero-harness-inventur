---
paths:
  - "werkzeuge.py"
  - "test_werkzeuge.py"
---

# werkzeuge.py

`werkzeuge.py` prüft für den Tab „Versionen“ (er hieß „Werkzeuge“; so heißen aber auch die Funktionen eines MCP-Servers und `Bash` oder `WebFetch` in den Berechtigungen — Modul, Datenfeld `werkzeuge` und `werkzeuge.json` behalten ihren Namen), was die Inventur braucht (`GRUNDBEDARF`, dazu das CLI des Harness) und was MCP-Server und Hooks laut Befehl starten (`werkzeuge_in()`: `npx` gehört zu Node, `bunx` zu Bun, `uvx` zu uv, ein `.py`-Argument zu Python; ein Befehl mit Pfad zählt nur, wenn er auf dasselbe Programm zeigt wie der Name im PATH — Codex bringt ein eigenes Node mit, dessen Pflege Sache der App ist).
Installiert per `--version`, neueste von endoflife.date (mit Pflegeende je Versionslinie), npm und GitHub, je Quelle ein Versuch am Tag, gemerkt in `werkzeuge.json` im Datenordner; ohne Netz gilt der letzte Stand.
„veraltet“ heißt: in der eigenen Linie gibt es Neueres.
Bei einer Homebrew-Installation nur, wenn Homebrew das Neuere schon anbietet (`homebrew_version()`, `brew info` aus dem lokalen Stand, ohne Auto-Update); sonst „wartet auf Homebrew“, ohne Befehl und nicht in `ZU_TUN`.
Anlass: Node 26.12.0 stand bei endoflife.date, Homebrew hatte noch 26.11.1, und das Dashboard zeigte `brew upgrade node`, das nichts tat.
Kennt Homebrew eine Version erst nach seinem eigenen Update, steht bis dahin „wartet“, obwohl `brew upgrade` (das vorher aktualisiert) schon etwas fände.
Den Update-Befehl liest `update_befehl()` am echten Pfad ab (Homebrew-Cellar, npm global, `~/.bun`, `~/.local/share/claude`), sonst keiner.
Nur Anzeige und Kopiertext, kein Knopf: Ein Laufzeit-Update aus dem Browser könnte laufende MCP-Server und Hooks stören, und `SKILLS_CLI` soll erst geprüft werden.
Ein Fehler hier kostet nur den Tab (`lesefehler`).
Darunter prüft `quellcode_pruefen()` MCP-Server, die als Quellcode unter `~` liegen (`quellen()`: Pfade aus den Befehlen eigener und Projekt-Server, nicht aus Plugins; Wurzel ist der nächste Ordner mit `.git`, sonst mit `package.json`, nie `~` selbst oder etwas außerhalb wie `/opt/homebrew`, das selbst ein Git-Repo ist).
`upstream()` vergleicht mit demselben Zweig auf `origin`, sonst hieße ein Arbeitszweig „neuere Commits“; bei GitHub über die API mit Datum (ruhend ab `RUHEND_TAGE`), ohne Antwort (privates Repo) per `ls-remote` mit den Zugangsdaten von git.
Jeder git-Aufruf läuft mit `GIT_SICHER`: `core.fsmonitor` aus der `.git/config` eines fremden Klons startete bei `git status` sonst ein Programm (nachgemessen).
`pakete()` nimmt die direkten npm-Abhängigkeiten, die installierte Version aus `node_modules` und die Meldungen der GitHub Advisory Database zu genau dieser Version; der Befehl richtet sich nach der Lock-Datei.
Alle Netzantworten laufen über denselben `Zwischenspeicher` (ohne Anmeldung erlaubt GitHub 60 Anfragen je Stunde).
