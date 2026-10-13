---
paths:
  - "build.py"
  - "test_build.py"
---

# build.py

`build.py` ist der Startbefehl: Es lässt den Bestand lesen (`claude.inventar()` für Claude Code, `codex.inventar()` für Codex), ergänzt, was für beide Harnesses gilt, und schreibt `dashboard.html` und `inventar.json` in den Datenordner.
Wie Claude Code gelesen wird, steht in `claude-py.md`, für Codex in `codex.md`; hier steht nur, was nach dem Leser kommt.
Beide sind erzeugt: nicht von Hand bearbeiten.
Geschrieben wird über `harness.schreiben()` (erst eine eigene Zwischendatei je Aufruf per `mkstemp`, dann ein Tausch; ein fester Name `<datei>.neu` kollidierte bei zwei gleichzeitigen Läufen), ebenso Verlauf, `harness.json` und die zurückgeschriebene Bewertung: Der Server liest diese Dateien bei jedem Klick und Seitenaufruf, auch während `build.py` läuft.
Mit `--harness codex` schreibt es stattdessen über `codex.py` `inventar-codex.json` und `dashboard-codex.html`.
Je Lauf hält `verlauf-<harness>.json` eine Zeile mit Datum, Kontextlast und Anzahl je Art; mehrere Läufe am selben Tag überschreiben sich.
Dazu die IDs je Art (`ids`), aber nur heute und beim letzten Lauf davor, sonst wüchse die Datei täglich um den ganzen Bestand.
`aenderungen()` vergleicht beide, deshalb mit dem Tag davor und nicht mit dem letzten Lauf: Nach dem zweiten Lauf eines Tages wäre sonst alles „unverändert“.
Eine Art, die der alte Stand nicht kennt, zählt nicht als neu.
Ins Inventar und ins Dashboard kommt nur das Ergebnis (`aenderungen`), nicht die IDs; das Dashboard zeigt es als Karte oben im Überblick.
Weil mit dem Tag davor verglichen wird, steht die Karte den ganzen Tag bei jedem Öffnen da; der Knopf „gelesen“ blendet sie aus, der Browser merkt sich dafür genau diesen Stand (Datum, Neues, Entferntes), und mit einer weiteren Änderung kommt sie wieder.
Die Kontextlast zählt Skills, Hooks, MCP-Anweisungen, Werkzeugnamen und die globalen Anweisungsdateien (`anweisungen.py`), aber nicht die eingebauten Skills des Harness: Codex legt sie als Dateien ab und könnte sie messen, Claude Code spielt sie zur Laufzeit ein.
Sonst hieße dieselbe Kachel in beiden Dashboards gleich und meinte Verschiedenes.
„ungenutzt“ setzt `ungenutzt_markieren()` für beide Harnesses: lädt in jede Sitzung, seit `UNGENUTZT_TAGE` nicht genutzt und länger installiert (bei Plugin-Bestandteilen zählt die Installation des Plugins, unter Codex mangels Datum der Stand seines Ordners).
Was das Dashboard aus der Bewertung ableitet, rechnet Python und legt es fertig ins Inventar: je Eintrag `umgesetzt` und `wirksam`, oben `ersparnis` und `versionen_offen`.
`umgesetzt()` steht in jedem Leser neben `aktionen()`, dessen Gegenstück es ist: Was schon abgeschaltet oder auf „nur manuell“ gestellt ist, braucht keine Aktion mehr; die Merkmale dafür sind je Harness verschieden, deshalb je Leser eine Funktion statt einer gemeinsamen mit Schaltern.
`empfehlungen_ableiten()` gilt für beide: `wirksam` ist die eigene Empfehlung, bei Plugin-Bestandteilen ohne eigene die ihres Plugins (`geerbt`), und `ersparnis` zählt jede Tokenzahl einmal (geht ein Plugin ganz, zählen seine Teile nicht noch einmal).
Vorher rechnete das Template alle vier selbst, in der einzigen Datei ohne Tests und mit einer eigenen Kopie von `LAEDT` aus `codex.py`.
Welche Status im Tab „Versionen“ Handlungsbedarf bedeuten, steht einmal in `werkzeuge.ZU_TUN`; danach zählt `werkzeuge.offen()` und meldet der Wochenlauf.
