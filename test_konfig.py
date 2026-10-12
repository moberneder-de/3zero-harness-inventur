import json
import shutil
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

import konfig

SETTINGS_VORHER = '{\n  "env": {\n    "A": "1"\n  },\n  "skillOverrides": {\n    "alt": "off"\n  }\n}\n'
CONFIG = '''# eigene Einstellungen
model = "gpt-test"

[mcp_servers.node_repl]
command = "/x/node_repl"   # Kommentar bleibt

[plugins."documents@openai-primary-runtime"]
enabled = true
'''
DOCUMENTS_AUS = {"toml": {"tabelle": 'plugins."documents@openai-primary-runtime"', "schluessel": "enabled", "wert": "false"}}


class Ordner(unittest.TestCase):
    """Schritt hinein, Datei prüfen: ohne Server und ohne Inventar, alles in einem Temp-Ordner."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.settings = self.tmp / "settings.json"
        self.settings.write_text(SETTINGS_VORHER)
        self.config = self.tmp / "config.toml"
        self.config.write_text(CONFIG)
        self.k = konfig.Konfig(self.settings, self.config)

    def ok(self, schritt, name="x"):
        antwort = self.k.ausfuehren(name, schritt)
        self.assertTrue(antwort["ok"], antwort)
        return antwort

    def scheitert(self, schritt, name="x"):
        antwort = self.k.ausfuehren(name, schritt)
        self.assertFalse(antwort["ok"], antwort)
        return antwort

    def sicherung(self, datei):
        """Die eine Sicherung <datei>.bak-<Zeitstempel> neben der Datei."""
        (treffer,) = datei.parent.glob(datei.name + ".bak-*")
        return treffer

    def skill(self, name, yaml=None):
        """Ein Skill-Ordner mit SKILL.md und, wenn angegeben, agents/openai.yaml (Text oder Bytes)."""
        ordner = self.tmp / "skills" / name
        (ordner / "agents").mkdir(parents=True) if yaml is not None else ordner.mkdir(parents=True)
        (ordner / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        if isinstance(yaml, bytes):
            (ordner / "agents/openai.yaml").write_bytes(yaml)
        elif yaml is not None:
            (ordner / "agents/openai.yaml").write_text(yaml, encoding="utf-8")
        return ordner


class Eingang(Ordner):
    def test_was_keine_datei_aendert_ist_nicht_seine_sache(self):
        """Papierkorb und Befehle führt der Server selbst aus: None heißt „nicht meins“, keine Fehlantwort."""
        for schritt in ({"papierkorb": str(self.tmp / "x")}, {"argv": ["touch", str(self.tmp / "gelaufen")]}):
            with self.subTest(schritt=schritt):
                self.assertIsNone(self.k.ausfuehren("x", schritt))
        self.assertEqual(self.settings.read_text(), SETTINGS_VORHER)


class Einstellungen(Ordner):
    """~/.claude/settings.json und die Einstellungsdateien der Projekte."""

    def test_override_landet_in_settings_und_original_in_sicherung(self):
        self.ok({"override": "off"}, "grilling")
        self.ok({"override": "user-invocable-only"}, "tdd")
        self.assertEqual(self.settings.read_text(), '{\n  "env": {\n    "A": "1"\n  },\n  "skillOverrides": {\n'
                         '    "alt": "off",\n    "grilling": "off",\n    "tdd": "user-invocable-only"\n  }\n}\n')
        self.assertEqual(self.sicherung(self.settings).read_text(), SETTINGS_VORHER)

    def test_override_ohne_bisherige_overrides(self):
        self.settings.write_text("{}\n")
        self.ok({"override": "off"}, "grilling")
        self.assertEqual(json.loads(self.settings.read_text()), {"skillOverrides": {"grilling": "off"}})

    def test_einschalten_nimmt_den_override_weg(self):
        self.ok({"override": None}, "alt")
        self.assertEqual(json.loads(self.settings.read_text()), {"env": {"A": "1"}})  # ein leeres skillOverrides bleibt nicht stehen
        self.assertEqual(self.sicherung(self.settings).read_text(), SETTINGS_VORHER)
        self.scheitert({"override": None}, "alt")  # steht nicht mehr drin

    def test_sicherung_ueberlebt_den_neustart(self):
        """Mit festem Namen sicherte der Server nach einem Neustart den Stand, den er selbst schon geändert hatte,
        über den ursprünglichen. Je Serverlauf deshalb eine eigene Sicherung mit Zeitstempel."""
        with mock.patch.object(konfig.time, "strftime", side_effect=["20260920-170000", "20260920-180000"]):
            self.ok({"override": "off"}, "grilling")
            nach_der_ersten = self.settings.read_text()
            neustart = konfig.Konfig(self.settings, self.config)
            self.assertTrue(neustart.ausfuehren("tdd", {"override": "off"})["ok"])
        self.assertEqual({d.name: d.read_text() for d in self.tmp.glob("settings.json.bak*")},
                         {"settings.json.bak-20260920-170000": SETTINGS_VORHER, "settings.json.bak-20260920-180000": nach_der_ersten})

    def test_nur_die_letzten_sicherungen_bleiben(self):
        """Je Serverlauf kam eine Sicherung dazu, ohne dass je eine ging."""
        for i in range(12):
            (self.tmp / f"settings.json.bak-202609{i + 10:02d}-120000").write_text(str(i))
        (self.tmp / "settings.json.bak-fremd").write_text("bleibt")  # kein Zeitstempel: nicht von uns
        with mock.patch.object(konfig.time, "strftime", return_value="20260925-120000"):
            self.ok({"override": "off"}, "grilling")
        sicherungen = sorted(d.name for d in self.tmp.glob("settings.json.bak-2*"))
        self.assertEqual(len(sicherungen), konfig.SICHERUNGEN)
        self.assertEqual(sicherungen[-1], "settings.json.bak-20260925-120000")
        self.assertTrue((self.tmp / "settings.json.bak-fremd").exists())

    def test_schreiben_laesst_eine_fremde_neu_datei_stehen(self):
        """Mit festem Zwischennamen <datei>.neu überschrieb und verschob der Server eine gleichnamige Datei."""
        fremd = self.tmp / "settings.json.neu"
        fremd.write_text("fremd")
        self.settings.chmod(0o640)
        self.ok({"override": "off"}, "grilling")
        self.assertEqual((fremd.read_text(), json.loads(self.settings.read_text())["skillOverrides"]["grilling"]), ("fremd", "off"))
        self.assertEqual(self.settings.stat().st_mode & 0o777, 0o640)

    def test_verlinkte_settings_bleiben_ein_link(self):
        """Wer settings.json aus einem Dotfiles-Repo verlinkt: os.replace ersetzte sonst den Link selbst durch eine
        normale Datei, und das Repo bekäme keine Änderung mehr zu sehen."""
        echt = self.tmp / "dotfiles" / "settings.json"
        echt.parent.mkdir()
        self.settings.rename(echt)
        self.settings.symlink_to(echt)
        self.ok({"override": "off"}, "grilling")
        self.assertTrue(self.settings.is_symlink())
        self.assertEqual(json.loads(echt.read_text())["skillOverrides"]["grilling"], "off")

    def test_aenderung_wird_zurueckgenommen_wenn_das_nachlesen_scheitert(self):
        """settings.json nimmt denselben Weg wie config.toml: Kommt die Änderung nicht an, steht der alte Stand wieder da."""
        with mock.patch.object(konfig, "text_lesen", side_effect=[SETTINGS_VORHER, OSError("weg")]):
            antwort = self.scheitert({"override": "off"}, "grilling")
        self.assertIn("nicht geändert", antwort["ausgabe"])
        self.assertEqual(self.settings.read_text(), SETTINGS_VORHER)

    def test_mcp_sperre_landet_einmal_in_settings(self):
        self.settings.write_text('{\n  "deniedMcpServers": [\n    {\n      "serverName": "alt"\n    }\n  ]\n}\n')
        vorher = self.settings.read_text()
        for _ in range(2):
            self.ok({"sperren": {"serverName": "claude.ai Gmail"}})
        self.assertEqual(json.loads(self.settings.read_text()),
                         {"deniedMcpServers": [{"serverName": "alt"}, {"serverName": "claude.ai Gmail"}]})
        self.assertEqual(self.sicherung(self.settings).read_text(), vorher)

    def test_entsperren_nimmt_nur_diesen_server(self):
        fremd = {"serverUrl": "https://example.org/*"}
        self.settings.write_text(json.dumps({"deniedMcpServers": [{"serverName": "alt"}, fremd, {"serverName": "x"}]}, indent=2) + "\n")
        self.ok({"entsperren": {"serverName": "x"}})
        self.assertEqual(json.loads(self.settings.read_text()), {"deniedMcpServers": [{"serverName": "alt"}, fremd]})
        self.scheitert({"entsperren": {"serverName": "x"}})
        self.ok({"entsperren": {"serverName": "alt"}})
        self.assertEqual(json.loads(self.settings.read_text()), {"deniedMcpServers": [fremd]})

    def test_hook_entfernen_nimmt_nur_dieses_ereignis(self):
        vorher = {"env": {"A": "1"}, "statusLine": {"type": "command", "command": "zeile.sh"},
                  "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "stop.sh"}]}],
                            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "pruefen.sh"}]}]}}
        self.settings.write_text(json.dumps(vorher, indent=2) + "\n")
        self.ok({"hook_entfernen": "Stop"})
        self.assertEqual(json.loads(self.settings.read_text()), {**vorher, "hooks": {"PreToolUse": vorher["hooks"]["PreToolUse"]}})
        self.scheitert({"hook_entfernen": "Stop"})  # gibt es nicht mehr
        self.ok({"hook_entfernen": "PreToolUse"})
        self.ok({"hook_entfernen": "statusLine"})
        self.assertEqual(json.loads(self.settings.read_text()), {"env": {"A": "1"}})  # ein leeres "hooks" bleibt nicht stehen
        self.assertEqual(json.loads(self.sicherung(self.settings).read_text()), vorher)

    def test_regel_entfernen_nimmt_nur_diese_regel_aus_der_projektdatei(self):
        datei = self.tmp / "P" / ".claude" / "settings.local.json"
        datei.parent.mkdir(parents=True)
        vorher = {"permissions": {"allow": ["Bash(git push *)", "mcp__weg__lesen"], "deny": ["mcp__weg__lesen"]}, "env": {"A": "1"}}
        datei.write_text(json.dumps(vorher, indent=2) + "\n")
        schritt = lambda d, regel: {"regel_entfernen": {"datei": str(d), "art": "allow", "regel": regel}}
        self.ok(schritt(datei, "mcp__weg__lesen"))
        self.assertEqual(json.loads(datei.read_text()), {"permissions": {"allow": ["Bash(git push *)"], "deny": ["mcp__weg__lesen"]}, "env": {"A": "1"}})
        self.assertEqual(json.loads(self.sicherung(datei).read_text()), vorher)
        self.scheitert(schritt(datei, "mcp__weg__lesen"))  # steht nicht mehr da
        self.ok(schritt(datei, "Bash(git push *)"))
        self.assertEqual(json.loads(datei.read_text()), {"permissions": {"deny": ["mcp__weg__lesen"]}, "env": {"A": "1"}})  # eine leere Liste bleibt nicht stehen
        self.assertEqual(self.settings.read_text(), SETTINGS_VORHER)  # die globale Datei bleibt, wie sie ist

    def test_regel_entfernen_nur_in_einstellungsdateien_von_claude_code(self):
        """Der Pfad kommt aus dem Inventar, einer Datei, die auch andere schreiben können."""
        fremd = self.tmp / "P" / "package.json"
        fremd.parent.mkdir()
        fremd.write_text(json.dumps({"permissions": {"allow": ["x"]}}))
        self.scheitert({"regel_entfernen": {"datei": str(fremd), "art": "allow", "regel": "x"}})
        self.assertEqual(json.loads(fremd.read_text()), {"permissions": {"allow": ["x"]}})
        self.assertEqual(list(fremd.parent.glob("*.bak-*")), [])

    def test_anweisung_ausschliessen_und_wieder_einschliessen(self):
        """Geschaltet wird in ~/.claude/settings.json: Die CLAUDE.md selbst liegt oft in einem Repo und bleibt, wie sie ist."""
        datei = self.tmp / "P" / "CLAUDE.md"
        datei.parent.mkdir()
        datei.write_text("Anweisung")
        vorher = {"claudeMdExcludes": ["/alt/CLAUDE.md"], "env": {"A": "1"}}
        self.settings.write_text(json.dumps(vorher, indent=2) + "\n")
        for _ in range(2):  # zweimal ergibt einen Eintrag
            self.ok({"ausschliessen": str(datei)})
        self.assertEqual(json.loads(self.settings.read_text()), {**vorher, "claudeMdExcludes": ["/alt/CLAUDE.md", str(datei)]})
        self.assertEqual(json.loads(self.sicherung(self.settings).read_text()), vorher)
        self.assertEqual(datei.read_text(), "Anweisung")
        self.ok({"einschliessen": str(datei)})
        self.assertEqual(json.loads(self.settings.read_text()), vorher)
        self.scheitert({"einschliessen": str(datei)})  # steht nicht mehr da

    def test_ausschliessen_nimmt_nur_einen_ganzen_pfad(self):
        """Claude Code liest den Eintrag als Muster, und der Wert kommt aus dem Inventar: `/**/CLAUDE.md` schaltete alle ab."""
        for pfad in ["/**/CLAUDE.md", "/p/CLAUDE?.md", "/p/[a]/CLAUDE.md", "CLAUDE.md", "/p/settings.json", "", 3, None, ["/p/CLAUDE.md"]]:
            with self.subTest(pfad=pfad):
                self.scheitert({"ausschliessen": pfad})
        self.assertEqual(self.settings.read_text(), SETTINGS_VORHER)

    def test_output_style_waehlen_und_abwaehlen(self):
        vorher = {"outputStyle": "Erklär es mir einfach", "env": {"A": "1"}}
        self.settings.write_text(json.dumps(vorher, indent=2, ensure_ascii=False) + "\n")
        self.ok({"output_style": None})
        self.assertEqual(json.loads(self.settings.read_text()), {"env": {"A": "1"}})
        self.assertEqual(json.loads(self.sicherung(self.settings).read_text()), vorher)
        self.scheitert({"output_style": None})  # ist schon abgewählt
        self.ok({"output_style": "Knapp"})
        self.assertEqual(json.loads(self.settings.read_text()), {"env": {"A": "1"}, "outputStyle": "Knapp"})
        self.scheitert({"output_style": 3})
        self.assertEqual(json.loads(self.settings.read_text())["outputStyle"], "Knapp")


class TomlText(unittest.TestCase):
    """Die Textfunktionen unter config.toml: Sie ändern eine Stelle und lassen alles andere Zeichen für Zeichen stehen."""

    def test_toml_pfad(self):
        self.assertEqual(konfig.toml_pfad('plugins."a@b".mcp_servers.c'), ["plugins", "a@b", "mcp_servers", "c"])
        self.assertEqual(konfig.toml_pfad("mcp_servers.node_repl"), ["mcp_servers", "node_repl"])

    def test_toml_text_setzen_aendert_nur_die_zeile(self):
        text = konfig.toml_text_setzen(CONFIG, 'plugins."documents@openai-primary-runtime"', "enabled", "false")
        self.assertIn("enabled = false", text)
        self.assertIn("# Kommentar bleibt", text)
        self.assertIn("# eigene Einstellungen", text)
        self.assertNotIn("enabled = true", text)
        # fehlender Schlüssel wird in die Tabelle eingefügt, fehlende Tabelle angehängt
        text = konfig.toml_text_setzen(CONFIG, "mcp_servers.node_repl", "enabled", "false")
        self.assertIn('[mcp_servers.node_repl]\nenabled = false\ncommand', text)
        text = konfig.toml_text_setzen(CONFIG, 'mcp_servers."neu"', "enabled", "false")
        self.assertTrue(text.rstrip().endswith('[mcp_servers."neu"]\nenabled = false'))

    def test_toml_text_setzen_findet_den_kopf_trotz_kommentar(self):
        """Sonst hängte es die Tabelle ein zweites Mal an; die Datei parste nicht mehr und die Aktion schlug fehl."""
        text = konfig.toml_text_setzen('[mcp_servers.a]  # mein Server\nenabled = true\n', "mcp_servers.a", "enabled", "false")
        self.assertEqual(text, '[mcp_servers.a]  # mein Server\nenabled = false\n')

    def test_toml_text_setzen_findet_den_kopf_in_anderer_schreibweise(self):
        """Der Kopf wurde nur als exakter Text gesucht. Bei [mcp_servers."foo"] oder [ mcp_servers.foo ] hängte der Server
        eine zweite Tabelle an; die Datei parste nicht mehr, und die Aktion scheiterte mit „Cannot declare …“."""
        for kopf in ('[mcp_servers."foo"]', "[ mcp_servers . foo ]", "[mcp_servers.'foo']  # Kommentar"):
            with self.subTest(kopf=kopf):
                self.assertEqual(konfig.toml_text_setzen(f'{kopf}\ncommand = "x"\n', "mcp_servers.foo", "enabled", "false"),
                                 f'{kopf}\nenabled = false\ncommand = "x"\n')
        self.assertEqual(konfig.toml_text_setzen("[plugins.'a@b']\nenabled = true\n", 'plugins."a@b"', "enabled", "false"),
                         "[plugins.'a@b']\nenabled = false\n")

    def test_toml_text_setzen_ueberspringt_mehrzeilige_strings(self):
        """Eine Zeile in einem mehrzeiligen String, die mit [ beginnt, beendete die Tabelle zu früh oder galt selbst als
        Kopf: Der Server schrieb in den String oder fügte eine zweite enabled-Zeile ein, und die Datei parste nicht mehr."""
        text = '[mcp_servers.foo]\ndescription = """\n[kein Kopf]\nenabled = true\n"""\nenabled = true\n'
        neu = tomllib.loads(konfig.toml_text_setzen(text, "mcp_servers.foo", "enabled", "false"))
        self.assertEqual(neu["mcp_servers"]["foo"], {"description": "[kein Kopf]\nenabled = true\n", "enabled": False})
        text = "notiz = '''\n[mcp_servers.foo]\n'''\n[mcp_servers.foo]\nenabled = true\n"
        neu = tomllib.loads(konfig.toml_text_setzen(text, "mcp_servers.foo", "enabled", "false"))
        self.assertEqual(neu, {"notiz": "[mcp_servers.foo]\n", "mcp_servers": {"foo": {"enabled": False}}})

    def test_toml_text_setzen_behaelt_crlf(self):
        """splitlines() und "\\n".join machten aus einer CRLF-Datei stillschweigend eine mit LF, und im Dotfiles-Repo
        war jede Zeile geändert."""
        text = '# eigene\r\n[mcp_servers.foo]\r\ncommand = "x"\r\n'
        self.assertEqual(konfig.toml_text_setzen(text, "mcp_servers.foo", "enabled", "false"),
                         '# eigene\r\n[mcp_servers.foo]\r\nenabled = false\r\ncommand = "x"\r\n')
        self.assertEqual(konfig.toml_text_setzen(text, "mcp_servers.neu", "enabled", "false"),
                         text + "\r\n[mcp_servers.neu]\r\nenabled = false\r\n")

    def test_toml_tabelle_entfernen_text(self):
        text = '[a]\nx = 1\n\n[projects."/weg"]\ntrust_level = "trusted"\nnotiz = """\n[projects."/da"]\n"""\n\n# bleibt\n[projects."/da"]\ny = 2\n'
        self.assertEqual(konfig.toml_tabelle_entfernen_text(text, ["projects", "/weg"]), ('[a]\nx = 1\n\n# bleibt\n[projects."/da"]\ny = 2\n', True))
        self.assertEqual(konfig.toml_tabelle_entfernen_text(text, ["projects", "/fehlt"]), (text, False))
        self.assertEqual(konfig.toml_tabelle_entfernen_text('[projects."/weg"]\r\nx = 1\r\n', ["projects", "/weg"]), ("", True))

    def test_nur_manuell_gesetzt_zaehlt_nur_den_policy_block(self):
        """Die Erfolgskontrolle nach dem Schreiben: Ein gleichnamiger Schlüssel unter defaults: ließe einen
        fehlgeschlagenen Schreibvorgang als Erfolg durchgehen. Dieselbe Prüfung nutzt codex.nur_manuell."""
        self.assertTrue(konfig.nur_manuell_gesetzt("policy:\n  allow_implicit_invocation: false\n"))
        self.assertTrue(konfig.nur_manuell_gesetzt("policy:\n  x: 1\n# Kommentar\n  allow_implicit_invocation: false\n"))
        self.assertFalse(konfig.nur_manuell_gesetzt("defaults:\n  allow_implicit_invocation: false\n"))
        self.assertFalse(konfig.nur_manuell_gesetzt(
            "policy:\n  allow_implicit_invocation: true\ndefaults:\n  allow_implicit_invocation: false\n"))
        # Ein Kommentar hinter dem Wert oder hinter policy: ändert nichts
        self.assertTrue(konfig.nur_manuell_gesetzt("policy:\n  allow_implicit_invocation: false  # nur per $name\n"))
        self.assertTrue(konfig.nur_manuell_gesetzt("policy:  # Codex\n  allow_implicit_invocation: false\n"))


class ConfigToml(Ordner):
    """~/.codex/config.toml: Wert setzen, Skill an und aus, vertrauten Ordner entfernen."""

    def test_deaktivieren_schreibt_config_und_sichert(self):
        self.ok(DOCUMENTS_AUS)
        self.assertIn("enabled = false", self.config.read_text())
        self.assertEqual(self.sicherung(self.config).read_text(), CONFIG)

    def test_schreiben_erhaelt_dateirechte_der_config(self):
        self.config.chmod(0o600)
        self.ok(DOCUMENTS_AUS)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.sicherung(self.config).stat().st_mode & 0o777, 0o600)

    def test_kaputte_config_bleibt_unangetastet(self):
        self.config.write_text("[kaputt\n")
        self.scheitert({"toml": {"tabelle": 'plugins."x@y"', "schluessel": "enabled", "wert": "false"}})
        self.assertEqual(self.config.read_text(), "[kaputt\n")

    def test_deaktivieren_ohne_config_toml_meldet_lesbaren_fehler(self):
        self.config.unlink()
        self.assertEqual(self.k.ausfuehren("documents", DOCUMENTS_AUS), {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"})

    def test_config_mit_bom(self):
        """Mit UTF-8-BOM scheiterte tomllib schon am ersten Zeichen: Jede Codex-Aktion schlug fehl. Die BOM bleibt beim
        Zurückschreiben stehen, der Server ändert nur den einen Wert."""
        self.config.write_bytes(b"\xef\xbb\xbf" + CONFIG.encode())
        self.ok(DOCUMENTS_AUS)
        self.assertEqual(self.config.read_bytes(), b"\xef\xbb\xbf" + CONFIG.replace("enabled = true", "enabled = false").encode())

    def test_toml_in_anderer_form_meldet_bitte_von_hand(self):
        """Als Inline-Tabelle oder mit gepunkteten Schlüsseln lässt sich die Tabelle nicht per Kopf ergänzen. Die Rücknahme
        griff, aber die Meldung („Cannot declare …“) sagte nicht, was zu tun ist."""
        for tabelle, inhalt in (("mcp_servers.foo", '[mcp_servers]\nfoo = { command = "x" }\n'),
                                ("mcp_servers.foo", 'mcp_servers.foo.command = "x"\n'),
                                ('plugins."a@b"', '[plugins]\n"a@b" = { enabled = true }\n')):
            with self.subTest(inhalt=inhalt):
                self.config.write_text(inhalt)
                antwort = self.scheitert({"toml": {"tabelle": tabelle, "schluessel": "enabled", "wert": "false"}})
                self.assertIn("von Hand", antwort["ausgabe"])
                self.assertEqual(self.config.read_text(), inhalt)

    def test_toml_tabelle_entfernen_nimmt_nur_diesen_ordner(self):
        self.config.write_text(CONFIG + '\n[projects."/weg/ordner"]\ntrust_level = "trusted"\n\n[projects."/da"]\ntrust_level = "trusted"\n')
        self.ok({"toml_tabelle_entfernen": ["projects", "/weg/ordner"]})
        self.assertEqual(self.config.read_text(), CONFIG + '\n[projects."/da"]\ntrust_level = "trusted"\n')
        self.scheitert({"toml_tabelle_entfernen": ["projects", "/weg/ordner"]})  # gibt es nicht mehr

    def test_toml_tabelle_entfernen_laesst_andere_schreibweisen_stehen(self):
        """Als Inline-Tabelle hat der Ordner keinen eigenen Kopf: nichts schreiben, „bitte von Hand“ melden."""
        text = 'projects = { "/weg/ordner" = { trust_level = "trusted" } }\n'
        self.config.write_text(text)
        self.assertIn("von Hand", self.scheitert({"toml_tabelle_entfernen": ["projects", "/weg/ordner"]})["ausgabe"])
        self.assertEqual(self.config.read_text(), text)

    def test_toml_tabelle_entfernen_nur_vertraute_ordner(self):
        """Der Pfad kommt aus dem Inventar: Eine andere Tabelle als [projects."<Pfad>"] nimmt der Schritt nicht heraus."""
        for pfad in (["mcp_servers", "node_repl"], ["projects"], ["projects", "/a", "b"], "projects", ["projects", 3]):
            with self.subTest(pfad=pfad):
                self.scheitert({"toml_tabelle_entfernen": pfad})
        self.assertEqual(self.config.read_text(), CONFIG)

    def skill_md(self, name="grilling"):
        return self.skill(name) / "SKILL.md"

    def test_skill_abschalten_haengt_block_an(self):
        skill = self.skill_md()
        for _ in range(2):  # zweimal ausführen darf keinen zweiten Block schreiben
            self.ok({"skill_aus": str(skill)})
        self.assertEqual(tomllib.loads(self.config.read_text())["skills"]["config"], [{"path": str(skill), "enabled": False}])
        self.assertIn("# eigene Einstellungen", self.config.read_text())

    def test_skill_einschalten_nimmt_den_block_zurueck(self):
        """Hin und zurück: deaktivieren hängt den Block an, einschalten stellt ihn auf true. Andere Blöcke, auch ein
        von Hand geschriebener mit anderer Schreibweise, bleiben Zeichen für Zeichen stehen."""
        skill, anderer = self.skill_md(), self.skill_md("pdf")
        von_hand = f"\n[[skills.config]]  # von Hand\nenabled = false\npath = '{anderer}'\n"
        self.config.write_text(self.config.read_text() + von_hand)
        self.ok({"skill_aus": str(skill)})
        self.ok({"skill_an": str(skill)})
        cfg = tomllib.loads(self.config.read_text())
        self.assertEqual(cfg["skills"]["config"], [{"enabled": False, "path": str(anderer)}, {"path": str(skill), "enabled": True}])
        self.assertIn(von_hand, self.config.read_text())
        self.scheitert({"skill_an": str(skill)})  # kein Block schaltet ihn mehr ab

    def test_deaktivieren_nach_einschalten_haengt_keinen_zweiten_block_an(self):
        """Einschalten lässt den Block mit enabled = true stehen. Deaktivieren suchte danach nur einen Block mit
        enabled = false und hängte jedes Mal einen neuen an; die Datei wuchs mit jedem Hin und Her."""
        skill = self.skill_md()
        for schritt in ("skill_aus", "skill_an", "skill_aus", "skill_an", "skill_aus"):
            self.ok({schritt: str(skill)})
        self.assertEqual(tomllib.loads(self.config.read_text())["skills"]["config"], [{"path": str(skill), "enabled": False}])
        self.assertEqual(self.config.read_text().count("[[skills.config]]"), 1)

    def test_skills_config_als_array_meldet_bitte_von_hand(self):
        """[skills] config = [{…}] liest sich wie [[skills.config]]-Blöcke, lässt sich aber nicht per Kopf ergänzen:
        Deaktivieren scheiterte mit „Cannot mutate …“, Einschalten fand keinen Block und meldete, die Einstellung stehe
        nach dem Schreiben nicht in der Datei."""
        skill = self.skill_md()
        for schritt, inhalt in (("skill_aus", f'[skills]\nconfig = [{{ path = "{skill}", enabled = true }}]\n'),
                                ("skill_an", f'skills.config = [{{ path = "{skill}", enabled = false }}]\n')):
            with self.subTest(schritt=schritt):
                self.config.write_text(inhalt)
                self.assertIn("von Hand", self.scheitert({schritt: str(skill)})["ausgabe"])
                self.assertEqual(self.config.read_text(), inhalt)

    def test_crlf_bleibt_in_config(self):
        """read_text() und splitlines() machten aus CRLF stillschweigend LF."""
        vorher = CONFIG.replace("\n", "\r\n").encode()
        self.config.write_bytes(vorher)
        skill = self.skill_md()
        self.ok({"skill_aus": str(skill)})
        self.ok({"skill_an": str(skill)})
        self.assertEqual(self.config.read_bytes(), vorher + f'\r\n[[skills.config]]\r\npath = "{skill}"\r\nenabled = true\r\n'.encode())


class OpenaiYaml(Ordner):
    """agents/openai.yaml im Skill-Ordner: nur manuell und wieder automatisch."""

    def test_nur_manuell_schreibt_openai_yaml(self):
        ordner = self.skill("teach", "interface:\n  display_name: Teach\npolicy:\n  allow_implicit_invocation: true\n")
        self.ok({"nur_manuell": str(ordner)})
        text = (ordner / "agents/openai.yaml").read_text()
        self.assertIn("allow_implicit_invocation: false", text)
        self.assertIn("display_name: Teach", text)
        self.assertEqual(self.sicherung(ordner / "agents/openai.yaml").read_text().count("true"), 1)

    def test_ohne_datei_legt_es_eine_neue_an_mit_0600(self):
        ordner = self.skill("neu")
        self.ok({"nur_manuell": str(ordner)})
        self.assertIn("allow_implicit_invocation: false", (ordner / "agents/openai.yaml").read_text())
        self.assertEqual((ordner / "agents/openai.yaml").stat().st_mode & 0o777, 0o600)

    def test_nur_manuell_ohne_skill_md_meldet_fehler_ohne_datei_anzulegen(self):
        ordner = self.tmp / "skills/leer"
        ordner.mkdir(parents=True)
        self.assertIn("SKILL.md", self.scheitert({"nur_manuell": str(ordner)})["ausgabe"])
        self.assertFalse((ordner / "agents").exists())

    def test_wieder_automatisch_stellt_die_policy_zurueck(self):
        ordner = self.skill("teach", "interface:\n  display_name: Teach\npolicy:\n    allow_implicit_invocation: false  \n")
        yaml = ordner / "agents/openai.yaml"
        self.ok({"automatisch": str(ordner)})
        self.assertEqual(yaml.read_text(), "interface:\n  display_name: Teach\npolicy:\n    allow_implicit_invocation: true\n")
        self.assertIn("false", self.sicherung(yaml).read_text())
        self.scheitert({"automatisch": str(ordner)})  # steht nicht mehr auf nur manuell

    def test_nur_manuell_mit_kommentar_hinter_policy(self):
        """`policy:  # Kommentar` ist ein Block wie `policy:`; der Server hielt den Kommentar für einen Wert und lehnte ab."""
        ordner = self.skill("kommentiert", "policy:  # Codex\n  allow_implicit_invocation: true\n")
        self.ok({"nur_manuell": str(ordner)})
        self.assertEqual((ordner / "agents/openai.yaml").read_text(), "policy:  # Codex\n  allow_implicit_invocation: false\n")
        self.ok({"automatisch": str(ordner)})

    def test_nur_manuell_mit_bom_haengt_keinen_zweiten_block_an(self):
        """Mit BOM vor `policy:` fand der Server den Block nicht, hängte einen zweiten an und meldete trotzdem Erfolg."""
        ordner = self.skill("bom", "\ufeffpolicy:\n  allow_implicit_invocation: true\n")
        self.ok({"nur_manuell": str(ordner)})
        self.assertEqual((ordner / "agents/openai.yaml").read_text(encoding="utf-8"), "\ufeffpolicy:\n  allow_implicit_invocation: false\n")

    def test_crlf_bleibt_in_openai_yaml(self):
        ordner = self.skill("teach", b"interface:\r\n  display_name: Teach\r\npolicy:\r\n  allow_implicit_invocation: true\r\n")
        self.ok({"nur_manuell": str(ordner)})
        self.assertEqual((ordner / "agents/openai.yaml").read_bytes(),
                         b"interface:\r\n  display_name: Teach\r\npolicy:\r\n  allow_implicit_invocation: false\r\n")

    def test_nur_manuell_uebernimmt_einrueckung(self):
        ordner = self.skill("vier", "interface:\n    display_name: Vier\npolicy:\n    allow_implicit_invocation: true\n    andere_einstellung: 1\n")
        self.ok({"nur_manuell": str(ordner)})
        self.assertEqual((ordner / "agents/openai.yaml").read_text(),
                         "interface:\n    display_name: Vier\npolicy:\n    allow_implicit_invocation: false\n    andere_einstellung: 1\n")

    def test_nur_manuell_aendert_nur_policy_block(self):
        ordner = self.skill("mix", "defaults:\n  allow_implicit_invocation: true\npolicy:\n  other: 1\n")
        self.ok({"nur_manuell": str(ordner)})
        self.assertEqual((ordner / "agents/openai.yaml").read_text(),
                         "defaults:\n  allow_implicit_invocation: true\npolicy:\n  allow_implicit_invocation: false\n  other: 1\n")

    def test_nur_manuell_kommentar_auf_spalte_0_beendet_block_nicht(self):
        """Die bestehende allow_implicit_invocation-Zeile steht nach einem Kommentar auf Spalte 0: ohne den Fix
        gilt der Block davor als beendet, und der Server fügt eine zweite (ungültige) Zeile davor ein."""
        ordner = self.skill("kommentar", "policy:\n  other: 1\n# Kommentar\n  allow_implicit_invocation: true\n")
        self.ok({"nur_manuell": str(ordner)})
        self.assertEqual((ordner / "agents/openai.yaml").read_text(),
                         "policy:\n  other: 1\n# Kommentar\n  allow_implicit_invocation: false\n")

    def test_andere_schreibweise_von_policy_wird_nicht_angefasst(self):
        for name, inhalt, meldung in (("inline", "policy: {allow_implicit_invocation: true}\n", "Inline-Notation"),
                                      ("skalar", "policy: ~\n", "einen Wert")):
            with self.subTest(name=name):
                ordner = self.skill(name, inhalt)
                self.assertIn(meldung, self.scheitert({"nur_manuell": str(ordner)})["ausgabe"])
                self.assertEqual((ordner / "agents/openai.yaml").read_text(), inhalt)
                self.assertEqual(list((ordner / "agents").glob("openai.yaml.bak*")), [])

    def test_nur_manuell_rollback_bei_fehlgeschlagener_pruefung(self):
        inhalt = "policy:\n  allow_implicit_invocation: true\n"
        ordner = self.skill("rollback", inhalt)
        with mock.patch("konfig.re.search", return_value=None):
            self.scheitert({"nur_manuell": str(ordner)})
        self.assertEqual((ordner / "agents/openai.yaml").read_text(), inhalt)

    def test_neu_angelegte_datei_verschwindet_wenn_die_pruefung_scheitert(self):
        """Gab es die Datei vorher nicht, bleibt auch keine zurück."""
        ordner = self.skill("neu")
        with mock.patch("konfig.re.search", return_value=None):
            self.scheitert({"nur_manuell": str(ordner)})
        self.assertFalse((ordner / "agents/openai.yaml").exists())


if __name__ == "__main__":
    unittest.main()
