"""Prüft einen Umbau an template.html: Sieht das Dashboard danach gleich aus, und tun die Knöpfe dasselbe?

Für das JavaScript im Template gibt es keine Tests. Dieses Skript füllt die Vorlage aus dem Arbeitsordner und die aus
einem Git-Stand mit demselben Inventar, lässt beide von einem Chromium-Browser ohne Fenster rendern und vergleicht:

- den sichtbaren Text aller Tabs, einmal als Datei geöffnet und einmal wie vom Server ausgeliefert (mit Aktionsknöpfen),
- was die Knöpfe an Regeln und Anweisungen auslösen: Rückfrage, Aufruf an den Server und Meldung danach, je einmal
  mit Erfolg, mit Fehler und wenn der Server inzwischen auf dem anderen Harness steht.

Ohne Angabe nimmt es beide Inventare aus dem Datenordner. Die sagen nur etwas über das, was dort vorkommt: Ist auf dem
Rechner nichts offen, bemerkt der Vergleich eine falsche Rechnung bei offenen Empfehlungen nicht. Dafür ein eigenes
Inventar mitgeben, in dem etwas offen ist. Ändert sich die Form des Inventars mit, ist der Vergleich nur mit einem
Inventar aussagekräftig, das die alte und die neue Vorlage lesen können.

Handarbeit beim Entwickeln, kein Teil der Tests: Es braucht einen Browser, und den hat der Lauf bei GitHub nicht.

Aufruf: python3 seitenvergleich.py [--gegen HEAD] [inventar.json …]
"""

import argparse
import difflib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import build
import harness

BROWSER = ["/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
           "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
           "/Applications/Chromium.app/Contents/MacOS/Chromium",
           "brave-browser", "google-chrome", "chromium", "chromium-browser"]
TOKEN_LEER = '<script id="token" type="application/json">null</script>'
# Läuft in der gefüllten Seite. fetch und frage() sind ersetzt: Nichts geht an einen Server, und die Rückfrage
# beantwortet sich selbst. Den <dialog> durchzuklicken geht ohne Fenster nicht: Sein close kommt dort verzögert an,
# und der Schritt landete beim nächsten Klick. /api/neu schlägt fehl, sonst lüde die Seite neu und begänne von vorn.
KLICKS = r"""
<script>
(async () => {
  const log = []; let modus = "gut";
  window.fetch = async (pfad, opt) => {
    log.push("  fetch " + pfad + " " + (opt.body || ""));
    if (modus === "wechsel") return { status: 409, json: async () => ({ ok: false, harness: "anderer", ausgabe: "Der Server steht auf dem anderen Harness." }) };
    const antwort = pfad === "/api/ausfuehren" ? (modus === "gut" ? { ok: true, ausgabe: "gut" } : { ok: false, ausgabe: "kaputt" })
      : pfad === "/api/neu" ? { ok: false, ausgabe: "neu-kaputt" } : { laeuft: false };
    return { status: 200, json: async () => antwort };
  };
  window.frage = async (titel, text, ja, liste = []) => { log.push("  frage: " + JSON.stringify([titel, text, ja, liste])); return true; };
  const warte = ms => new Promise(f => setTimeout(f, ms));
  const meldung = () => "  meldung: " + document.getElementById("meldung-bereich").textContent.replace(/\s+/g, " ").trim().slice(0, 300);
  async function klick(knopf, erwartet) {
    const vorher = log.length;
    knopf.click();
    for (let n = 0; n < 100 && (log.length < vorher + erwartet || beschaeftigt); n++) await warte(20);
    await warte(40);
    log.push(meldung());
  }
  await warte(300);
  try {
    for (const attr of ["data-recht", "data-anweisung"]) {
      const anzahl = Math.min(document.querySelectorAll(`[${attr}]`).length, 8);
      for (let i = 0; i < anzahl; i++) for (modus of ["gut", "kaputt"]) {
        const knopf = document.querySelectorAll(`[${attr}]`)[i];
        log.push(`${attr} ${knopf.getAttribute(attr)} „${knopf.textContent}“ (${modus})`);
        await klick(knopf, 2);
      }
    }
    /* zuletzt, weil die Seite danach als veraltet gilt */
    modus = "wechsel";
    for (const attr of ["data-recht", "data-anweisung"]) {
      const knopf = document.querySelector(`[${attr}]`);
      if (!knopf) continue;
      log.push(`${attr} ${knopf.getAttribute(attr)} (wechsel)`);
      await klick(knopf, 2);
    }
  } catch (fehler) { log.push("ABBRUCH: " + fehler); }
  const pre = document.createElement("pre"); pre.id = "protokoll"; pre.textContent = log.join("\n");
  document.body.appendChild(pre);
})();
</script>"""


