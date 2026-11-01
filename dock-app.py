#!/usr/bin/env python3
"""Baut die Dock-App „3zero Harness-Inventur“ nach ~/Applications: python3 dock-app.py (nur macOS).

Die App startet server.py im Hintergrund und bleibt offen, solange er läuft. „Beenden“ im Dock beendet auch den
Server (SIGTERM, server.py löscht start.html dann selbst), ein Klick aufs Icon holt den Dashboard-Tab nach vorn
oder öffnet einen, wenn keiner offen ist. Läuft schon ein Server, etwa aus dem Terminal, öffnet sie nur das Dashboard
und beendet sich wieder.

Alles Bordmittel: das Programm ist AppleScript (`osacompile`), das Icon entsteht aus inventur/app-icon.svg mit `sips`
und `iconutil`. python3 und PATH kommen aus diesem Aufruf, wie beim Wochenlauf: Eine App aus dem Dock bekommt nur den
Grund-PATH des Systems, dort fehlen Homebrew-Python, claude, codex und gh. Nach einem Umzug des Projekts neu bauen.
"""

import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from inventur import harness

NAME = "3zero Harness-Inventur"
KENNUNG = "de.3zero.harness-inventur"
ZIEL = Path.home() / "Applications" / f"{NAME}.app"
# Apples Raster für Mac-Icons: Die Fläche füllt 824 von 1024 Punkten, der Rand bleibt durchsichtig. Anders als beim
# apple-touch-icon legt macOS keine eigene Maske darüber, die Ecken bleiben also die runden aus der Quelle.
RAND, FLAECHE = 64 * 100 / 1024, 824 / 1024

# Signierte Programme schreiben ihre Variablen beim Beenden nicht zurück (nachgemessen): `server` lebt nur im Speicher.
PROGRAMM = """global server

on run
	set server to missing value
	if serverLaeuft() then
		dashboard()
		quit
	else
		-- Nicht „cd … && python … &“: Das schickt beides in eine Unter-Shell, und $! wäre deren Nummer statt der von
		-- Python, „kill“ ließe den Server weiterlaufen (nachgemessen). umask: Das Protokoll nennt den Schlüssel.
		set server to do shell script "cd " & quoted form of projekt & " || exit 1; umask 077; PATH=" & quoted form of pfad & " " & quoted form of python & " server.py > " & quoted form of protokoll & " 2>&1 < /dev/null & echo $!"
	end if
end run

on reopen
	dashboard()
end reopen

on idle
	if server is missing value then return 5
	try
		do shell script "kill -0 " & server
	on error
		set server to missing value
		activate -- sonst wartet der Hinweis unsichtbar, bis jemand aufs hüpfende Icon klickt (nachgemessen)
		display dialog "Der Server ist beendet." & return & return & (do shell script "tail -n 5 " & quoted form of protokoll) buttons {"OK"} default button 1 with title "3zero Harness-Inventur"
		quit
	end try
	return 5
end idle

on quit
	if server is not missing value then
		try
			do shell script "kill " & server
		end try
		-- Der Dashboard-Tab bleibt offen, sonst verschwände nur der Punkt unter dem Icon.
		display notification "Server beendet." with title "3zero Harness-Inventur"
	end if
	continue quit
end quit

on serverLaeuft()
	try
		do shell script "curl -s -o /dev/null -m 2 http://127.0.0.1:8765/"
		return true
	on error
		return false
	end try
end serverLaeuft

-- Holt einen offenen Dashboard-Tab nach vorn (tab.js), sonst einen neuen Tab. Vor dem ersten Einlesen gibt es
-- start.html noch nicht, dann öffnet der Server den Browser gleich selbst.
on dashboard()
	try
		if (do shell script "osascript -l JavaScript " & quoted form of POSIX path of (path to resource "tab.js") & " " & quoted form of startdatei) is "ja" then return
	end try
	do shell script "cd " & quoted form of projekt & " && if [ -f " & quoted form of startdatei & " ]; then " & quoted form of python & " -c 'import sys; from inventur.server import browser_oeffnen; browser_oeffnen(sys.argv[1])' " & quoted form of startdatei & "; fi"
end dashboard
"""

