---
paths:
  - "docs/**"
---

# docs/

`docs/` enthält vier Diagramme, je als geprüftes HTML und als Quell-JSON: `harness-inventur` den Aufbau, `neu-bewerten` den Bewertungslauf (für beide Harnesses gleich, mit dem Abbrechen), `zahlen` die Herkunft der Kennzahlen, `schritte` die sechs Schritte des Skills.
Erzeugt mit dem Skill `archify` (`deliver <typ> … --quality showcase`).
Ändert sich etwas, die JSON anpassen und neu ausliefern, nicht das HTML bearbeiten.
Beim Sequenzdiagramm gibt `meta.viewBox` bewusst eine Größe vor (1085×660): Der Viewer skaliert schmale Diagramme hoch, ein hohes Sequenzbild passt dann nicht mehr in ein 1440×900-Fenster.
Bis Oktober 2026 waren es 1085×480; archify 3.1 rechnet Notizen höher und verlangt Platz für die Legende, deshalb stehen die Nachrichten jetzt weiter auseinander (nachgesehen in 1440×900, die Legende passt).
`finalize` legt `<Name>.delivery.json` neben das HTML: Das ist eine Quittung, kein Teil des Repos; die Quittungen von `--out-dir` gehören in einen Ordner außerhalb.
Die Screenshots von `visual-check` sind Nachweise und gehören nicht ins Repo.