def browser():
    for kandidat in [os.environ.get("BROWSER_BIN"), *BROWSER]:
        pfad = kandidat and (kandidat if Path(kandidat).is_file() else shutil.which(kandidat))
        if pfad:
            return pfad
    sys.exit("Kein Chromium-Browser gefunden (Brave, Chrome, Chromium). Mit BROWSER_BIN=<Pfad> angeben.")


def gerendert(seite, ordner, budget):
    """Das DOM, nachdem die Seite ihr JavaScript ausgeführt hat."""
    datei = Path(ordner) / "seite.html"
    datei.write_text(seite, encoding="utf-8")
    return subprocess.run([browser(), "--headless=new", "--dump-dom", f"--virtual-time-budget={budget}", datei.as_uri()],
                          capture_output=True, text=True, timeout=300).stdout


def sichtbarer_text(dom):
    """Je Zeile ein Textstück. Dazu, was in Attributen steht und trotzdem zählt: der Text hinter „kopieren“, Hinweise beim
    Überfahren, Feldinhalte."""
    dom = re.sub(r"<(script|style)\b.*?</\1>", "", dom, flags=re.S)
    dom = re.sub(r'(data-copy|title|value|placeholder|aria-label)="([^"]*)"', r">§\1=\2§<", dom)
    return [z.strip() for z in html.unescape(re.sub(r"<[^>]+>", "\n", dom)).split("\n") if z.strip()]


def ansichten(vorlage, inventar, ordner):
    """Was es zu vergleichen gibt, je als Liste von Zeilen."""
    seite = build.seite(vorlage, inventar)
    mit_server = seite.replace(TOKEN_LEER, '<script id="token" type="application/json">"x"</script>')
    protokoll = re.search(r'<pre id="protokoll">(.*?)</pre>', gerendert(mit_server.replace("</body>", KLICKS + "</body>"), ordner, 60000), re.S)
    return {"Text als Datei": sichtbarer_text(gerendert(seite, ordner, 8000)),
            "Text mit Server": sichtbarer_text(gerendert(mit_server, ordner, 8000)),
            "Klicks": html.unescape(protokoll.group(1)).split("\n") if protokoll else ["kein Protokoll: Das Skript in der Seite kam nicht bis zum Ende"]}


def main():
    parser = argparse.ArgumentParser(description="Vergleicht das Dashboard aus template.html mit dem aus einem Git-Stand")
    parser.add_argument("--gegen", default="HEAD", help="Git-Stand der alten Vorlage (Vorgabe: HEAD)")
    parser.add_argument("inventare", nargs="*", type=Path, help="ohne Angabe beide Inventare aus dem Datenordner")
    wahl = parser.parse_args()
    inventare = wahl.inventare or [d for h in harness.HARNESSE.values() if (d := harness.DATEN / h["inventar"]).is_file()]
    if not inventare:
        sys.exit(f"Kein Inventar in {harness.DATEN}: erst python3 build.py ausführen oder eine Datei angeben.")
    alt = subprocess.run(["git", "show", f"{wahl.gegen}:template.html"], cwd=build.HIER, capture_output=True, text=True)
    if alt.returncode:
        sys.exit(f"template.html aus {wahl.gegen} nicht lesbar: {alt.stderr.strip()}")
    neu = (build.HIER / "template.html").read_text(encoding="utf-8")
    verschieden = False
    with tempfile.TemporaryDirectory() as ordner:
        for datei in inventare:
            inventar = json.loads(datei.read_text(encoding="utf-8"))
            vorher, nachher = ansichten(alt.stdout, inventar, ordner), ansichten(neu, inventar, ordner)
            for name in vorher:
                gleich = vorher[name] == nachher[name]
                verschieden |= not gleich
                umfang = sum(z.startswith("data-") for z in nachher[name]) if name == "Klicks" else len(nachher[name])
                print(f"{'gleich     ' if gleich else 'VERSCHIEDEN'}  {datei.name}: {name} ({umfang} {'Klicks' if name == 'Klicks' else 'Zeilen'})")
                if not gleich:
                    for zeile in list(difflib.unified_diff(vorher[name], nachher[name], wahl.gegen, "Arbeitsordner", lineterm="", n=1))[:40]:
                        print("    " + zeile[:200])
            if not inventar.get("ersparnis"):
                print(f"             {datei.name}: Ersparnis 0, offene Empfehlungen zum Abschalten oder Entfernen prüft dieser Lauf also nicht")
    sys.exit(1 if verschieden else 0)


if __name__ == "__main__":
    main()
