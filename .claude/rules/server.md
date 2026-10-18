---
paths:
  - "server.py"
  - "inventur/server.py"
  - "tests/test_server.py"
---

# server.py

`server.py` zeigt das Dashboard auf http://127.0.0.1:8765 und führt die dort gewählten Aktionen aus (deinstallieren, deaktivieren, nur manuell).
Welche Aktion ein Eintrag anbietet und mit welchem Befehl, legen `aktionen()` in `claude.py` bzw. `codex.py` fest, für Regeln und Anweisungen `rechte_aktionen()` und `anweisungen_aktionen()` in `build.py`; der Server führt nur aus, was so in `inventar*.json` steht.
Was davon eine Konfigurationsdatei ändert, führt `konfig.py` aus (`Konfig.ausfuehren`, siehe `.claude/rules/konfig.md`); der Server hält seine Sperre darum und führt selbst nur noch Papierkorb und Befehle aus.
Die Seite nennt bei jeder Anfrage ihren Harness (`X-Harness`), und der Server antwortet mit 409, wenn inzwischen der andere gilt: Beide Inventare führen dieselben IDs mit denselben Aktionsnamen, ein Klick träfe sonst die Konfiguration des anderen Harness und meldete „erledigt“.
Die Seite mit dem Token gibt es nur mit dem Token als Schlüssel in der Adresse (`/?k=…`), sonst 403: `127.0.0.1` erreicht jeder Benutzer des Rechners, und wer die Seite bekommt, kann Aktionen auslösen und eine Suche starten, die Dateien liest.
Der Browser bekommt deshalb nicht die Adresse, sondern `start.html` im Datenordner (Modus 0600, leitet weiter, wird beim Beenden gelöscht — auch bei `kill` und geschlossenem Terminal, `bedienen()` fängt SIGTERM und SIGHUP ab).
An dieser Datei erkennt auch der Skill den laufenden Server, nicht per `curl`: Die Codex-Sandbox sperrt das Netz auch zum eigenen Rechner (nachgemessen, `000 exit=7` in `workspace-write` und `read-only`), der Skill öffnete dort immer die Datei.
Bleibt `start.html` nach einem harten Ende liegen, fragt sie selbst nach einem Lebenszeichen (`/` ohne Schlüssel, 403 ist auch eine Antwort) und nennt sonst die Dashboards als Datei — Argumente eines Befehls sieht jeder in `ps`.
Auf dem Mac öffnet sie `open -a <Standardbrowser>` (`browser_oeffnen()`), nicht Pythons `webbrowser`: Das schickt `file:`-Adressen per AppleScript an den Browser und holt ihn nicht nach vorn (nachgemessen), das Dashboard ging im Hintergrund auf und es sah aus, als geschähe nichts.
Homebrew löscht beim Python-Update den Ordner der alten Version; ein Server, der noch mit ihr läuft, scheitert dann an Modulen, die Python erst bei Bedarf lädt (etwa `LookupError: unknown encoding: utf-8-sig`).
`python_aktualisiert()` prüft deshalb, ob `os.__file__` noch existiert — nicht `sys.executable`, das über `/opt/homebrew/opt/…` auf die neue Version zeigt —, und jede API-Anfrage meldet dann den Neustart.
Kein Cookie: Cookies gelten je Host, nicht je Port, und gingen an jeden anderen Server auf `127.0.0.1` mit.
`Referrer-Policy: no-referrer`, damit Links nach draußen die Adresse nicht mitnehmen.
Eigene Skills und Befehle deinstalliert der Schritt `papierkorb`, den der Server selbst ausführt (`in_den_papierkorb()`), nicht `mv`: Das scheiterte an einem gleichnamigen Ordner im Papierkorb und überschrieb eine gleichnamige Datei still (nachgemessen).
Mac `~/.Trash`, Linux XDG-Papierkorb mit `.trashinfo`, sonst eine Fehlermeldung.
Befehle mit `npx` stehen schon im Inventar mit fester Version (`harness.SKILLS_CLI`), damit Anzeige, Kopiertext und Ausführung gleich sind; ohne sie liefe die jeweils neueste auf npm.
Eine neue Version erst prüfen, dann dort eintragen.
Der Server ergänzt nur `--prefix <leerer Ordner>`: Ohne ihn nahm npx im Projektordner dessen `node_modules/.bin` und `.npmrc` (nachgemessen).
Einen Namen, der mit `-` beginnt, setzt er in keinen Befehl ein, er läse ihn als Option.
Was das Dashboard abschalten kann, schaltet es auch wieder ein: `aktionen()` bietet bei allem, was aus ist, die Gegen-Aktion „einschalten“ oder „wieder automatisch“ an (Override `None`, `entsperren`, `claude plugin enable`; unter Codex `enabled = true`, `skill_an`, `automatisch`).
Nach gelungenen Aktionen liest die Seite selbst neu ein (`nachAktionen()`), sonst ließen sich erledigte Zeilen erneut ausführen; schlug eine fehl, bleibt sie mit der Meldung stehen.
Bewusst kein Rückgängig-Knopf: Der bräuchte ein Gedächtnis, das mit dem Server endet, die Gegen-Aktion steht im Inventar und geht jederzeit.
Overrides schreibt der Server unter `schluessel`, dem Namen, den Claude Code führt; unter dem Anzeigenamen gleichnamiger Plugins wirkten sie nicht.
Eigene Hooks und die Statuszeile bieten unter Claude Code „deinstallieren“ (Schritt `hook_entfernen`, nimmt den Block aus `settings.json`); abschalten lässt sich ein einzelner Hook dort nicht, nur alle zusammen (`disableAllHooks`).
Plugin-Hooks und Codex-Hooks bleiben ohne Aktion.
Plugins bieten unter Claude Code immer „aktualisieren“ (`claude plugin update`, bewusst ohne `-y`: Damit nähme der Server einen geänderten Installationsbefehl aus dem Marktplatz ungefragt an); ob ein Update bereitsteht, zeigt `claude plugin list` nicht.
Das zeigt `plugin_updates_markieren()` (Feld `update`), per `gh api` gegen den Marktplatz auf GitHub, so wie Claude Code die Version festhält: die aus `plugin.json` der Quelle (nicht die im Marktplatz-Eintrag, die veraltet sein kann); legt der Marktplatz einen `sha` fest, der gegen `gitCommitSha`; sonst ist die Version der Git-Stand der Quelle, bei einem Ordner im Marktplatz also der des ganzen Marktplatzes, und es zählt nur, ob der letzte Commit am Ordner im installierten Stand liegt (`compare`); zählt der Eintrag seine Bestandteile selbst auf (`skills`, `commands`, `agents`), an jedem davon, denn document-skills teilt sich ein Repo mit anderen Plugins, deren Commits es nicht betreffen.
Repos, Pfade und `ref` kommen aus fremden Marktplatz-Dateien und gehen nur in harmloser Form an `gh api` (`GH_REPO`, `GH_PFAD`, `GH_REF`).
Codex führt kein Repo zu seinen Plugins, dort gibt es die Angabe nicht.
Codex hat keinen Update-Befehl für Plugins.
Als Datei geöffnet bleibt das Dashboard nur lesbar.
Der Knopf „neu bewerten“ im Seitenkopf startet über `/api/bewerten` einen eigenen Claude-Lauf (`claude -p "/3zero-harness-inventur"`, Opus).
Er läuft wie der Codex-Lauf auf einer Arbeitskopie der Bewertung in einem Temp-Ordner; `build.py` führt der Server davor und danach selbst aus, und zurück kommt nur ein gültiges JSON-Objekt.
Werkzeuge: Lesen, Suchen und `Edit`, sonst nichts — kein `Bash`, kein `Write`, weil der Lauf fremde Skill-Texte und Hook-Skripte liest.
Die Grenze zieht dabei nicht `--allowedTools`: Es genehmigt nur vorab, und mit `defaultMode: auto` in den Einstellungen des Benutzers liefen auch andere Werkzeuge; pfadgebundene Regeln begrenzen `Edit` dort nicht (nachgemessen).
Wirksam sind `CLAUDE_GRENZEN`: `--tools` legt fest, welche Werkzeuge es gibt, `--restricted` ignoriert die Einstellungen des Benutzers und begrenzt die Datei-Werkzeuge auf den Arbeitsordner und `--add-dir` (das Home, zum Lesen), dazu `deny` in einer eigenen Einstellungsdatei (`--settings`), die `Edit` im Home sperrt; beschreibbar bleibt nur der Temp-Ordner.
Ohne `--restricted` schrieb `Edit` auch außerhalb des Home, etwa nach `/opt/homebrew`.
`--no-session-persistence`, sonst zählte `build.py` die Läufe in den Messwerten mit.
`--restricted` lädt `~/.claude/skills` nicht; der Server legt den Skill deshalb als Kopie (kein Symlink, über den `Edit` ihn im Projekt ändern könnte) in ein Plugin im Arbeitsordner (`skill_plugin()`, `--plugin-dir`), aufgerufen als `/inventur:3zero-harness-inventur`.
Dasselbe gilt für die Suche.
Der Tab „Suche“ startet über `/api/suchen` den Such-Modus ohne Rückfragen und nur mit Lese- und Suchrechten (kein Schreiben, kein `python3`, kein `gh`, weil der Lauf fremde Inhalte aus dem Netz liest); die Kandidaten kommen per `--json-schema` zurück.
Zugangsdaten sperrt `SUCHEN_SPERREN` fürs Lesen: dieselben Pfade wie `GESPERRT_LESEN` bei Codex, je als Datei und als Ordner, wieder als `deny` in einer eigenen Einstellungsdatei.
Das gilt für `Read`, `Grep` und `Glob`, auch wenn `Grep` im Ordner darüber sucht; `jq` liest Dateien außerhalb des Arbeitsordners ohnehin nicht, nur Pipes (alles nachgemessen).
`WebFetch` bleibt für jede Adresse offen, weil die Suche nach MCP-Servern die Doku der Anbieter braucht — die Sperre nimmt dem Lauf also das Lesenswerte, nicht den Weg nach draußen.
Jeder Kandidat trägt `belege` (Installationen, Sterne, letzter Push, Lizenz); `belege()` lässt nur diese Felder mit passendem Typ durch.
`/api/installieren` installiert nur Skills und Plugins aus der letzten Suche, mit einem Befehl, den der Server aus den geprüften Feldern baut, nie mit Claudes Befehlstext.
Ein Agent aus der Suche (`art: "agent"`, `repo` und `pfad`) ist Text, den der Harness als Anweisung lädt: `/api/agent_vorschau` holt den letzten Commit der Datei (`api.github.com/…/commits?path=`) und den Text von genau diesem Commit (`raw.githubusercontent.com`), prüft Format und Kopf (`agent_pruefen()`) und merkt sich den Text am Kandidaten (`vorschau`); `/api/installieren` schreibt dann genau diesen Text, ohne ein zweites Mal zu laden, mit `open(…, "x")`.
Ohne Vorschau kein Installieren, und `geprueft()` lässt nur die Felder des Schemas durch: Ein Suchlauf, der fremde Webseiten liest, brachte sonst selbst ein Feld `vorschau` mit Text und Ziel mit, die niemand gesehen hatte (Test `test_suchlauf_bringt_keine_eigene_vorschau_mit`).
`github_text()` fragt ohne Anmeldung und folgt keiner Umleitung (`OhneUmleitung`), höchstens 200 KB; der Pfad geht nur durch, wenn er zu `AGENT_PFAD` passt und kein `.` oder `..` als Ordner hat, und der Name aus dem Kopf wird nur zum Dateinamen, wenn er zu `AGENT_NAME` passt.
`/api/anlegen` legt aus dem Tab „Erstellen“ einen Agenten, Skill, Befehl oder Output-Style an (`anlegen()`), die einzige Stelle, an der der Server Text aus der Seite in eine Datei schreibt (der Agent aus der Suche kommt aus dem Repo, nicht aus der Seite); der Text kommt vom Benutzer, nicht aus einem Lauf, der Fremdes liest.
Name und Ziel werden Pfade: der Name nur aus `AGENT_NAME` (kein Punkt, kein `/`), das Ziel nur `global` (unter `self.home`, in Tests ersetzt: Mit dem echten Home schrieb ein Testlauf im Oktober 2026 zwei Agenten nach `~/.claude/agents` und `~/.codex/agents`) oder ein Ordner aus `projektordner` im Inventar, den es noch gibt, sonst legte `mkdir` ihn neu an.
Geschrieben wird mit `open(…, "x")`, also nie über eine vorhandene Datei, und nicht, wenn das Inventar dort schon einen gleichnamigen Agenten in einer anderen Datei führt; ein Skill-Ordner, den es schon gibt, bleibt unberührt, auch ohne `SKILL.md`.
Befehl und Output-Style nur unter Claude Code, der Output-Style nur global; „nur manuell“ steht beim Skill unter Claude Code als `disable-model-invocation: true` im Kopf, unter Codex in `agents/openai.yaml`.
Ein Output-Style heißt, wie `name` im Kopf ihn nennt (so liest ihn `anweisungen.py`), mit Leerzeichen und Umlauten; die Datei bekommt `kurzname()` davon, und der Name steht ohne Anführungszeichen im Kopf, weil `kopf()` sie nicht entfernt: `STIL_NAME` lässt deshalb keinen Doppelpunkt, kein `#` und kein `"` zu.
`anlegen_text()` setzt die Beschreibung (beim Codex-Agenten auch die Anweisung) als JSON-String, der in YAML wie in TOML gilt: Ein Doppelpunkt, ein Anführungszeichen oder `---` beendet so weder Wert noch Kopf; `frontmatter()` zeigt ein `\"` darin allerdings mit dem Schrägstrich.
Der Knopf „prüfen“ an der `CLAUDE.md` eines Projekts (oder der `AGENTS.md`, die sie einbindet) startet über `/api/pruefen` einen dritten Lauf (`pruefen()`): Claude bewertet die Datei mit dem Skill `claude-md-improver` aus dem Plugin `claude-md-management` und gibt Punkte je Kriterium, Probleme und Vorschläge per `--json-schema` zurück.
Er ist enger eingezäunt als die Suche, weil er ein fremdes Repo liest: nur `Read`, `Glob` und `Grep`, kein Schreiben, keine Shell, kein Netz — ohne Netz kann er auch nichts verschicken, was er liest — und gelesen wird nur im Projekt (`PRUEFEN_GRENZEN` mit `--add-dir <Projekt>`, nicht das Home wie in `CLAUDE_GRENZEN`).
Er startet in einem Temp-Ordner, nicht im Projekt: Dort lüde Claude Code dessen Einstellungen und Hooks.
Der Skill schriebe nach Zustimmung auch in die Datei; `PRUEFEN_HINWEIS` lässt ihn nach dem Bericht enden, und ohne Werkzeug zum Schreiben könnte er es auch nicht.
Umgesetzt wird in einer Sitzung im Projekt, aber ohne die Analyse ein zweites Mal zu bezahlen: Der Server legt den Bericht als Markdown in den Datenordner (`pruefdatei()`, je Projekt eine Datei `pruefung-<Ordner>-<Hash>.md`, der letzte gilt), und `umsetzen_befehl()` baut den Befehl, der Claude im Projekt nur diese Datei lesen und umsetzen lässt.
Der Befehl entsteht aus den Pfaden, nicht aus Claudes Text, und `shlex.quote` hält auch einen Pfad mit Leerzeichen oder `$(…)` zusammen: Er steht neben einem Kopierknopf.
Die Vorschläge im Bericht bleiben fremder Text aus einem Lauf, der ein Repo las; der Kopf der Datei sagt das, und die Sitzung zeigt jede Änderung, bevor sie schreibt.
Datei, Projekt und Skill nimmt `pruefauftrag()` aus dem Inventar (`pruefen` an der Zeile, `pruefskill` oben), einer Datei, die auch andere schreiben können: Als Projekt gilt deshalb weder das Home noch ein Ordner darüber, die Datei muss eine `CLAUDE.md` in diesem Projekt sein und der Skill-Ordner `claude-md-improver` heißen.
Den Skill bekommt der Lauf als Kopie in einem Plugin im Temp-Ordner (`/pruefung:claude-md-improver`), mit seinen `references`, weil `--restricted` die Plugins des Benutzers nicht lädt.
`pruefbericht()` lässt nur die erwarteten Felder durch und rechnet Summe und Note selbst, jedes Kriterium höchstens bis zu seinem Höchstwert (`PRUEFEN_KRITERIEN`): Was Claude als Gesamtzahl nennt, muss nicht zu seinen Einzelwerten passen.
Die Karte bleibt wie bei der Suche auf dem Server, bis sie geschlossen wird; die Berichtsdatei bleibt liegen; `/api/pruefung` nennt den Stand, `/api/abbrechen` beendet auch diesen Lauf.
Die Seite hält dafür keine Sperre: Der Lauf ändert nichts, andere Aktionen müssen nicht minutenlang warten.
Bewertung und Suche laufen im Hintergrund (`im_hintergrund()`); der Browser fragt den Stand über `/api/bewertung` und `/api/suche` ab, das Ergebnis bleibt auf dem Server, bis die Karte geschlossen wird.
Eine minutenlang offene Anfrage riss beim Neuladen der Seite ab.
Die Bewertung hält die Sperre nur für `build.py` davor und für Zurückschreiben und `build.py` danach, nicht für den Lauf selbst: Sonst hing jede Aktion bis zu 20 Minuten, und die Suche wurde abgelehnt.
Jeder Lauf hat seine Befehlszeile als eigene Funktion, in der seine Grenzen wörtlich stehen (`bewerten_argv`, `suchen_argv`, `pruefen_argv`, `codex_bewerten`, `codex_suchen`): bewusst keine Tabelle, aus der sie entstünden, denn dann stünde nirgends mehr, was ein Lauf darf.
`bewerten()` ist für beide Harnesses eine Methode, verschieden ist nur die Mitte (Einstellungen und Plugin für Claude Code, Berichtsdatei für Codex); vorher standen dieselben sieben Schritte zweimal da, und eine Änderung am Rückweg musste an beide Stellen.
`build.py` startet nur `bauen()`, und wer es aufruft, hält die Sperre.
`/api/abbrechen` beendet einen solchen Lauf: `lauf()` startet jeden Prozess in einer eigenen Prozessgruppe (`start_new_session`), `abbrechen()` beendet die ganze Gruppe, weil claude und codex ihre Werkzeuge als Kindprozesse starten.
Ctrl+C im Terminal erreicht diese Gruppen nicht mehr, deshalb beendet `main()` sie beim Verlassen selbst.
Die Prüfung auf Python 3.11 steht im Startbefehl `server.py`, vor dem ersten Import aus dem Paket: Mit 3.9 scheiterte sonst schon `import tomllib` mit einem Traceback.
`main()` nimmt `--harness` wie `build.py` und reicht es beim ersten Einlesen an `build.py` weiter, das ihn sich merkt; vorher ignorierte der Server die Angabe still und zeigte den gemerkten Harness.
Kommt der Abbruch, während `build.py` davor noch läuft, stirbt der Lauf beim Start; ist claude schon durch, lehnt der Server ab, weil die Bewertung dann schon zurückgeschrieben ist.
Unter Codex läuft „neu bewerten“ als `codex exec -s workspace-write` mit `-C` auf einer Arbeitskopie in einem Temp-Ordner, die eine Kopie von `bewertung-codex.json` enthält, nicht im Projektordner; build.py führt der Server davor und danach aus, prüft die zurückgegebene Kopie als JSON-Objekt und schreibt sie atomar zurück.
Dabei darf der Lauf weder `$TMPDIR` noch `/tmp` beschreiben (`sandbox_workspace_write.exclude_tmpdir_env_var`, `exclude_slash_tmp`): workspace-write macht sonst auch diese Ordner schreibbar, und ein injizierter Lauf könnte dort etwas ablegen, das `installieren` oder die Claude-Suche später ohne Sandbox ausführen.
Die Suche unter Codex läuft `codex exec` mit dem Berechtigungsprofil `suche`: nur lesen, schreiben nur in einen npm-Zwischenspeicher, Zugangsdaten gesperrt (u. a. `.ssh`, `.aws`, `.netrc`, `.npmrc`, `.pypirc`, `.claude.json`, `.codex/config.toml`, `.docker/config.json`, `.kube/config`, `.git-credentials`; `npm_config_userconfig` zeigt dafür auf einen eigenen Pfad), Netz nur über den Codex-Proxy im Modus `limited` zu skills.sh, npm und GitHub, dazu die Websuche (`web_search="live"`) als eigener Kanal außerhalb dieses Proxys.
Beide Codex-Läufe ohne eigene Plugins, MCP-Server, Regeln und Apps (`--ignore-user-config --ignore-rules`).
`codex exec` braucht leeres stdin, sonst wartet es.
Mit VPN kommt der Codex-Proxy nicht durch.
Installiert wird unter Codex mit `npx skills add … --agent codex` bzw. `codex plugin add`.
