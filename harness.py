"""Welcher Harness gerade inventarisiert wird: Claude Code oder Codex.

Der Skill nennt ihn beim Start (build.py --harness), build.py merkt ihn sich in harness.json,
server.py liest ihn bei jeder Anfrage. Der letzte Start zählt.

Alles, was zum Rechner des Benutzers gehört und nicht zum Werkzeug, liegt in DATEN:
Bewertungen, Inventare, Dashboards und harness.json. Der Projektordner bleibt dadurch
frei von persönlichen Daten und lässt sich veröffentlichen.
"""

import json
import os
import tempfile
from pathlib import Path

DATEN = Path.home() / ".3zero-harness-inventur"

HARNESSE = {
    "claude-code": {"name": "Claude Code", "cli": "claude", "inventar": "inventar-claude-code.json", "dashboard": "dashboard-claude-code.html",
                    "bewertung": "bewertung-claude-code.json", "verlauf": "verlauf-claude-code.json"},
    "codex": {"name": "Codex", "cli": "codex", "inventar": "inventar-codex.json", "dashboard": "dashboard-codex.html",
              "bewertung": "bewertung-codex.json", "verlauf": "verlauf-codex.json"},
}
STANDARD = "claude-code"
# Feste Version statt der jeweils neuesten auf npm, in jedem Befehl, der skills installiert oder entfernt.
# Neue Version: prüfen, dann hier eintragen.
SKILLS_CLI = "skills@1.7.2"


def schreiben(pfad, text):
    """Schreibt in eine Zwischendatei und tauscht in einem Schritt: Der Server liest Inventar, Dashboard und harness.json
    bei jedem Klick, auch während build.py läuft, und sähe sonst eine halbe Datei. Einem Symlink folgt es.
    Die Zwischendatei hat einen eigenen Namen je Aufruf: Mit festem Namen nahmen sich zwei Schreiber die Datei weg."""
    pfad = Path(pfad).resolve()
    fd, neu = tempfile.mkstemp(dir=pfad.parent, prefix=pfad.name + ".", suffix=".neu")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        if pfad.exists():  # mkstemp legt 0600 an; eine vorhandene Datei behält ihre Rechte
            os.chmod(neu, pfad.stat().st_mode & 0o777)
        os.replace(neu, pfad)
    except BaseException:
        Path(neu).unlink(missing_ok=True)
        raise


def aktuell(ordner):
    try:
        wert = json.loads((Path(ordner) / "harness.json").read_text(encoding="utf-8")).get("harness")
    except (OSError, ValueError, AttributeError):
        return STANDARD
    return wert if isinstance(wert, str) and wert in HARNESSE else STANDARD


def merken(ordner, harness_id):
    if harness_id not in HARNESSE:
        raise ValueError(f"unbekannter Harness: {harness_id}")
    schreiben(Path(ordner) / "harness.json", json.dumps({"harness": harness_id}) + "\n")
