---
paths:
  - "konfig.py"
  - "test_konfig.py"
---

# konfig.py

`konfig.py` ändert die Konfigurationsdateien, die das Dashboard schalten kann: `~/.claude/settings.json`, `.claude/settings*.json` der Projekte, `~/.codex/config.toml` und `agents/openai.yaml` eines Skills.
Es gibt einen Eingang, `Konfig.ausfuehren(name, schritt)`, mit dem Schritt, wie ihn `aktionen()` ins Inventar schreibt; `None` heißt „nicht meins“ (Papierkorb, Befehl), dann macht der Server selbst weiter.
Der Server legt beim Start ein `Konfig` an und hält seine Sperre um jeden Aufruf; das Modul kümmert sich nicht um gleichzeitige Zugriffe.
Das Objekt merkt sich, welche Datei es schon gesichert hat (`gesichert`), deshalb eines je Serverlauf: Ein zweites sicherte dieselbe Datei noch einmal.
Jede Änderung nimmt denselben Weg, `ersetzen()`: sichern, atomar schreiben, nachlesen und zurücknehmen, wenn die Änderung nicht angekommen ist.
Vorher stand dieser Ablauf dreimal in eigener Fassung in `server.py`, für `settings.json` ohne Nachlesen.
Der Schritt kommt aus dem Inventar, einer Datei, die auch andere schreiben können; was er nennen darf, prüft deshalb dieses Modul selbst und nicht sein Aufrufer.
`regel_entfernen` nimmt nur `settings.json` oder `settings.local.json` in einem `.claude`-Ordner, `ausschliessen` nur einen ganzen Pfad zu einer `.md`-Datei ohne Musterzeichen, `toml_tabelle_entfernen` nur `[projects."<Pfad>"]`.
Die Antwort ist immer `{"ok", "ausgabe"}`; den Text zeigt das Dashboard, wie er ist.
Eine `settings.json`, die kein JSON ist, fängt das Modul nicht ab: `json.loads` wirft, bevor etwas geschrieben ist, und der Handler in `server.py` macht daraus die Fehlantwort.
`nur_manuell_gesetzt()` nutzt auch `codex.py` beim Einlesen: Läsen Leser und Schreiber `allow_implicit_invocation` verschieden, meldete das Dashboard „erledigt“, und der Bestand zeigte es nicht.
`codex.py` importiert von hier und nicht umgekehrt: Als das entstand, zog `codex.py` noch `build.py` nach, und das las beim Laden Dateien vom Rechner.
Beides gilt nicht mehr (`codex.py` holt seine Funktionen aus `bestand.py`, gelesen wird erst in `main()`), die Richtung ist geblieben: `konfig.py` importiert nur `harness`.
Eine neue Schritt-Art braucht einen Zweig in `Konfig.ausfuehren`; `server.py` muss von ihr nichts wissen.
`test_konfig.py` ruft das Modul direkt auf Temp-Dateien auf; in `test_server.py` beweist ein Test je Schritt-Art nur noch den Weg vom Inventar zur Datei.

## Sichern und Schreiben

Vor der ersten Änderung sichert er die Datei nach `<datei>.bak-<Zeitstempel>`, einmal je Datei und Serverlauf: Mit festem Namen überschrieb der nächste Lauf die Sicherung mit dem Stand, den der Server selbst schon geändert hatte.
Je Datei bleiben die letzten `SICHERUNGEN` (10), erkannt am Zeitstempel im Namen.
Er schreibt atomar und nimmt die Änderung zurück, wenn die Datei danach nicht mehr parst oder der Wert fehlt.
Tabellenköpfe vergleicht er über ihre Schlüssel (`toml_kopf()`), also auch mit Anführungszeichen oder Leerzeichen; steht die Stelle als Inline-Tabelle, mit gepunkteten Schlüsseln oder als Array, parst der neue Text nicht, und er schreibt nichts, sondern meldet „bitte von Hand“.
Zeilenenden (CRLF) und eine BOM bleiben erhalten.
Ist die Datei ein Symlink (Dotfiles-Repo), schreibt er ins Ziel: `os.replace` ersetzte sonst den Link selbst durch eine normale Datei.
Dasselbe gilt für die Bewertung im Datenordner.
