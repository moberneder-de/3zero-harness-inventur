"""Wöchentlicher Lauf: liest den Bestand neu ein und meldet sich nur, wenn es seit der letzten Meldung Neues zu tun gibt.

    python3 wochenlauf.py einrichten   jeden Montag um 9 Uhr (macOS: launchd, Linux: systemd-Timer)
    python3 wochenlauf.py entfernen
    python3 wochenlauf.py              ein Lauf, so wie der Zeitplan ihn startet

Nur build.py, keine Bewertung und keine Suche: Die brauchen claude oder codex und lesen fremde Texte. Der Lauf ändert
nichts an der Konfiguration; er schreibt nur in den Datenordner (Inventar, Dashboard, Verlauf, wochenlauf.json).
Eine Mitteilung per osascript öffnet beim Klick den Skripteditor, nicht das Dashboard; sie sagt deshalb, wo es liegt.
"""

import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import harness
import werkzeuge

HIER = Path(__file__).resolve().parent
KENNUNG = "de.3zero.harness-inventur.wochenlauf"  # launchd
DIENST = "3zero-harness-inventur-wochenlauf"  # systemd


def bauen():
    subprocess.run([sys.executable, str(HIER / "build.py")], check=True, timeout=900)


def offen(inv):
    """Was zu tun ist, je Punkt ein fester Schlüssel und ein kurzer Text."""
    kurz = lambda i: i.split(":", 1)[-1]
    punkte = {}
    a = inv.get("aenderungen") or {}
    for wie in ("neu", "entfernt"):
        for ids in (a.get(wie) or {}).values():
            for i in ids:
                punkte[f"{wie}:{i}"] = f"{wie}: {kurz(i)}"
    for s in inv.get("skills") or []:
        if s.get("ungenutzt"):
            punkte[f"ungenutzt:{s['id']}"] = f"ungenutzt: {s['name']}"
    for w in inv.get("werkzeuge") or []:
        if w.get("status") in werkzeuge.ZU_TUN:
            punkte[f"werkzeug:{w['id']}:{w['status']}"] = f"{w['name']} {w['status']}"
    for q in inv.get("quellcode") or []:
        for p in q.get("pakete") or []:
            for m in p.get("meldungen") or []:
                punkte[f"meldung:{q['name']}:{p['name']}:{m.get('id')}"] = f"Sicherheitsmeldung: {p['name']} in {q['name']}"
    return punkte


def text(punkte):
    kopf = "1 neuer Punkt" if len(punkte) == 1 else f"{len(punkte)} neue Punkte"
    rest = f" und {len(punkte) - 5} weitere" if len(punkte) > 5 else ""
    return f"{kopf}: {', '.join(punkte[:5])}{rest}"


def melden(punkte):
    """Die Namen gehen als Argument an osascript, nie in den Skripttext: Ein Anführungszeichen in einem Skill-Namen
    führte dort sonst AppleScript aus."""
    nachricht = text(punkte)
    if platform.system() == "Darwin":
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        'display notification (item 1 of argv) with title "Harness-Inventur" subtitle "Details: /3zero-harness-inventur"',
                        "-e", "end run", nachricht], check=False)
    elif notify := shutil.which("notify-send"):
        subprocess.run([notify, "Harness-Inventur", nachricht], check=False)
    else:
        print(nachricht)


def laufen():
    bauen()
    h = harness.aktuell(harness.DATEN)
    inv = json.loads((harness.DATEN / harness.HARNESSE[h]["inventar"]).read_text(encoding="utf-8"))
    jetzt = offen(inv)
    datei = harness.DATEN / "wochenlauf.json"
    try:
        gemeldet = set(json.loads(datei.read_text(encoding="utf-8")).get("gemeldet") or [])
    except (OSError, ValueError, AttributeError, TypeError):
        gemeldet = set()
    neu = [t for k, t in jetzt.items() if k not in gemeldet]
    if neu:
        melden(neu)
    # nur, was noch offen ist: Kehrt etwas Erledigtes zurück, meldet der nächste Lauf es wieder
    harness.schreiben(datei, json.dumps({"gemeldet": sorted(jetzt)}, ensure_ascii=False, indent=1))


