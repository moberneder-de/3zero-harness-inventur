import json
import plistlib
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from inventur import wochenlauf


def inventar(**extra):
    return {"harness": "claude-code", "aenderungen": None, "skills": [], "werkzeuge": [], "quellcode": [], **extra}


class Offen(unittest.TestCase):
    def test_was_zu_tun_ist(self):
        inv = inventar(
            aenderungen={"seit": "2026-09-22", "neu": {"mcp": ["mcp:aimitarnie"]}, "entfernt": {"skills": ["skill:upnote"]}},
            skills=[{"id": "skill:a", "name": "a", "ungenutzt": True}, {"id": "skill:b", "name": "b"}],
            werkzeuge=[{"id": "node", "name": "Node.js", "status": "veraltet"}, {"id": "gh", "name": "GitHub CLI", "status": "aktuell"},
                       {"id": "bun", "name": "Bun", "status": "fehlt"}],
            quellcode=[{"name": "q", "pakete": [{"name": "zod", "meldungen": [{"id": "GHSA-1"}]}, {"name": "x", "meldungen": []}]}])
        self.assertEqual(wochenlauf.offen(inv), {
            "neu:mcp:aimitarnie": "neu: aimitarnie",
            "entfernt:skill:upnote": "entfernt: upnote",
            "ungenutzt:skill:a": "ungenutzt: a",
            "werkzeug:node:veraltet": "Node.js veraltet",
            "werkzeug:bun:fehlt": "Bun fehlt",
            "meldung:q:zod:GHSA-1": "Sicherheitsmeldung: zod in q",
        })

    def test_nichts(self):
        self.assertEqual(wochenlauf.offen(inventar(aenderungen={"seit": "x", "neu": {}, "entfernt": {}})), {})


class Laufen(unittest.TestCase):
    def setUp(self):
        self.daten = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.daten)
        self.inv = inventar(werkzeuge=[{"id": "node", "name": "Node.js", "status": "veraltet"}])
        self.datei = "inventar-claude-code.json"
        for p in (mock.patch.object(wochenlauf.harness, "DATEN", self.daten),
                  mock.patch.object(wochenlauf, "bauen", side_effect=lambda: (self.daten / self.datei).write_text(json.dumps(self.inv))),
                  mock.patch.object(wochenlauf, "melden")):
            p.start()
            self.addCleanup(p.stop)

    def test_meldet_nur_was_neu_ist(self):
        """Was schon gemeldet war, kommt nicht jede Woche wieder; ist es erledigt und kehrt zurück, schon."""
        wochenlauf.laufen()
        self.assertEqual(wochenlauf.melden.call_args.args[0], ["Node.js veraltet"])
        wochenlauf.melden.reset_mock()
        wochenlauf.laufen()
        wochenlauf.melden.assert_not_called()
        self.inv["werkzeuge"] = []
        wochenlauf.laufen()
        wochenlauf.melden.assert_not_called()
        self.inv["werkzeuge"] = [{"id": "node", "name": "Node.js", "status": "veraltet"}]
        wochenlauf.laufen()
        wochenlauf.melden.assert_called_once()

    def test_liest_den_zuletzt_genannten_harness(self):
        (self.daten / "harness.json").write_text('{"harness": "codex"}')
        self.datei = "inventar-codex.json"
        wochenlauf.laufen()
        self.assertEqual(wochenlauf.melden.call_args.args[0], ["Node.js veraltet"])


class Melden(unittest.TestCase):
    def test_namen_nur_als_argument(self):
        """Ein Anführungszeichen in einem Skill-Namen darf in AppleScript nichts ausführen."""
        with mock.patch.object(wochenlauf.platform, "system", return_value="Darwin"), mock.patch.object(wochenlauf.subprocess, "run") as run:
            wochenlauf.melden(['neu: a" & do shell script "x', "Node.js veraltet"])
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "osascript")
        self.assertNotIn('do shell script "x', " ".join(argv[:-1]))
        self.assertIn('do shell script "x', argv[-1])
        self.assertIn("2 neue Punkte", argv[-1])

    def test_linux_mit_notify_send(self):
        with mock.patch.object(wochenlauf.platform, "system", return_value="Linux"), \
                mock.patch.object(wochenlauf.shutil, "which", return_value="/usr/bin/notify-send"), \
                mock.patch.object(wochenlauf.subprocess, "run") as run:
            wochenlauf.melden(["Bun fehlt"])
        self.assertEqual(run.call_args.args[0][:2], ["/usr/bin/notify-send", "Harness-Inventur"])

    def test_viele_punkte_werden_gekuerzt(self):
        text = wochenlauf.text([f"neu: s{i}" for i in range(20)])
        self.assertTrue(text.startswith("20 neue Punkte: neu: s0, "))
        self.assertIn("und 15 weitere", text)


class Einrichten(unittest.TestCase):
    def test_launchd_montags_um_neun(self):
        roh = wochenlauf.launchd_plist("/opt/homebrew/bin/python3", "/x/wochenlauf.py", "/opt/homebrew/bin:/usr/bin", Path("/d"))
        p = plistlib.loads(roh)
        self.assertEqual(p["Label"], wochenlauf.KENNUNG)
        self.assertEqual(p["ProgramArguments"], ["/opt/homebrew/bin/python3", "/x/wochenlauf.py"])
        self.assertEqual(p["StartCalendarInterval"], {"Weekday": 1, "Hour": 9, "Minute": 0})
        self.assertEqual(p["EnvironmentVariables"], {"PATH": "/opt/homebrew/bin:/usr/bin"})
        self.assertEqual(p["StandardErrorPath"], "/d/wochenlauf.log")

    def test_systemd_holt_verpasste_laeufe_nach(self):
        dienst, timer = wochenlauf.systemd_dateien("/usr/bin/python3", "/x/wochenlauf.py", "/usr/bin:/bin")
        self.assertIn("ExecStart=/usr/bin/python3 /x/wochenlauf.py", dienst)
        self.assertIn('Environment="PATH=/usr/bin:/bin"', dienst)
        self.assertIn("OnCalendar=Mon *-*-* 09:00:00", timer)
        self.assertIn("Persistent=true", timer)

    def test_pfade_mit_leerzeichen_werden_fuer_systemd_gequotet(self):
        dienst, _ = wochenlauf.systemd_dateien("/usr/bin/python3", "/home/a b/wochenlauf.py", "/usr/bin")
        self.assertIn('ExecStart=/usr/bin/python3 "/home/a b/wochenlauf.py"', dienst)

    def test_mac_einrichten_und_entfernen(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        with mock.patch.object(wochenlauf.platform, "system", return_value="Darwin"), mock.patch.object(wochenlauf.Path, "home", return_value=tmp), \
                mock.patch.object(wochenlauf.subprocess, "run") as run, mock.patch.object(wochenlauf.os, "getuid", return_value=501, create=True), \
                mock.patch.object(wochenlauf.harness, "DATEN", tmp / "daten"):
            wochenlauf.einrichten()
            datei = tmp / "Library/LaunchAgents" / f"{wochenlauf.KENNUNG}.plist"
            self.assertTrue(datei.exists())
            self.assertEqual(run.call_args_list[-1].args[0], ["launchctl", "bootstrap", "gui/501", str(datei)])
            wochenlauf.entfernen()
            self.assertFalse(datei.exists())
            self.assertEqual(run.call_args_list[-1].args[0], ["launchctl", "bootout", f"gui/501/{wochenlauf.KENNUNG}"])


if __name__ == "__main__":
    unittest.main()
