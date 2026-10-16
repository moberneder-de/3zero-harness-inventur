---
paths:
  - "inventur/anweisungen.py"
  - "tests/test_anweisungen.py"
---

# anweisungen.py

`anweisungen.py` liest für den Tab „Anweisungen“ (unter „Bestand“), was der Harness als Text in die Sitzung lädt; `anweisungen_lesen()` in `build.py` ruft es nach den Modellen auf, ein Fehler kostet nur den Tab (`lesefehler`).
Der eingebaute Systemprompt steht in keiner Datei und fehlt.
Claude Code, global: verwaltete `CLAUDE.md` (`VERWALTET`), `~/.claude/CLAUDE.md`, `~/.claude/rules/**/*.md` und die Styles in `~/.claude/output-styles` (aktiv ist der, den `outputStyle` in `~/.claude/settings.json` nennt; ein eingebauter Name hat keine Datei, ein unbekannter ergibt den Standard und steht als „wirkungslos“ da).
Je Projekt aus `~/.claude.json`: `CLAUDE.md`, `.claude/CLAUDE.md` und `CLAUDE.local.md`, auch aus jedem Ordner darüber (`geerbt`; `~/.claude/CLAUDE.md` nicht noch einmal, das ist die globale — auch nicht, wenn `~/.claude` selbst als Projekt eingetragen ist, weil dort einmal eine Sitzung lief), `.claude/rules`, `AGENTS.md` („wirkungslos“, sobald es eine CLAUDE.md gibt) und `MEMORY.md` aus `~/.claude/projects/<Pfad mit - für alles außer Buchstaben und Ziffern>/memory` (gezählt werden nur die ersten `MEMORY_ZEILEN` Zeilen oder `MEMORY_BYTES`, mehr lädt Claude Code beim Start nicht).
Eine `CLAUDE.md` mit mehr als `GROSS_ZEILEN` Zeilen oder `GROSS_TOKENS` Tokens trägt die Marke „groß“: Die Doku rät zu unter 200 Zeilen, und die Token-Grenze fängt Dateien mit wenigen, sehr langen Zeilen (die dieses Repos hatte 24 Zeilen und 10.000 Tokens).
Haben die Dateien, die in einem Projekt beim Start laden, zusammen mit den globalen mehr als `LIMIT_ZEICHEN` Zeichen, trägt die größte von ihnen „über dem Limit“, ab `LIMIT_NAHE` „nahe am Limit“, mit der Summe in `limit_summe` (`limit_markieren()`).
Die Grenze von 150.000 Zeichen steht in keiner Doku: Sie stammt aus der Warnung, die Claude Code beim Start zeigt („2 instruction files add up to 151.0k chars, over the 150.0k-char total limit“, Version 2.1.296); was es oberhalb tut, ist ungeprüft.
Gezählt wird wie in dieser Warnung: `CLAUDE.md`, Regeln ohne `paths` und `AGENTS.md`, nicht `MEMORY.md` und nicht der Output-Style; jede Zeile trägt dafür `zeichen`.
Zwei andere billige Prüfungen sind ausprobiert und verworfen: „nennt Dateien, die es nicht gibt“ traf an 15 echten Dateien meist Falsches (Modulnamen wie `std/net.http`, GitHub-Repos, Adressen wie `/v1/models`), und ob Befehle fehlen, hängt vom Projekt ab (ein Design-Kit braucht keine).
Beides braucht ein Urteil und gehört in einen Lauf mit Claude, nicht in eine Marke.
Liegen in einem Projekt `CLAUDE.md` und `.claude/CLAUDE.md`, tragen beide die Marke „zwei CLAUDE.md“: Claude Code lädt beide, und wer nur eine öffnet, hält sie für die ganze Anleitung (so in 3zero-Q-Ecosystem).
`laedt` heißt: steht beim Start im Kontext; eine Regel mit `paths` im Kopf lädt erst „bei Bedarf“.
Ihre Muster stehen in `gebunden` (`gebunden()` liest `paths` als Liste oder als Text mit Kommas; ein Komma in `{ts,tsx}` trennt nicht).
Passt im Projekt keine Datei auf eines der Muster, trägt die Regel die Marke „verwaist“ (`verwaiste_markieren()`): Sie lädt nie, etwa nach dem Umbenennen einer Datei, und das merkte sonst niemand.
Die Muster rechnet `muster_regex()` selbst nach, Claude Code mit seiner eigenen Logik: Die Marke ist ein Hinweis zum Nachsehen, deshalb ohne Aktion.
„nicht geprüft“ statt „verwaist“, wo sich nichts sagen lässt: ein Muster, das sich nicht lesen ließ, oder ein Projekt mit mehr als `SUCHGRENZE` Dateien (`.git`, `node_modules` und `.venv` bleiben aus) — lieber kein Urteil als eine falsche Warnung.
Globale Regeln und abgeschaltete bleiben ohne Prüfung: Ein Muster in `~/.claude/rules` gilt in jedem Projekt.
Tokens sind Zeichen ÷ 4; Dateien, die eine Anweisung mit `@pfad` einbindet, zählen nicht mit, die Zeile nennt nur ihre Zahl (nur vorhandene Dateien, sonst wäre `@anthropic-ai/sdk` ein Import).
Die globalen Zeilen, die laden, zählen zur Kontextlast (`kontextlast()`), Projektdateien nicht, wie Projekt-Skills; unter Codex zählt dafür `~/.codex/AGENTS.md` (die erste nicht leere von `AGENTS.override.md` und `AGENTS.md`, belegt an `codex-rs/codex-home/src/instructions/mod.rs`), die `AGENTS.md` der Projekte liest die Inventur dort noch nicht.
Schalten lässt sich über den Server, aber nie an der Datei, die oft in einem Repo liegt, sondern nur in `~/.claude/settings.json` (`anweisungen_aktionen()`): eine CLAUDE.md, Regel oder AGENTS.md über `claudeMdExcludes` (Schritte `ausschliessen` und `einschliessen`; nachgemessen mit `claude -p`: Mit dem Eintrag folgt Claude der Datei nicht mehr), ein Output-Style über `outputStyle` (Schritt `output_style`, `None` wählt ab).
Claude Code liest einen Eintrag in `claudeMdExcludes` als Muster, und der Pfad kommt aus dem Inventar: Der Server nimmt deshalb nur einen ganzen Pfad zu einer `.md`-Datei ohne `*?[]{}`.
„einschalten“ gibt es nur, wo genau diese Datei in `~/.claude/settings.json` steht (`ausgeschlossen: eigen`); schließt ein Muster oder ein Eintrag im Projekt sie aus („per Muster“), träfe das Herausnehmen mehr als die Zeile.
Die `CLAUDE.md` eines Projekts bekommt außerdem `pruefen` (Datei und Projekt), wenn der Skill `claude-md-improver` installiert ist und lädt (`pruefskill()` in `claude.py`, Feld `pruefskill`): Damit bietet das Dashboard „prüfen“ an, einen lesenden Claude-Lauf im Server (siehe `.claude/rules/server.md`).
Nicht die globale Datei, weil das Raster des Skills nach Befehlen und Aufbau eines Projekts fragt, keine geerbte, weil sie zum Ordner darüber gehört, und keine verwaltete.
Ohne Aktion bleiben auch verwaltete Dateien, `MEMORY.md` (`autoMemoryEnabled` schaltet alles zusammen) und Codex.
Einen Output-Style kann ein Projekt in `.claude/settings.local.json` anders wählen; das liest das Modul nicht.
Sammelordner lässt der Reiter „Je Projekt“ weg, wie der Tab „Projekte“: Für `~/Daten/600_Projekte` lag eine `MEMORY.md` da, weil dort einmal eine Sitzung lief, und stand als Projekt in der Liste.
Die ID trägt Bereich und Pfad (`anweisung:<Bereich>:<Pfad>`), weil dieselbe geerbte Datei in jedem Projekt darunter steht.
Die Tests von `build.main` ersetzen auch diese beiden Lesefunktionen.