# Ohne das öffnete jeder Klick aufs Icon einen weiteren Tab. Gesucht wird nur die Adresse mit dem Schlüssel dieses
# Laufs: Tabs aus früheren Läufen bleiben offen, gehen aber nicht mehr. Das Wörterbuch (`tabs`, `activeTabIndex`)
# haben Brave, Chrome und Edge; jeder Fehler, auch eine verweigerte Erlaubnis, führt zu „nein“ und damit zum neuen Tab.
TAB = r"""ObjC.import("AppKit");

function run(argv) {
  const inhalt = $.NSString.stringWithContentsOfFileEncodingError(argv[0], $.NSUTF8StringEncoding, null);
  if (inhalt.isNil()) return "nein";
  const adresse = inhalt.js.match(/http:\/\/127\.0\.0\.1:8765\/\?k=[0-9a-f]+/);
  if (!adresse) return "nein";
  const pfad = $.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL($.NSURL.URLWithString("https://python.org")).path.js;
  const browser = Application(pfad);
  if (!browser.running()) return "nein";
  for (const fenster of browser.windows()) {
    const i = fenster.tabs.url().findIndex(url => url && url.startsWith(adresse[0]));
    if (i >= 0) {
      fenster.activeTabIndex = i + 1;
      fenster.index = 1;
      browser.activate();
      return "ja";
    }
  }
  return "nein";
}
"""


def text(wert):
    """Ein AppleScript-String: Ein Anführungszeichen im Pfad darf ihn nicht beenden."""
    return '"' + str(wert).replace("\\", "\\\\").replace('"', '\\"') + '"'


def programm(python, pfad):
    werte = {"projekt": harness.PROJEKT, "python": python, "pfad": pfad,
             "startdatei": harness.DATEN / "start.html", "protokoll": harness.DATEN / "dock-app.log"}
    return "".join(f"property {k} : {text(v)}\n" for k, v in werte.items()) + "\n" + PROGRAMM


def icon(ordner):
    """applet.icns aus app-icon.svg, jede Größe direkt aus der Quelle gerendert statt aus einem Bild verkleinert."""
    svg = (harness.PROJEKT / "inventur" / "app-icon.svg").read_text(encoding="utf-8")
    inhalt = svg.split(">", 1)[1].rsplit("</svg>", 1)[0]  # alles innerhalb von <svg …>
    satz = Path(ordner, "applet.iconset")
    satz.mkdir()
    for kante in (16, 32, 128, 256, 512):
        for faktor, endung in ((1, ""), (2, "@2x")):
            n = kante * faktor
            quelle = Path(ordner, f"{n}.svg")
            quelle.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="{n}" height="{n}">'
                              f'<g transform="translate({RAND} {RAND}) scale({FLAECHE})">{inhalt}</g></svg>', encoding="utf-8")
            subprocess.run(["sips", "-s", "format", "png", str(quelle), "--out", str(satz / f"icon_{kante}x{kante}{endung}.png")],
                           check=True, capture_output=True)
    subprocess.run(["iconutil", "-c", "icns", str(satz)], check=True, capture_output=True)
    return Path(ordner, "applet.icns")


def main():
    if sys.platform != "darwin":
        sys.exit("dock-app.py baut eine Mac-App und läuft nur unter macOS.")
    info = ZIEL / "Contents" / "Info.plist"
    if ZIEL.exists():
        if not info.is_file() or plistlib.loads(info.read_bytes()).get("CFBundleIdentifier") != KENNUNG:
            sys.exit(f"{ZIEL} gibt es schon und stammt nicht von dock-app.py. Bitte selbst wegräumen.")
        shutil.rmtree(ZIEL)
    ZIEL.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        quelle = Path(tmp, "programm.applescript")
        quelle.write_text(programm(shutil.which("python3") or sys.executable, os.environ.get("PATH", "")), encoding="utf-8")
        subprocess.run(["osacompile", "-s", "-o", str(ZIEL), str(quelle)], check=True, capture_output=True)
        shutil.copyfile(icon(tmp), ZIEL / "Contents" / "Resources" / "applet.icns")
    (ZIEL / "Contents" / "Resources" / "tab.js").write_text(TAB, encoding="utf-8")
    # Sonst gälte das Skript-Icon aus dem Asset-Katalog (CFBundleIconName) statt applet.icns.
    (ZIEL / "Contents" / "Resources" / "Assets.car").unlink()
    werte = plistlib.loads(info.read_bytes())
    werte.pop("CFBundleIconName", None)
    werte["CFBundleIdentifier"] = KENNUNG
    werte["NSAppleEventsUsageDescription"] = "Damit ein Klick aufs Icon den offenen Dashboard-Tab nach vorn holt, statt einen neuen zu öffnen."
    info.write_bytes(plistlib.dumps(werte))
    # Icon und Info.plist sind nach osacompile geändert, die Signatur muss sie wieder abdecken.
    subprocess.run(["codesign", "--force", "--sign", "-", str(ZIEL)], check=True, capture_output=True)
    print(f"{ZIEL} gebaut. Einmal öffnen und das Icon im Dock mit Rechtsklick > Optionen > Im Dock behalten.")


if __name__ == "__main__":
    main()
