---
paths:
  - "build.py"
  - "test_build.py"
---

# build.py

`build.py` liest den Bestand aus `~/.claude`, `~/.claude.json` und `~/.agents` und schreibt `dashboard.html` und `inventar.json` in den Datenordner.
Beide sind erzeugt: nicht von Hand bearbeiten.
Geschrieben wird über `harness.schreiben()` (erst eine eigene Zwischendatei je Aufruf per `mkstemp`, dann ein Tausch; ein fester Name `<datei>.neu` kollidierte bei zwei gleichzeitigen Läufen), ebenso Verlauf, `harness.json` und die zurückgeschriebene Bewertung: Der Server liest diese Dateien bei jedem Klick und Seitenaufruf, auch während `build.py` läuft.
Was nur in einem Projekt gilt, steht im Tab „Projekte“, die Tabs unter „Bestand“ sind global: Skills und Befehle aus `<Projekt>/.claude`, MCP-Server aus `<Projekt>/.mcp.json` und lokal in `~/.claude.json`, jeweils für die Projekte in `~/.claude.json`.
Ihre IDs tragen den Ordnernamen des Projekts (`skill:<Projekt>/<Name>`); haben zwei Projekte mit demselben Ordnernamen Einträge, steht dort der ganze Pfad (`projekt_ids()`), sonst teilten sie sich eine ID und eine Aktion träfe das erste.
Aus demselben Grund trägt der MCP-Server eines Plugins, das es in zwei Marktplätzen gibt, den Marktplatz in der ID; Claude Code führt ihn aber ohne, deshalb hängen Live-Status, Sperre und Messung dort am Feld `schluessel`, wie bei den Skills solcher Plugins.
Liegt unter Codex derselbe Projekt-Skill in `.agents/skills` und `.codex/skills`, zählt der erste.
Mods stehen bei den Hooks, mit `mod: true`: Hook-Module, die `hooks/hooks.json` eines Plugins unter `modules` nennt, also Code, der in der Sitzung mitläuft und Werkzeug-Aufrufe, Prompt und System-Prompt ändern kann.
`mod_eintraege()` liest die genannte Datei nur als Text und nimmt je `on('<Ereignis>'` einen Eintrag (ID `hook:<Plugin>:mod:<Ereignis>`); der Pfad kommt aus einer fremden Datei, was aus dem Plugin herausführt, bleibt ungelesen.
Steht ein `on(` nur in einer importierten Datei, fehlt es; ohne Fund bleibt deshalb die Zeile „Mod“ mit dem Ereignis `unbekannt`, sonst wäre das Modul unsichtbar.
Gemessen wird an Mods nichts, die Transkripte führen sie nicht.
`ordner_plugins()` liest Hooks und Mods auch aus Plugins, die ohne Installation aus einem Ordner laden: Skill-Ordner mit eigener `.claude-plugin/plugin.json`, die Ordner aus `CLAUDE_CODE_PLUGIN_DIRS` (Umgebung von `build.py` und `env` in `settings.json`) und `~/.claude/dev-mods/<Sitzung>/<Mod>` (Status „nur eine Sitzung“: Sie laden nur dort, wo sie entstanden, und nur nach Freigabe).
Ihre ID trägt den Ordner, und das Feld `ordner` sagt `aktionen()` und `befehl()`, dass der Eintrag weder zu einem Plugin noch in `settings.json` gehört: keine Aktion, sonst suchte „deinstallieren“ den Block in `settings.json`.
Was nur in einem Projekt läuft, liest `projekt_hooks()` in ein eigenes Feld (`projekt_hooks`): `hooks` aus `.claude/settings.json` und `.claude/settings.local.json` des Projekts und Skill-Ordner mit eigener `plugin.json` unter `.claude/skills`, je Projekt aus `~/.claude.json`; ein geklontes Repo bringt beides mit.
Die ID trägt die Datei oder den Ordner.
Sie stehen nur im Tab „Projekte“ und bewusst nicht in `alle`: keine Bewertung, keine Aktion (ein Hook in den Einstellungen eines Projekts gehört oft zum Repo, ein Klick wäre eine Änderung in Git), nur der Text von `befehl()` zum Kopieren; keine Messung, weil die Transkripte Laufzeit und Ausgabe je Ereignis und Befehl führen, nicht je Projekt; nicht in der Kontextlast, wie Projekt-Skills.
Über `ID_ARTEN` meldet die Karte im Überblick aber, wenn in einem Projekt ein Hook dazukommt.
Codex kennt keine Mods, aber Hooks je Projekt: `projekt_hooks()` in `codex.py` liest `[hooks]` aus `<Projekt>/.codex/config.toml` (ohne die Vertrauensliste `state`) und `<Projekt>/.codex/hooks.json`, für die Projekte aus `[projects]`, in dasselbe Feld und mit derselben Anzeige.
Belegt an der Doku und an `codex-rs/hooks/src/engine/discovery.rs`, nicht am laufenden Codex ausprobiert.
Der Status folgt dem Vertrauen in das Projekt („aktiv“ oder „nicht vertraut“), wie bei den Projekt-Servern.
Codex führt einen Hook außerdem erst aus, wenn der Benutzer ihn mit `/hooks` geprüft hat; das hängt an einem Hash je Hook in `hooks.state` und steht nicht im Inventar, auch bei den globalen Hooks nicht: „aktiv“ heißt dort nur „würde laden“.
Was Hooks an Claude zurückgeben, misst `build.py` in den Transkripten der letzten `HOOK_TAGE` Tage unter `~/.claude/projects`.
Aus denselben Transkripten kommen die Laufzeiten der Hooks (`durationMs` in jeder Hook-Ausgabe und in der Zusammenfassung am Turn-Ende, die auch Stop-Hooks ohne Ausgabe erfasst) und je MCP-Server die Zahl seiner Werkzeuge, seine Aufrufe und sein Anweisungsblock in Tokens.
Codex protokolliert weder Werkzeuglisten noch Hook-Laufzeiten: dort bleiben diese Felder `null` und das Dashboard schreibt „nicht gemessen“.
HTTP-Server ohne Zugangsdaten fragt `mcp_direkt.py` deshalb selbst, in beiden Harnesses (`initialize`, `tools/list`, einmal am Tag über den `Zwischenspeicher` der Werkzeuge, Schlüssel ist ein Hash der URL, weil in der Query ein Geheimnis stehen kann); seine Werte gehen denen aus den Transkripten vor (`messung`: `direkt` oder `Transkript`).
Nie stdio-Server (hieße fremde Programme starten), nie Server mit `headers`, `headersHelper`, `oauth` oder den Token-Schlüsseln von Codex, nie Umleitungen (urllib folgte sonst auch nach `ftp:`), nur `http`/`https`.
Verlangt ein Server auf `initialize` eine Anmeldung (401), merkt `mcp_direkt` sich nur das (`OAuth`, wenn er per `resource_metadata` auf seinen Anbieter verweist); die Werte aus den Transkripten bleiben.
`netz()` in `build.py` kennzeichnet Server im Netz (nicht `localhost`, `127.0.0.1`, `::1`) mit der Domain aus der Konfiguration und der Anmeldung, dazu kommen `anweisungen` (der Anweisungsblock allein) und `lesend` (Werkzeuge mit `readOnlyHint`, eine Angabe des Servers).
`tokens` eines MCP-Servers sind Anweisungsblock plus Werkzeugnamen, wie sie in der Werkzeugliste stehen (`mcp__<Server>__<Name>`); die Schemata (`schemata`) laden erst bei Bedarf und zählen nicht zur Kontextlast.
Je Lauf hält `verlauf-<harness>.json` eine Zeile mit Datum, Kontextlast und Anzahl je Art; mehrere Läufe am selben Tag überschreiben sich.
Dazu die IDs je Art (`ids`), aber nur heute und beim letzten Lauf davor, sonst wüchse die Datei täglich um den ganzen Bestand.
`aenderungen()` vergleicht beide, deshalb mit dem Tag davor und nicht mit dem letzten Lauf: Nach dem zweiten Lauf eines Tages wäre sonst alles „unverändert“.
Eine Art, die der alte Stand nicht kennt, zählt nicht als neu.
Ins Inventar und ins Dashboard kommt nur das Ergebnis (`aenderungen`), nicht die IDs; das Dashboard zeigt es als Karte oben im Überblick.
Die Kontextlast zählt Skills, Hooks, MCP-Anweisungen, Werkzeugnamen und die globalen Anweisungsdateien (`anweisungen.py`), aber nicht die eingebauten Skills des Harness: Codex legt sie als Dateien ab und könnte sie messen, Claude Code spielt sie zur Laufzeit ein.
Sonst hieße dieselbe Kachel in beiden Dashboards gleich und meinte Verschiedenes.
Ob Skills aus `npx skills` ein Update haben, fragt `build.py` per `gh api` bei GitHub ab (Ordner-SHA gegen `skillFolderHash`); ohne `gh` oder Netz fehlt nur diese Angabe, ebenso bei einer unerwarteten Antwort oder einem Lock-Eintrag ohne Repo, Pfad oder Stand.
Ein Absturz in `build.py` wiegt schwer, weil der Server ohne gelungenen Lauf nicht startet; ein Plugin-Eintrag ohne Installation oder ohne Pfad gilt deshalb als nicht installiert.
Einen unerwarteten Fehler in einem Plugin oder Projekt fangen `build.py` und `codex.py` je Quelle ab: Sie fällt weg, `lesefehler` im Inventar nennt sie, das Dashboard zeigt sie als Banner.
„ungenutzt“ setzt `ungenutzt_markieren()` für beide Harnesses: lädt in jede Sitzung, seit `UNGENUTZT_TAGE` nicht genutzt und länger installiert (bei Plugin-Bestandteilen zählt die Installation des Plugins, unter Codex mangels Datum der Stand seines Ordners).
Mit `--harness codex` schreibt es stattdessen über `codex.py` `inventar-codex.json` und `dashboard-codex.html`.
