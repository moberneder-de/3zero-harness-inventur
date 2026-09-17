"""Welcher Harness gerade inventarisiert wird: Claude Code oder Codex.

Der Skill nennt ihn beim Start (build.py --harness), build.py merkt ihn sich in harness.json,
server.py liest ihn bei jeder Anfrage. Der letzte Start zählt.

Alles, was zum Rechner des Benutzers gehört und nicht zum Werkzeug, liegt in DATEN:
Bewertungen, Inventare, Dashboards und harness.json. Der Projektordner bleibt dadurch
frei von persönlichen Daten und lässt sich veröffentlichen.
"""

import json
from pathlib import Path

DATEN = Path.home() / ".3zero-harness-inventur"

HARNESSE = {
    "claude-code": {"name": "Claude Code", "cli": "claude",
                    "inventar": "inventar.json", "dashboard": "dashboard.html", "bewertung": "bewertung-claude-code.json"},
    "codex": {"name": "Codex", "cli": "codex",
              "inventar": "inventar-codex.json", "dashboard": "dashboard-codex.html", "bewertung": "bewertung-codex.json"},
}
STANDARD = "claude-code"


def aktuell(ordner):
    try:
        wert = json.loads((Path(ordner) / "harness.json").read_text(encoding="utf-8")).get("harness")
    except (OSError, ValueError, AttributeError):
        return STANDARD
    return wert if wert in HARNESSE else STANDARD


def merken(ordner, harness_id):
    if harness_id not in HARNESSE:
        raise ValueError(f"unbekannter Harness: {harness_id}")
    (Path(ordner) / "harness.json").write_text(json.dumps({"harness": harness_id}) + "\n", encoding="utf-8")
