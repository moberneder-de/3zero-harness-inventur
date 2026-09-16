"""Rendert ../app-icon.svg einmalig nach ../app-icon.png.

Einmalschritt von Hand, keine Abhängigkeit von build.py: das PNG liegt danach im Repo.
Die Formen sind hier nachgezeichnet, nicht aus dem SVG gelesen — ändert sich app-icon.svg,
muss dieses Skript mitgeändert werden. Gezeichnet wird 4-fach überabgetastet und dann
verkleinert, das glättet die Kanten. Braucht Pillow.

Aufruf: python3 icon-entwuerfe/render.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

E = 64          # Kantenlänge der viewBox aus dem SVG
ZIEL = 512
UEBER = 4
S = ZIEL * UEBER / E   # Maßstab viewBox -> Leinwand

CYAN = (0, 175, 208, 255)
WEISS = (255, 255, 255, 255)


def p(x, y):
    return (x * S, y * S)


def bezier(p0, p1, p2, p3, schritte=80):
    punkte = []
    for i in range(schritte + 1):
        t = i / schritte
        u = 1 - t
        x = u**3 * p0[0] + 3 * u**2 * t * p1[0] + 3 * u * t**2 * p2[0] + t**3 * p3[0]
        y = u**3 * p0[1] + 3 * u**2 * t * p1[1] + 3 * u * t**2 * p2[1] + t**3 * p3[1]
        punkte.append(p(x, y))
    return punkte


def strich(d, ecken, breite):
    """Polylinie mit runden Ecken und runden Enden, wie stroke-linecap/linejoin round."""
    d.line([p(*e) for e in ecken], fill=WEISS, width=int(breite * S), joint="curve")
    r = breite * S / 2
    for x, y in (ecken[0], ecken[-1]):
        cx, cy = p(x, y)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WEISS)


bild = Image.new("RGB", (ZIEL * UEBER, ZIEL * UEBER), CYAN[:3])
d = ImageDraw.Draw(bild)

# Im SVG rundet rx="14" die Ecken. Hier bleibt die Fläche voll:
# Apple legt über das apple-touch-icon eine eigene Maske, transparente Ecken
# laufen dabei auf manchen Systemen schwarz an.

# <path d="M20 13 H11 V51 H20"/> und <path d="M44 13 H53 V51 H44"/>, stroke-width 3.6
strich(d, [(20, 13), (11, 13), (11, 51), (20, 51)], 3.6)
strich(d, [(44, 13), (53, 13), (53, 51), (44, 51)], 3.6)

# <path d="M32 16 C33.4 26.2 ... Z" fill="#fff"/> — vier kubische Segmente
funke = (
    bezier((32, 16), (33.4, 26.2), (37.8, 30.6), (48, 32))
    + bezier((48, 32), (37.8, 33.4), (33.4, 37.8), (32, 48))
    + bezier((32, 48), (30.6, 37.8), (26.2, 33.4), (16, 32))
    + bezier((16, 32), (26.2, 30.6), (30.6, 26.2), (32, 16))
)
d.polygon(funke, fill=WEISS)

ziel = Path(__file__).resolve().parent.parent / "app-icon.png"
bild.resize((ZIEL, ZIEL), Image.LANCZOS).save(ziel, optimize=True)
print(f"{ziel} geschrieben")
