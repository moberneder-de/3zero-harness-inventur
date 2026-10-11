---
paths:
  - "docs/**"
---

# docs/

`docs/` enthält vier Diagramme, je als geprüftes HTML und als Quell-JSON: `harness-inventur` den Aufbau, `neu-bewerten` den Bewertungslauf (für beide Harnesses gleich, mit dem Abbrechen), `zahlen` die Herkunft der Kennzahlen, `schritte` die sechs Schritte des Skills.
Erzeugt mit dem Skill `archify` (`deliver <typ> … --quality showcase`).
Ändert sich etwas, die JSON anpassen und neu ausliefern, nicht das HTML bearbeiten.
Beim Sequenzdiagramm gibt `meta.viewBox` bewusst 1085×480 vor: Der Viewer skaliert schmale Diagramme hoch, ein hohes Sequenzbild passt dann nicht mehr in ein 1440×900-Fenster.
Die Screenshots von `visual-check` sind Nachweise und gehören nicht ins Repo.
