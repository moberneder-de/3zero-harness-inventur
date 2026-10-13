---
paths:
  - "bestand.py"
  - "test_bestand.py"
---

# bestand.py

`bestand.py` enthält, was beide Leser des Bestands brauchen: `claude.py` für Claude Code und `codex.py` für Codex.
Hierher gehört nur, was wirklich beide benutzen und was nicht weiß, welcher Harness gerade gelesen wird; was sich unterscheidet, geben die Leser als Argument mit.
Vorher holte `codex.py` diese Funktionen aus `build.py`, und `build.py` lud `codex.py`: ein Kreis.
Als Skript gestartet heißt `build.py` aber `__main__`; der Import aus `codex.py` führte seine Modulebene ein zweites Mal aus, mit einer zweiten Kopie aller Konstanten, und `main()` musste das über `sys.modules` verdecken.
Deshalb importiert `bestand.py` weder `build` noch `claude` noch `codex`.
Seit Oktober 2026 liegt der Leser für Claude Code in `claude.py`; für ihn gilt dasselbe: Er importiert `build` nicht.
`MESS_TAGE` und `zeitgrenze()` liegen hier, obwohl `codex.py` sie nicht benutzt: `claude.py` braucht das Messfenster für die Transkripte und `build.py` für den Tab „Modelle“ beider Harnesses; in `claude.py` hinge der Codex-Weg am Claude-Leser.
`bewerte()` bekommt Bewertung und `befehl()` des Harness immer ausdrücklich: Mit einer stillen Vorgabe für Claude Code hinge das Modul wieder am Claude-Leser.
Den Befehl baut dabei immer `befehl_fuer`, nie die Bewertung: Sie stammt aus einem Lauf, der fremde Skill-Texte liest, und der Befehl steht neben dem Kopierknopf.
`LESEFEHLER` ist eine Liste für beide Leser, `main()` in `build.py` leert sie am Anfang; wer `lesefehler()` in einem Test auslöst, leert sie selbst.
Einen unerwarteten Fehler in einem Plugin oder Projekt fangen `claude.py` und `codex.py` je Quelle ab: Sie fällt weg, `lesefehler` im Inventar nennt sie, das Dashboard zeigt sie als Banner.
Ob Skills aus `npx skills` ein Update haben, fragt `updates_markieren()` per `gh api` bei GitHub ab (Ordner-SHA gegen `skillFolderHash`); ohne `gh` oder Netz fehlt nur diese Angabe, ebenso bei einer unerwarteten Antwort oder einem Lock-Eintrag ohne Repo, Pfad oder Stand.
`verdecken()` ersetzt in URL und Argumenten eines MCP-Servers, was nach Zugangsdaten aussieht, bevor das Ziel im Inventar steht; es erkennt Muster, keine Bedeutung.
`shellwort()` setzt einfache Anführungszeichen, weil in doppelten `` ` ``, `$` und `\` wirksam blieben: Ein Projektpfad mit `$(…)` führte beim Einfügen ins Terminal etwas anderes aus, als dasteht.
`build.py`, `claude.py` und `codex.py` holen sich die Namen per `from bestand import …`; ein Test, der eine dieser Funktionen ersetzen will, ersetzt sie deshalb in dem Modul, das sie aufruft (`claude.hook_eintraege`, `claude.zeitgrenze` für die Messung aus Transkripten), oder in `bestand`, wenn `bestand` sie selbst aufruft (`bestand.github_baum`).