def launchd_plist(python, skript, pfad, daten):
    """PATH aus der Einrichtung: launchd startet mit /usr/bin:/bin, build.py braucht aber claude, codex und gh."""
    log = str(Path(daten) / "wochenlauf.log")
    return plistlib.dumps({"Label": KENNUNG, "ProgramArguments": [python, skript],
                           "StartCalendarInterval": {"Weekday": 1, "Hour": 9, "Minute": 0},
                           "EnvironmentVariables": {"PATH": pfad}, "StandardOutPath": log, "StandardErrorPath": log})


def systemd_zitat(wert):
    """In Anführungszeichen, mit Backslash und `"` maskiert; `%` leitet in systemd einen Platzhalter ein."""
    return '"' + wert.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def systemd_wort(wert):
    return systemd_zitat(wert) if any(z in wert for z in ' "\\%') else wert


def systemd_dateien(python, skript, pfad):
    """Persistent: Ein Lauf, den der Rechner verschlafen hat, kommt beim nächsten Start."""
    dienst = (f"[Unit]\nDescription=Harness-Inventur, wöchentlicher Lauf\n\n[Service]\nType=oneshot\n"
              f"Environment={systemd_zitat('PATH=' + pfad)}\nExecStart={systemd_wort(python)} {systemd_wort(skript)}\n")
    timer = (f"[Unit]\nDescription=Harness-Inventur jeden Montag\n\n[Timer]\nOnCalendar=Mon *-*-* 09:00:00\nPersistent=true\n\n"
             f"[Install]\nWantedBy=timers.target\n")
    return dienst, timer


def einrichten():
    """python3 aus dem PATH, nicht sys.executable: Homebrew löscht beim Update den Ordner der alten Version, der Link bleibt."""
    python, skript, pfad = shutil.which("python3") or sys.executable, str(HIER / "wochenlauf.py"), os.environ.get("PATH", "")
    harness.DATEN.mkdir(parents=True, exist_ok=True)
    if platform.system() == "Darwin":
        ziel = Path.home() / "Library/LaunchAgents" / f"{KENNUNG}.plist"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        harness.schreiben(ziel, launchd_plist(python, skript, pfad, harness.DATEN).decode())
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{KENNUNG}"], check=False, capture_output=True)  # ein alter Stand
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(ziel)], check=True)
        return ziel
    if platform.system() == "Linux":
        ordner = Path.home() / ".config/systemd/user"
        ordner.mkdir(parents=True, exist_ok=True)
        dienst, timer = systemd_dateien(python, skript, pfad)
        harness.schreiben(ordner / f"{DIENST}.service", dienst)
        harness.schreiben(ordner / f"{DIENST}.timer", timer)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", f"{DIENST}.timer"], check=True)
        return ordner / f"{DIENST}.timer"
    raise SystemExit("Der wöchentliche Lauf geht nur unter macOS und Linux.")


def entfernen():
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{KENNUNG}"], check=False, capture_output=True)
        (Path.home() / "Library/LaunchAgents" / f"{KENNUNG}.plist").unlink(missing_ok=True)
    elif platform.system() == "Linux":
        ordner = Path.home() / ".config/systemd/user"
        subprocess.run(["systemctl", "--user", "disable", "--now", f"{DIENST}.timer"], check=False, capture_output=True)
        for endung in ("service", "timer"):
            (ordner / f"{DIENST}.{endung}").unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)


def main(argv=None):
    befehl = (argv if argv is not None else sys.argv[1:])[:1]
    if befehl == ["einrichten"]:
        print(f"Eingerichtet: jeden Montag um 9 Uhr ({einrichten()}). Entfernen mit: python3 {HIER / 'wochenlauf.py'} entfernen")
    elif befehl == ["entfernen"]:
        entfernen()
        print("Der wöchentliche Lauf ist entfernt.")
    elif not befehl:
        laufen()
    else:
        raise SystemExit("Aufruf: python3 wochenlauf.py [einrichten|entfernen]")


if __name__ == "__main__":
    main()
