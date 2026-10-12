---
paths:
  - "template.html"
  - "app-icon.svg"
  - "app-icon.png"
  - "render-app-icon.py"
  - "wordmark.svg"
---

# template.html

`template.html` ist die Oberfläche.
Die Erläuterung eines Tabs steht am rechten Fensterrand (`details.erklaerung` aus `abschnitt()`, `position: fixed`), wie im 3zero-Cobol-Analyzer: zugeklappt eine schmale Lasche, aufgeklappt eine Tafel über dem Inhalt.
Erst stand sie über der Tabelle und nahm ihr den Platz, dann eingeklappt hinter der Überschrift und schob die Tabelle aufgeklappt nach unten.
Je Tab ein eigenes `<details>`; zu sehen ist nur das des gewählten, und alle klappen gemeinsam (`toggle` im Einfangen, es steigt nicht auf): Wer die Tafel offen hat, will sie nach dem Wechsel des Tabs weiter sehen.
`main` hat rechts dafür mehr Rand als links, sonst läge die Lasche über der Trefferzahl des Filters.
Sie merkt sich nichts, nach dem Neuladen ist sie wieder zu.
Die Kacheln im Überblick (`.kpi.kurz`) zeigen nur Zahl, ein Wort dafür und eine kurze zweite Zeile; was die Zahl genau zählt, steht in der Tafel und beim Überfahren (`GENAU`).
Als Fließtext in der Kachel machte die längste Erklärung alle sieben so hoch wie sich selbst.
Ihr Text ist gegliedert (`erklaert()`): ein Satz, worum es geht, dann je Begriff oder Spalte eine Zeile (`dl.begriffe`), dann „Gut zu wissen“ und, wo es Marken gibt, deren Legende.
Als ein Absatz Fließtext war der Begriff, den man suchte, nicht zu finden.
Eine neue Erläuterung deshalb nicht als Fließtext schreiben.
Über jeder Tabelle steht ein Filter, der beim Scrollen unter dem Seitenkopf stehen bleibt (`position: sticky` mit `--kopf`).
„Skills“ filtert seine Daten selbst und merkt sich die Wahl (`skillsFiltern()`); die Empfehlungen entstehen bei jeder Aktion neu und haben deshalb ein Suchfeld außerhalb von `#empf-inhalt` (`empfFiltern()`); alle anderen Tabellen stehen fertig im Dokument, und `tabellenFilter()` setzt ein Suchfeld davor und je Spalte mit `data-filter` im Tabellenkopf eine Auswahl aus den Werten, die in der Tabelle vorkommen.
Eine neue Tabelle bekommt den Filter also von selbst, eine Auswahl braucht nur das Attribut.
Die Suche liest auch die aufgeklappten Einzelheiten einer Zeile mit (im Tab „Projekte“ findet `git push` die Projekte mit dieser Regel).
Tabellen in aufgeklappten Zeilen bekommen keinen Filter, und diese Filter merken sich nichts.
Auch die Tabellenköpfe bleiben stehen, unter Seitenkopf und Filter (`--kopf` plus `--filter`, die Höhe des Filters, die ein `ResizeObserver` an das Element danach schreibt).
Dafür hat `.tabelle` kein `overflow-x: auto` mehr, außer unter 900 Pixeln Breite: Mit `overflow` hinge der Kopf an diesem Kasten statt an der Seite.
Schmal scrollt die Tabelle also weiter für sich, ohne festen Kopf.
In der Meldungskarte (`.meldung`) steht der Kopf nicht fest: Ein langer Bericht scrollt dort in seinem eigenen Kasten (`.inhalt.gekuerzt`), und der feste Kopf hing um die Höhe des Seitenkopfs versetzt über der ersten Zeile der Tabelle.
Die Navigation hat zwei Ebenen: sieben Einträge oben, davon drei Gruppen („Aufräumen“: Empfehlungen, Überschneidungen, Hinweise; „Bestand“: MCP-Server, Plugins, Skills, Hooks, Anweisungen; „Einstellungen“: Berechtigungen, Modelle, Versionen), deren Tabs in einer zweiten Zeile (`.unternav`) stehen.
Jeder Tab bleibt ein eigener `<section>` mit seiner Adresse (`#mcp`, `#modelle`); `zeigeTab()` liest die Gruppe aus der zweiten Zeile ab, ein neuer Tab braucht also nur dort einen Link.
Vierzehn Tabs nebeneinander waren nach vier Ordnungen gewachsen (Art, Ort, Thema, Aufgabe) und brachen in zwei Zeilen um.
Der Tab „Projekte“ zeigt je Projekt auch dessen Regeln und Modelle, mit denselben Tabellen wie die Tabs „Berechtigungen“ und „Modelle“ (`RECHT`, `MESSUNG`): zwei Sichten auf dieselben Daten, je Projekt und über alle Projekte.
Sammelordner lässt der Tab weg (`sammelordner()` in `build.py`, Feld `sammelordner`): eingetragene Ordner, in denen andere eingetragene Projekte liegen und die selbst kein Git-Repo sind, also `~` oder ein Ordner voller Projekte.
Claude Code trägt jeden Ordner ein, in dem einmal eine Sitzung lief.
Lädt dort ein Projekt-Skill oder Projekt-Server, bleibt der Ordner stehen, denn die zeigt nur dieser Tab; seine Regeln und Tokens stehen weiter in „Berechtigungen“ und „Modelle“.
Wo je Projekt etwas steht (Tab „Projekte“, „Alle Regeln je Projekt“, Modelle „Je Projekt“), ist es eine Tabelle mit einer Zeile je Projekt, die sich per Klick aufklappt, wie bei Skills und MCP-Servern; lose `<details>` untereinander zeigten den Namen groß und die Zahlen klein dahinter.
Der aufgeklappte Inhalt ist dort eine Tabelle und steht in `tr.inhalt` (`inhaltZeile()`), nicht in `tr.detail`: Dessen Regel `tr.detail td` träfe auch die Zellen der Tabelle darin.
Farben und Bausteine nur aus dem 3zero-Brand-Kit (`../3zero-Design-Kit/BRAND.md` — zum Nachschlagen, der Bau braucht es nicht).
Das Logo liegt als `wordmark.svg` im Projekt: eine Kopie aus dem Kit, viewBox schon gekürzt, damit der Zusatz „IT- Consulting“ wegfällt; `build.py` bettet sie als Data-URI ein.
Ändert sich das Kit, die Kopie erneuern — wie, steht im Kommentar am Anfang der Datei.
Die Klasse `kopf` ist für Gruppenköpfe vergeben; der feste Seitenkopf heißt `seitenkopf`.
Signalfarben sind dort noch offen, deshalb unterscheiden sich die Empfehlungen per Form und Symbol statt per Farbe.
Eine dunkle Palette kennt das Kit nicht: Jede Farbe steht deshalb einmal als `light-dark(hell, dunkel)` in `:root`, der Knopf im Kopf schaltet nur `color-scheme` um und merkt sich die Wahl im Browser; ohne Wahl gilt die Systemeinstellung.
Dunkel übernimmt `--hero` als Kartenfarbe, nur Seitengrund, Linien und die Zeile unter dem Mauszeiger sind abgeleitet.
Neue Flächen deshalb nie auf `#fff` setzen, sondern auf `var(--bg)`.
Bestätigungen laufen über `frage()` mit einem `<dialog>` im Stil der Service-Card, nie über `confirm()` oder `prompt()`; das Kit hat keinen Dialog-Baustein.

