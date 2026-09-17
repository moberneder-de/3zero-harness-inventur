"""Rendert app-icon.svg nach app-icon.png, dem apple-touch-icon fürs Dock.

Einmalschritt von Hand, keine Abhängigkeit von build.py: das PNG liegt danach im Repo.
Rasterisiert wird die echte Quelle mit `qlmanage`, einem macOS-Bordmittel — app-icon.svg
bleibt damit die einzige Stelle, an der die Formen stehen.

Vorher fällt die Eckenrundung aus der Quelle: Apple legt über das apple-touch-icon eine
eigene Maske, das Bild muss also ein volles Quadrat sein. Runde Ecken kämen dabei doppelt,
und qlmanage rendert sie weiß statt durchsichtig, also ließen sie sich hinterher nicht mehr
von der weißen Zeichnung trennen. Braucht Pillow.

Aufruf: python3 render-app-icon.py
"""

import subprocess
import tempfile
from pathlib import Path

from PIL import Image

HIER = Path(__file__).resolve().parent
QUELLE = HIER / "app-icon.svg"
ZIEL = HIER / "app-icon.png"
KANTE = 512
RUNDUNG = ' rx="14"'
GRUND = (0, 175, 208)  # --cyan aus dem Brand-Kit, die Fläche der Quelle

svg = QUELLE.read_text(encoding="utf-8")
if RUNDUNG not in svg:
    raise SystemExit(f'{QUELLE.name} hat kein{RUNDUNG!r} mehr — prüfen, wie die Ecken jetzt gerundet werden.')

with tempfile.TemporaryDirectory() as tmp:
    voll = Path(tmp) / "voll.svg"
    voll.write_text(svg.replace(RUNDUNG, "", 1), encoding="utf-8")
    subprocess.run(["qlmanage", "-t", "-s", str(KANTE), "-o", tmp, str(voll)], check=True, capture_output=True)
    erzeugt = voll.with_name(voll.name + ".png")
    if not erzeugt.is_file():
        raise SystemExit("qlmanage hat kein PNG abgelegt.")
    bild = Image.open(erzeugt).convert("RGB")

# qlmanage skaliert stillschweigend anders, wenn Maße und viewBox nicht zusammenpassen; dann
# säße das Icon zu klein in seiner Fläche und die Ecken wären nicht mehr die Markenfarbe.
if bild.size != (KANTE, KANTE):
    raise SystemExit(f"qlmanage lieferte {bild.size[0]}x{bild.size[1]} statt {KANTE}x{KANTE}.")
ecken = [bild.getpixel(xy) for xy in ((0, 0), (KANTE - 1, 0), (0, KANTE - 1), (KANTE - 1, KANTE - 1))]
if any(e != GRUND for e in ecken):
    raise SystemExit(f"Die Ecken sind {ecken}, erwartet war überall {GRUND}: Das Bild füllt sein Quadrat nicht.")

bild.save(ZIEL, optimize=True)
print(f"{ZIEL} geschrieben")
