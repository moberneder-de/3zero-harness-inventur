---
paths:
  - "inventur/claude.py"
  - "tests/test_claude.py"
  - "inventur/mcp_direkt.py"
  - "tests/test_mcp_direkt.py"
---

# claude.py

`claude.py` liest den Bestand von Claude Code aus `~/.claude`, `~/.claude.json` und `~/.agents`, in die Datenform, die `template.html` erwartet; `build.py` ruft `claude.inventar()` auf und schreibt das Ergebnis.
Es ist das Gegenstück zu `codex.py` und war bis Oktober 2026 der vordere Teil von `build.py`: Der Leser für Codex hatte eine eigene Datei, der für Claude Code steckte im Startbefehl.
`claude.py` importiert `build` nicht, sonst entstünde wieder der Kreis, den `bestand.md` beschreibt; was Leser und `build.py` beide brauchen (`MESS_TAGE`, `zeitgrenze()`, `frisch()`), liegt deshalb in `bestand.py`.
`HOME`, `CLAUDE`, `AGENTS`, `PLUGINS` und `CODEX` stehen hier und nicht in `build.py`, das `claude.HOME` schreibt: Mit einer zweiten Kopie ersetzte ein Test nur eine, und die andere läse den echten Rechner.
`codex.py` hat eigene `HOME`, `CODEX`, `AGENTS` und `CLAUDE`; `build.py` nimmt im Codex-Zweig `codex.HOME`.
Aus demselben Grund schreibt `build.py` `import claude` und nicht `from claude import …`: Ein Test, der am falschen Modul ersetzt, schlägt dann mit `AttributeError` fehl, statt still nichts zu ersetzen.
Der Datenordner ist wie in `codex.py` `harness.DATEN`; ein Test, der `claude.inventar()` aufruft, ersetzt `claude.harness.DATEN`, sonst schriebe der `Zwischenspeicher` in den echten Datenordner.
In `test_claude.py` darf keine lokale Variable `claude` heißen (etwa für `tmp / ".claude"`): Sie verdeckte das Modul, und `mock.patch.object(claude, …)` träfe einen Pfad.
„auch in Codex“ (`geteilt`) trägt ein persönlicher Skill, dessen Datei in `~/.agents/skills` liegt oder auf dessen Datei `~/.agents/skills/<Name>` oder `~/.codex/skills/<Name>` verweist; bis November 2026 fehlte der Verweis aus `~/.agents/skills`, und Skills aus einem Repo mit je einem Verweis in `~/.claude/skills` und `~/.agents/skills` (etwa die aus 3zero-Tools) fehlten im Filter.
Eigene Agenten liest es aus `~/.claude/agents/*.md` und `<Projekt>/.claude/agents/*.md`, bis Oktober 2026 nur die aus Plugins; den Namen nimmt `agent_name()` aus `name` im Kopf, wie Claude Code (Plugin-Agenten behalten den Dateinamen).
`skillOverrides` gilt nur für Skills: Ein eigener Agent bietet nur „deinstallieren“, und der Papierkorb nimmt die Datei, nicht den Ordner `agents/`; die Projekt-IDs vergibt `projekt_ids()` je Art, sonst teilten sich ein Agent und ein Skill gleichen Namens eine ID.
Welche Subagenten liefen, steht nur in den Transkripten (`agent_aufrufe()`: Werkzeug `Agent`, früher `Task`, mit `subagent_type`, je `id` einmal, nur im Messfenster); woher ein Typ kommt, steht dort nicht.
`agenten_messen()` gibt eigenen und Plugin-Agenten ihre Starts als `nutzung` (nach `schluessel`, also nach Namen: gleichnamige Agenten in zwei Projekten teilen sich die Zahl) und liefert den Rest als eingebaute Agenten in `eingebaut` (`typ: "Agent"`), gemessen statt aus einer festen Liste, die mit jeder Version von Claude Code veraltete.
Ein Typ mit „:“ ist ein Plugin-Agent ohne Plugin und zählt nicht als eingebaut; ein gerade gelöschter eigener Agent steht dagegen als eingebaut da, bis er aus dem Messfenster fällt.
`projektordner()` nennt, wohin der Server einen neuen Agenten legen darf: eingetragene Projekte, die es gibt, ohne das Home, ohne alles in `~/.claude`, ohne Sammelordner und ohne Scratch-Ordner der App (`SCRATCH` aus `modelle.py`); Claude Code trägt jeden Ordner ein, in dem einmal eine Sitzung lief, und `~/.claude` stand sonst als Projekt zur Wahl.
Was nur in einem Projekt gilt, steht im Tab „Projekte“, die Tabs unter „Bestand“ sind global: Skills und Befehle aus `<Projekt>/.claude`, MCP-Server aus `<Projekt>/.mcp.json` und lokal in `~/.claude.json`, jeweils für die Projekte in `~/.claude.json`.
Ihre IDs tragen den Ordnernamen des Projekts (`skill:<Projekt>/<Name>`); haben zwei Projekte mit demselben Ordnernamen Einträge, steht dort der ganze Pfad (`projekt_ids()`), sonst teilten sie sich eine ID und eine Aktion träfe das erste.
`plugins[].mcp` nennt im Inventar nur die Namen der Server: `mcp_definitionen()` liest die rohe Angabe aus `.mcp.json` und `plugin.json`, mit `env`, `headers` und Token in Argumenten, und die ging bis Oktober 2026 so ins Dashboard; `inventar()` kürzt sie, sobald `mcp_server()` daraus die verdeckten Einträge unter `mcp` gebaut hat.
Aus demselben Grund wie die Projekt-IDs trägt der MCP-Server eines Plugins, das es in zwei Marktplätzen gibt, den Marktplatz in der ID; Claude Code führt ihn aber ohne, deshalb hängen Live-Status, Sperre und Messung dort am Feld `schluessel`, wie bei den Skills solcher Plugins.
Mods haben einen eigenen Tab (Feld `mods`, je Projekt `projekt_mods`): Hook-Module, die `hooks/hooks.json` eines Plugins unter `modules` nennt, also Code, der in der Sitzung mitläuft, Panels und Befehle hinzufügen, Werkzeug-Aufrufe und Prompt ändern, Dateien schreiben und Programme starten kann.
`mod_eintrag()` macht einen Eintrag je Plugin oder Ordner (ID `mod:<Plugin>` bzw. `mod:<Ordner>`); bis Oktober 2026 stand je Ereignis eine Zeile bei den Hooks (`hook:<Plugin>:mod:<Ereignis>`), gelesen nur an `on('…'` in der genannten Datei.
Was ein Mod tut, meldet `mod_bericht()` über `claude plugin validate --json <Ordner>`: Es liest den Quelltext wie Claude Code selbst, auch importierte Dateien, führt nichts aus und braucht etwa 1,5 Sekunden je Mod.
Hooks und Aufrufe stehen dort nur als Text in `notes` (`<Modul> hooks: …`, `<Modul> calls: … (via …)`), die Wächter in `gatingHooks`; ändert Claude Code diesen Text, fällt `mod_bericht()` auf None und `mod_eintrag()` auf die Textsuche zurück (`messung`: `validate` oder `Textsuche`), ebenso ohne `claude`, etwa bei GitHub.
Die Tests ersetzen `mod_bericht()` deshalb immer (`Mods.setUp`): Ohne Ersatz liefen sie gegen das echte Claude Code des Rechners.
`zeigt`, `greift_ein` und `darf` leitet `mod_eintrag()` aus Ereignissen und Aufrufen ab (`MOD_ZEIGT`, `MOD_GREIFT_EIN`, `MOD_DARF`, Namen aus den Typen von Claude Code im Skill `plugin-authoring`); `befehle` sind die Befehle, auf die ein `command.run`-Hook hört, auch eingebaute wie `/clear`.
Der Pfad eines Moduls kommt aus einer fremden Datei: Was aus dem Plugin herausführt, bleibt ungelesen; ohne lesbares Modul gibt es keinen Eintrag.
Aktionen hat ein Mod keine: Er kommt mit seinem Plugin oder Ordner und geht mit ihm; `befehl()` nennt `claude plugin disable`/`uninstall` bzw. den Ordner.
Gemessen wird an Mods nichts, die Transkripte führen sie nicht.
`ordner_plugins()` liest Hooks und Mods auch aus Plugins, die ohne Installation aus einem Ordner laden: Skill-Ordner mit eigener `.claude-plugin/plugin.json`, die Ordner aus `CLAUDE_CODE_PLUGIN_DIRS` (Umgebung von `build.py` und `env` in `settings.json`) und `~/.claude/dev-mods/<Sitzung>/<Mod>` (Status „nur eine Sitzung“: Sie laden nur dort, wo sie entstanden, und nur nach Freigabe).
Ihre ID trägt den Ordner, und das Feld `ordner` sagt `aktionen()` und `befehl()`, dass der Eintrag weder zu einem Plugin noch in `settings.json` gehört: keine Aktion, sonst suchte „deinstallieren“ den Block in `settings.json`.
Was nur in einem Projekt läuft, liest `projekt_hooks()` in ein eigenes Feld (`projekt_hooks`): `hooks` aus `.claude/settings.json` und `.claude/settings.local.json` des Projekts und Skill-Ordner mit eigener `plugin.json` unter `.claude/skills`, je Projekt aus `~/.claude.json`; ein geklontes Repo bringt beides mit.
Die ID trägt die Datei oder den Ordner.
Sie stehen nur im Tab „Projekte“ und bewusst nicht in `alle`: keine Bewertung, keine Aktion (ein Hook in den Einstellungen eines Projekts gehört oft zum Repo, ein Klick wäre eine Änderung in Git), nur der Text von `befehl()` zum Kopieren; keine Messung, weil die Transkripte Laufzeit und Ausgabe je Ereignis und Befehl führen, nicht je Projekt; nicht in der Kontextlast, wie Projekt-Skills.
Über `ID_ARTEN` meldet die Karte im Überblick aber, wenn in einem Projekt ein Hook dazukommt.
Was Hooks an Claude zurückgeben, misst `claude.py` in den Transkripten der letzten `MESS_TAGE` Tage unter `~/.claude/projects`.
Aus denselben Transkripten kommen die Laufzeiten der Hooks (`durationMs` in jeder Hook-Ausgabe und in der Zusammenfassung am Turn-Ende, die auch Stop-Hooks ohne Ausgabe erfasst) und je MCP-Server die Zahl seiner Werkzeuge, seine Aufrufe und sein Anweisungsblock in Tokens.
Codex protokolliert weder Werkzeuglisten noch Hook-Laufzeiten: dort bleiben diese Felder `null` und das Dashboard schreibt „nicht gemessen“.
HTTP-Server ohne Zugangsdaten fragt `mcp_direkt.py` deshalb selbst, in beiden Harnesses (`initialize`, `tools/list`, einmal am Tag über den `Zwischenspeicher` der Werkzeuge, Schlüssel ist ein Hash der URL, weil in der Query ein Geheimnis stehen kann); seine Werte gehen denen aus den Transkripten vor (`messung`: `direkt` oder `Transkript`).
Nie stdio-Server (hieße fremde Programme starten), nie Server mit `headers`, `headersHelper`, `oauth` oder den Token-Schlüsseln von Codex, nie Umleitungen (urllib folgte sonst auch nach `ftp:`), nur `http`/`https`.
Verlangt ein Server auf `initialize` eine Anmeldung (401), merkt `mcp_direkt` sich nur das (`OAuth`, wenn er per `resource_metadata` auf seinen Anbieter verweist); die Werte aus den Transkripten bleiben.
`netz()` in `bestand.py` kennzeichnet Server im Netz (nicht `localhost`, `127.0.0.1`, `::1`) mit der Domain aus der Konfiguration und der Anmeldung, dazu kommen `anweisungen` (der Anweisungsblock allein) und `lesend` (Werkzeuge mit `readOnlyHint`, eine Angabe des Servers).
`tokens` eines MCP-Servers sind Anweisungsblock plus Werkzeugnamen, wie sie in der Werkzeugliste stehen (`mcp__<Server>__<Name>`); die Schemata (`schemata`) laden erst bei Bedarf und zählen nicht zur Kontextlast.
Ein Absturz hier bricht `build.py` ab, und ohne gelungenen Lauf startet der Server nicht; ein Plugin-Eintrag ohne Installation oder ohne Pfad gilt deshalb als nicht installiert.
`fehlende_ordner()` nennt eingetragene und gemessene Projekte, deren Ordner es nicht mehr gibt (`ordner_fehlt`, nur Claude Code): Ein gelöschtes Projekt steht noch im Tab „Projekte“, solange dort Tokens gemessen sind, und trägt dann „Ordner fehlt“; ausblenden hieße, dass seine Tokens in der Summe fehlen.
Gelesen wird einmal je Lauf: `main()` in `build.py` liest `~/.claude.json` (`cfg`) und `settings.json` (`settings`), dazu Bewertung und Lock für `claude.inventar()`, und gibt sie als Argumente weiter, so wie `codex.py` es mit seinem `cfg` tut.
Vorher standen sie als Modulwerte (`CFG`, `SETTINGS`, `GESPERRT` und vier weitere): `import build` (damals mit dem Leser) las vier Dateien vom Rechner, 13 Funktionen lasen heimlich mit, und ein Test, der nur `SETTINGS` ersetzte, bekam weiter das `GESPERRT` des echten Rechners.
Sieben Tests riefen `projekt_skills()` oder `persoenliche_skills()` ganz ohne Ersatz auf und liefen so unbemerkt gegen die Overrides und Nutzungszahlen des Rechners, auf dem sie liefen.
Die Pfade (`HOME`, `CLAUDE`, `AGENTS`, `PLUGINS`) bleiben Konstanten in `claude.py`; ein Test, der `main()` aufruft, setzt `HOME`, `CLAUDE` und `AGENTS` auf ein leeres Home, sonst liest `main()` die echte Konfiguration (`PLUGINS` liest nur `claude.inventar()`, das diese Tests ersetzen).