## Icons

Icons: `app-icon.svg` ist die eine Quelle für Favicon und das Zeichen im Seitenkopf; `build.py` bettet sie an beiden Stellen als Data-URI ein, im Template steht nur `__ICON__`.
Fürs Dock kommt `app-icon.png` als `apple-touch-icon` dazu: volles Quadrat ohne Transparenz, weil Apple eine eigene Maske darüberlegt.
Das PNG rendert `render-app-icon.py` von Hand aus denselben Formen (braucht Pillow, läuft nicht im Build); ändert sich `app-icon.svg`, muss das Skript mit.
Die zwölf Entwürfe, aus denen Nummer 7 gewählt wurde, liegen in Commit `28eff5a` unter `icon-entwuerfe/`.

## Was das Template nicht rechnet

Urteile über den Bestand kommen fertig aus dem Inventar: `umgesetzt`, `wirksam`, `ersparnis`, `versionen_offen`, `kontextlast`.
Eine neue Regel dieser Art gehört nach `build.py` oder in den Leser, wo sie einen Test bekommt; im Template gibt es keinen.
Im Template bleibt, was von der Wahl im Browser abhängt (`gewaehlt()`, `abweichung`, die Zahl der offenen Aktionen), alle Texte und `befehlText()`: Der leitet die Anzeige bewusst aus genau dem Schritt ab, den der Server ausführt.
Kein Rückfall auf eine eigene Rechnung, falls ein Feld fehlt: Inventar und Dashboard entstehen im selben Lauf.
Einen Umbau am Template prüft man ohne JS-Tests so: das Template mit einem festen Inventar füllen, von Brave mit `--headless=new --dump-dom` rendern lassen und den sichtbaren Text vor und nach dem Umbau vergleichen, für beide Harnesses und je einmal mit `null` und mit einem Wert in `<script id="token">`.
Dabei auch einen Datensatz nehmen, in dem etwas offen ist: Am echten Rechner war die Ersparnis 0, und der Vergleich hätte eine falsche Rechnung dort nicht bemerkt.
Regeln und Anweisungen stehen nicht in `ALLE` und laufen nicht über die Warteschlange der Empfehlungen: ein Klick, eine eigene Rückfrage in Worten, dann `schalte()`.
Die Rückfragen bleiben getrennt, weil sie Verschiedenes erklären; der Weg danach (ausführen, anderer Harness, Fehlermeldung, neu einlesen) stand für beide wortgleich da und ist jetzt einer.
`befehlText()` kennt deshalb nur die Schritte der Einträge aus `ALLE`; die fünf Schritte von Regeln und Anweisungen erreichen es nie, Zweige dafür wären toter Code.
Klicks prüft man ohne JS-Tests mit einem angehängten Skript in der gefüllten Seite: `fetch` und `frage()` ersetzen, jeden Knopf klicken und mitschreiben, was die Rückfrage sagt, was an den Server ginge und welche Meldung danach steht, je einmal mit Erfolg, Fehler und anderem Harness; vor und nach dem Umbau vergleichen.
Den `<dialog>` dabei nicht durchklicken: Sein `close` kommt in Brave ohne Fenster (`--virtual-time-budget`) verzögert an, und der Schritt landet beim nächsten Klick.
