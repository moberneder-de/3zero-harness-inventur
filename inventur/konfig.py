"""Ändert die Konfigurationsdateien, die das Dashboard schalten kann.

Vier Arten von Dateien: ~/.claude/settings.json, .claude/settings*.json eines Projekts, ~/.codex/config.toml und
agents/openai.yaml eines Skills. Es gibt einen Eingang, `Konfig.ausfuehren(name, schritt)`, mit dem Schritt, wie ihn
aktionen() ins Inventar schreibt. Jede Änderung nimmt denselben Weg (`Konfig.ersetzen`): sichern, atomar schreiben,
nachlesen und zurücknehmen, wenn sie nicht angekommen ist.

Der Schritt kommt aus dem Inventar, einer Datei, die auch andere schreiben können. Welche Datei und welche Stelle er
nennen darf, prüft deshalb dieses Modul selbst, nicht sein Aufrufer.
"""

import json
import re
import shutil
import time
import tomllib
from pathlib import Path

from . import harness

SICHERUNGEN = 10  # so viele <datei>.bak-<Zeitstempel> bleiben je Datei
BOM = "\ufeff"


def toml_pfad(tabelle):
    """Zerlegt einen Tabellenkopf wie plugins."a@b".mcp_servers.c in seine Schlüssel."""
    return [t.strip('"') for t in re.findall(r'"[^"]*"|[^.]+', tabelle)]


def text_lesen(datei):
    """Liest ohne Übersetzung der Zeilenenden: read_text() machte aus CRLF stillschweigend LF. Eine BOM bleibt als
    erstes Zeichen stehen, damit die Rücknahme die Datei genau so wiederherstellt."""
    return Path(datei).read_bytes().decode("utf-8")


def zeilen_trennen(text):
    """(Zeilen, Zeilenende): CRLF, wenn die Datei so geschrieben ist. Zusammensetzen mit nl.join(zeilen) + nl."""
    nl = "\r\n" if "\r\n" in text else "\n"
    return text.removesuffix(nl).split(nl), nl


def toml_kopf(zeile):
    """(Schlüssel, Array-Tabelle?) eines Tabellenkopfs wie [ a."b" ] oder [[a.b]], sonst None. Über die Schlüssel
    verglichen findet sich der Kopf auch mit Leerzeichen, Anführungszeichen oder Kommentar, wie ein Mensch ihn schreibt."""
    if not zeile.lstrip().startswith("["):
        return None
    try:
        daten = tomllib.loads(zeile)
    except ValueError:
        return None
    pfad = []
    while isinstance(daten, dict) and daten:
        (schluessel, daten), = daten.items()
        pfad.append(schluessel)
    return pfad, isinstance(daten, list)


# Strings und Kommentare in TOML, von links gelesen; nur so lässt sich sagen, ob eine Zeile in einem mehrzeiligen String liegt
TOML_STRINGS = re.compile(r'"""(?:[^\\]|\\.)*?"""(?!")|\'\'\'.*?\'\'\'(?!\')|"(?:[^"\\\n]|\\.)*"|\'[^\'\n]*\'|#[^\n]*', re.S)


def toml_zeilen(text):
    """(Zeilen, Zeilenende, {Zeilennummer: Kopf}, Zeilen in mehrzeiligen Strings). Eine Zeile in einem mehrzeiligen
    String ist kein Kopf und keine Zuweisung, auch wenn sie mit [ oder `enabled =` beginnt."""
    zeilen, nl = zeilen_trennen(text)
    im_string = set()
    for m in TOML_STRINGS.finditer(text):
        if m.group().startswith(('"""', "'''")):
            erste = text.count(nl, 0, m.start()) + 1
            im_string.update(range(erste, erste + m.group().count(nl)))
    koepfe = {i: k for i, z in enumerate(zeilen) if i not in im_string and (k := toml_kopf(z))}
    return zeilen, nl, koepfe, im_string


def toml_text_setzen(text, tabelle, schluessel, wert):
    """Setzt `schluessel = wert` in [tabelle]: vorhandene Zeile ersetzen, sonst in die Tabelle einfügen,
    sonst die Tabelle anhängen. Alles andere bleibt Zeichen für Zeichen stehen."""
    zeilen, nl, koepfe, im_string = toml_zeilen(text)
    ziel = toml_kopf(f"[{tabelle}]")
    start = next((i for i, kopf in koepfe.items() if kopf == ziel), None)
    if start is None:
        rumpf = text if text.endswith(nl) or not text else text + nl
        return f"{rumpf}{nl}[{tabelle}]{nl}{schluessel} = {wert}{nl}"
    ende = next((i for i in koepfe if i > start), len(zeilen))
    muster = re.compile(rf"^\s*{re.escape(schluessel)}\s*=")
    for i in range(start + 1, ende):
        if i not in im_string and muster.match(zeilen[i]):
            zeilen[i] = f"{schluessel} = {wert}"
            break
    else:
        zeilen.insert(start + 1, f"{schluessel} = {wert}")
    return nl.join(zeilen) + nl


def toml_tabelle_entfernen_text(text, pfad):
    """Nimmt die Tabelle mit diesem Kopf samt ihren Zeilen heraus; liefert (neuer Text, ob es sie gab). Kommentare
    direkt vor dem nächsten Kopf gehören zu dem und bleiben stehen."""
    zeilen, nl, koepfe, im_string = toml_zeilen(text)
    start = next((i for i, kopf in koepfe.items() if kopf == (pfad, False)), None)
    if start is None:
        return text, False
    ende = next((i for i in koepfe if i > start), len(zeilen))
    letzte = ende == len(zeilen)
    while ende - 1 > start and ende - 1 not in im_string and zeilen[ende - 1].lstrip().startswith("#"):
        ende -= 1
    del zeilen[start:ende]
    while letzte and zeilen and not zeilen[-1].strip():  # die Leerzeile vor einer letzten Tabelle bliebe sonst am Ende stehen
        zeilen.pop()
    return (nl.join(zeilen) + nl if zeilen else ""), True


def skills_config_setzen(text, ziel, wert):
    """Setzt `enabled = wert` in jedem [[skills.config]]-Block, dessen path auf `ziel` zeigt; fehlt die Zeile, kommt sie
    unter den Kopf. Liefert (neuer Text, ob es einen solchen Block gab)."""
    zeilen, nl, koepfe, im_string = toml_zeilen(text)
    grenzen = [*koepfe, len(zeilen)]
    gefunden = False
    for start, ende in reversed(list(zip(grenzen, grenzen[1:]))):  # von hinten: eingefügte Zeilen verschieben nichts davor
        if koepfe[start] != (["skills", "config"], True):
            continue
        try:
            block = tomllib.loads(nl.join(zeilen[start + 1:ende]))
        except ValueError:
            continue
        if not (isinstance(block.get("path"), str) and Path(block["path"]).expanduser().resolve() == ziel):
            continue
        gefunden = True
        treffer = [i for i in range(start + 1, ende) if i not in im_string and re.match(r"\s*enabled\s*=", zeilen[i])]
        for i in treffer:
            zeilen[i] = f"enabled = {wert}"
        if not treffer and wert == "false":  # ohne enabled-Zeile ist der Block schon an
            zeilen.insert(start + 1, f"enabled = {wert}")
    return nl.join(zeilen) + nl, gefunden


def skill_eintraege(daten, ziel):
    """Die Einträge in skills.config, deren path auf `ziel` zeigt. Codex vergleicht Pfade aufgelöst, also ohne Symlinks."""
    return [e for e in (daten.get("skills") or {}).get("config") or []
            if isinstance(e, dict) and isinstance(e.get("path"), str) and Path(e["path"]).expanduser().resolve() == ziel]


def nur_manuell_gesetzt(text):
    """Steht `allow_implicit_invocation: false` im policy-Block? Nur dort zählt es. Ein gleichnamiger Schlüssel
    unter defaults: oder interface: ließe sonst einen fehlgeschlagenen Schreibvorgang als Erfolg durchgehen.
    codex.nur_manuell nutzt dieselbe Prüfung beim Einlesen."""
    text = text.removeprefix("\ufeff")  # eine BOM vor `policy:` in der ersten Zeile verdeckte den Block
    # Ein Kommentar oder eine Leerzeile auf Spalte 0 beendet den Block nicht, eine andere Zeile schon
    block = re.search(r"^policy:[^\S\n]*(#.*)?\n((?:(?:[ \t]+.*|[ \t]*#.*|[ \t]*)\n?)*)", text, re.M)
    return bool(block) and re.search(r"^\s+allow_implicit_invocation:\s*false\s*(#.*)?$", block.group(2), re.M) is not None


class Konfig:
    def __init__(self, settings, codex_config):
        self.settings, self.codex_config = Path(settings), Path(codex_config)
        self.gesichert = set()

    def ausfuehren(self, name, schritt):
        """Führt einen Schritt aus dem Inventar aus und liefert {"ok", "ausgabe"}. None heißt: Dieser Schritt ändert
        keine Konfigurationsdatei (Papierkorb, Befehl). `name` ist der Name, unter dem der Harness den Eintrag führt."""
        if "regel_entfernen" in schritt:
            return self.regel_entfernen(**schritt["regel_entfernen"])
        if "toml_tabelle_entfernen" in schritt:
            return self.toml_tabelle_entfernen(schritt["toml_tabelle_entfernen"])
        if "override" in schritt:
            return self.override_setzen(name, schritt["override"])
        if "sperren" in schritt:
            return self.mcp_sperren(schritt["sperren"])
        if "entsperren" in schritt:
            return self.settings_eintrag_entfernen("deniedMcpServers", schritt["entsperren"])
        if "toml" in schritt:
            return self.toml_setzen(**schritt["toml"])
        if "skill_aus" in schritt:
            return self.skill_abschalten(schritt["skill_aus"])
        if "skill_an" in schritt:
            return self.skill_einschalten(schritt["skill_an"])
        if "nur_manuell" in schritt:
            return self.nur_manuell_datei(schritt["nur_manuell"])
        if "automatisch" in schritt:
            return self.nur_manuell_datei(schritt["automatisch"], manuell=False)
        if "hook_entfernen" in schritt:
            return self.hook_entfernen(schritt["hook_entfernen"])
        if "ausschliessen" in schritt:
            return self.ausschliessen(schritt["ausschliessen"])
        if "einschliessen" in schritt:
            return self.settings_eintrag_entfernen("claudeMdExcludes", schritt["einschliessen"])
        if "output_style" in schritt:
            return self.output_style(schritt["output_style"])
        return None

    def sichern(self, datei):
        """Einmal je Datei und Serverlauf eine Sicherung <datei>.bak-<Zeitstempel>, bevor der Server sie zum ersten Mal
        ändert. Mit festem Namen überschrieb der nächste Lauf sie mit dem Stand, den der Server selbst schon geändert hatte."""
        if datei in self.gesichert:
            return
        shutil.copy2(datei, datei.with_name(f"{datei.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}"))  # übernimmt Rechte und Zeitstempel
        self.gesichert.add(datei)
        # nur die eigenen, am Zeitstempel erkannt; der Name sortiert sie zugleich nach der Zeit
        eigene = sorted(d for d in datei.parent.glob(f"{datei.name}.bak-*") if re.fullmatch(r"\d{8}-\d{6}", d.name.rsplit(".bak-", 1)[1]))
        for alt in eigene[:-SICHERUNGEN]:
            alt.unlink()

    def ersetzen(self, datei, alt, neu, wirkt):
        """Der eine Schreibweg für alle vier Dateiarten. `alt` ist der bisherige Text, None, wenn es die Datei noch
        nicht gibt. `wirkt(text)` sagt am nachgelesenen Text, ob die Änderung angekommen ist; sonst steht danach wieder
        der alte Stand da. Liefert None oder den Grund.
        Geschrieben wird atomar über eine eigene Zwischendatei (0600, die echte config.toml kann Zugangsdaten enthalten),
        einem Symlink folgend (Dotfiles-Repo) und mit den Rechten der Datei; siehe harness.schreiben."""
        if alt is None:
            datei.parent.mkdir(parents=True, exist_ok=True)
        else:
            self.sichern(datei)
        harness.schreiben(datei, neu)
        try:
            if not wirkt(text_lesen(datei)):
                raise ValueError("Die Einstellung steht nach dem Schreiben nicht in der Datei.")
        except Exception as fehler:  # jeder Fehler nach dem Schreiben muss zurückrollen, nicht nur ValueError/OSError
            if alt is None:
                datei.unlink()
            else:
                harness.schreiben(datei, alt)
            return str(fehler)
        return None

    def json_aendern(self, datei, aendern):
        """Ändert eine Einstellungsdatei von Claude Code über `aendern(daten)`; liefert None oder die Fehlantwort."""
        alt = text_lesen(datei)
        daten = json.loads(alt)
        aendern(daten)
        fehler = self.ersetzen(datei, alt, json.dumps(daten, indent=2, ensure_ascii=False) + "\n", lambda text: json.loads(text) == daten)
        return fehler and {"ok": False, "ausgabe": f"{datei} nicht geändert: {fehler}"}

    def config_lesen(self):
        """config.toml ohne BOM, mit den Zeilenenden der Datei."""
        return text_lesen(self.codex_config).removeprefix(BOM)

    def von_hand(self, was, fehler=""):
        return {"ok": False, "ausgabe": f"{was} steht in {self.codex_config} in einer Form, die der Server nicht ändert "
                                        f"(Inline-Tabelle, gepunktete Schlüssel oder Array statt eigenem Kopf). Bitte von Hand ändern. {fehler}".strip()}

    def config_aendern(self, neuer_text, pruefen, was):
        """Schreibt config.toml und nimmt die Änderung zurück, wenn sie die Datei zerstört oder nicht wirkt.
        `was` nennt die Stelle, wenn sich die Datei so nicht ändern lässt."""
        alt = text_lesen(self.codex_config)
        try:
            tomllib.loads(alt.removeprefix(BOM))
        except ValueError as fehler:
            return {"ok": False, "ausgabe": f"{self.codex_config} ist kein gültiges TOML, nicht geändert: {fehler}"}
        try:
            tomllib.loads(neuer_text)
        except ValueError as fehler:
            # Steht die Stelle schon in anderer Form da, kollidiert ein neuer Kopf damit („Cannot declare …“)
            return self.von_hand(was, f"({fehler})")
        # Eine BOM bleibt stehen: Der Server ändert nur den einen Wert, nicht die Kodierung der Datei
        fehler = self.ersetzen(self.codex_config, alt, (BOM if alt.startswith(BOM) else "") + neuer_text,
                               lambda text: pruefen(tomllib.loads(text.removeprefix(BOM))))
        return fehler and {"ok": False, "ausgabe": f"{self.codex_config} nicht geändert: {fehler}"}

    def toml_setzen(self, tabelle, schluessel, wert):
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        alt = self.config_lesen()
        pfad = toml_pfad(tabelle) + [schluessel]

        def pruefen(daten):
            # wert ist ein TOML-Literal; der Vergleich über json.dumps setzt voraus, dass seine JSON- und
            # TOML-Schreibweise übereinstimmen. Gilt für "false" (heute das Einzige, was der Server erzeugt).
            for teil in pfad[:-1]:
                daten = daten.get(teil) if isinstance(daten, dict) else None
                if daten is None:
                    return False
            return json.dumps(daten.get(pfad[-1])) == wert

        fehler = self.config_aendern(toml_text_setzen(alt, tabelle, schluessel, wert), pruefen, f"[{tabelle}]")
        return fehler or {"ok": True, "ausgabe": f"~/.codex/config.toml → [{tabelle}] {schluessel} = {wert}"}

    def skill_abschalten(self, pfad):
        """Stellt vorhandene [[skills.config]]-Blöcke dieses Pfads auf false und hängt nur ohne einen neuen an. Nur
        anhängen hieß: Nach „einschalten“ (der Block bleibt mit true stehen) kam mit jedem „deaktivieren“ einer dazu."""
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        ziel = Path(pfad).expanduser().resolve()
        alt = self.config_lesen()

        def pruefen(daten):
            eintraege = skill_eintraege(daten, ziel)
            return bool(eintraege) and all(e.get("enabled", True) is False for e in eintraege)
        if pruefen(tomllib.loads(alt)):
            return {"ok": True, "ausgabe": f"steht schon in ~/.codex/config.toml: [[skills.config]] path = {json.dumps(pfad)}"}
        neu, gefunden = skills_config_setzen(alt, ziel, "false")
        if not gefunden:
            _, nl = zeilen_trennen(alt)
            rumpf = alt if alt.endswith(nl) or not alt else alt + nl
            neu = f"{rumpf}{nl}[[skills.config]]{nl}path = {json.dumps(pfad, ensure_ascii=False)}{nl}enabled = false{nl}"
        fehler = self.config_aendern(neu, pruefen, "skills.config")
        return fehler or {"ok": True, "ausgabe": f"~/.codex/config.toml → [[skills.config]] path = {json.dumps(pfad)}, enabled = false"}

    def skill_einschalten(self, pfad):
        """Die Gegen-Aktion zu skill_abschalten(): stellt `enabled` in jedem [[skills.config]]-Block dieses Pfads auf true.
        Der Block bleibt stehen, damit die Datei sonst Zeichen für Zeichen gleich bleibt."""
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        ziel = Path(pfad).expanduser().resolve()
        schaltet_ab = lambda daten: any(e.get("enabled", True) is False for e in skill_eintraege(daten, ziel))
        alt = self.config_lesen()
        if not schaltet_ab(tomllib.loads(alt)):
            return {"ok": False, "ausgabe": f"Kein [[skills.config]]-Block schaltet {pfad} ab"}
        neu, gefunden = skills_config_setzen(alt, ziel, "true")
        if not gefunden:  # der Eintrag steht in einem Array skills.config = [...], nicht als eigener Block
            return self.von_hand("skills.config")
        fehler = self.config_aendern(neu, lambda daten: not schaltet_ab(daten), "skills.config")
        return fehler or {"ok": True, "ausgabe": f"~/.codex/config.toml → [[skills.config]] path = {json.dumps(str(pfad))}, enabled = true"}

    def nur_manuell_datei(self, ordner, manuell=True):
        """agents/openai.yaml im Skill-Ordner: policy.allow_implicit_invocation = false, damit Codex den Skill
        nur noch per $name lädt. Ändert nur innerhalb des policy:-Blocks (Einrückung 0 bis zur nächsten nicht
        leeren Zeile mit Einrückung 0) und übernimmt dessen Einrückung; alles andere bleibt erhalten.
        `manuell=False` ist die Gegen-Aktion „wieder automatisch“ und stellt denselben Schlüssel auf true."""
        if not any((Path(ordner) / f).is_file() for f in ("SKILL.md", "agents/openai.yaml")):
            return {"ok": False, "ausgabe": f"{ordner} enthält weder SKILL.md noch agents/openai.yaml"}
        datei = Path(ordner) / "agents" / "openai.yaml"
        alt = text_lesen(datei) if datei.is_file() else None
        wert = "false" if manuell else "true"
        if not manuell and not (alt is not None and nur_manuell_gesetzt(alt)):
            return {"ok": False, "ausgabe": f"{datei} stellt den Skill nicht auf nur manuell"}
        if alt is not None:
            bom = BOM if alt.startswith(BOM) else ""  # sonst stünde sie vor `policy:`, und der Block fehlte
            zeilen, nl = zeilen_trennen(alt.removeprefix(bom))
            start = next((i for i, z in enumerate(zeilen) if re.match(r"^policy:\s*", z)), None)
            if start is not None and not re.fullmatch(r"policy:\s*(#.*)?", zeilen[start]):
                art = "eine Inline-Notation" if "{" in zeilen[start] else "einen Wert"
                return {"ok": False, "ausgabe": f"{datei} nutzt {art} für policy, bitte von Hand ändern"}
            if start is None:
                zeilen += ["policy:", "  allow_implicit_invocation: false"]
            else:
                # Leerzeilen und Kommentare auf Spalte 0 beenden den Block nicht, nur eine echte neue Zeile auf Spalte 0
                ende = next((i for i in range(start + 1, len(zeilen))
                             if zeilen[i].strip() and not zeilen[i].lstrip().startswith("#")
                             and len(zeilen[i]) == len(zeilen[i].lstrip())), len(zeilen))
                muster = re.compile(r"^\s+allow_implicit_invocation\s*:")
                for i in range(start + 1, ende):
                    if muster.match(zeilen[i]):
                        einrueckung = zeilen[i][:len(zeilen[i]) - len(zeilen[i].lstrip())]
                        zeilen[i] = f"{einrueckung}allow_implicit_invocation: {wert}"
                        break
                else:
                    folge = next((z for z in zeilen[start + 1:ende] if z.strip()), None)
                    einrueckung = folge[:len(folge) - len(folge.lstrip())] if folge else "  "
                    zeilen.insert(start + 1, f"{einrueckung}allow_implicit_invocation: false")
            text = bom + nl.join(zeilen) + nl
        else:
            text = "policy:\n  allow_implicit_invocation: false\n"
        if self.ersetzen(datei, alt, text, lambda neu: nur_manuell_gesetzt(neu) == manuell):
            return {"ok": False, "ausgabe": f"{datei} nicht geändert"}
        return {"ok": True, "ausgabe": f"{datei} → policy: allow_implicit_invocation: {wert}"}

    def override_setzen(self, name, wert):
        """`wert` None nimmt den Override weg: die Gegen-Aktion zu „deaktivieren“ und „nur manuell“."""
        if wert is None:
            return self.settings_eintrag_entfernen("skillOverrides", name)

        def aendern(daten):
            daten.setdefault("skillOverrides", {})[name] = wert
        return self.json_aendern(self.settings, aendern) or {"ok": True, "ausgabe": f'skillOverrides: "{name}": "{wert}"'}

    def mcp_sperren(self, eintrag):
        def aendern(daten):
            liste = daten.setdefault("deniedMcpServers", [])
            if eintrag not in liste:
                liste.append(eintrag)
        return self.json_aendern(self.settings, aendern) or {"ok": True, "ausgabe": "deniedMcpServers: " + json.dumps(eintrag, ensure_ascii=False)}

    def settings_eintrag_entfernen(self, feld, eintrag):
        """Nimmt einen Schlüssel aus skillOverrides oder einen Eintrag aus deniedMcpServers oder claudeMdExcludes;
        ein leeres Feld bleibt nicht stehen."""
        vorhanden = json.loads(text_lesen(self.settings)).get(feld)
        if not isinstance(vorhanden, (dict, list)) or eintrag not in vorhanden:
            return {"ok": False, "ausgabe": f"{json.dumps(eintrag, ensure_ascii=False)} steht nicht in {feld}"}  # ohne die Datei anzufassen

        def aendern(daten):
            if isinstance(daten[feld], dict):
                del daten[feld][eintrag]
            else:
                daten[feld].remove(eintrag)
            if not daten[feld]:
                del daten[feld]
        return self.json_aendern(self.settings, aendern) or {"ok": True, "ausgabe": f"{feld}: {json.dumps(eintrag, ensure_ascii=False)} entfernt"}

    def ausschliessen(self, pfad):
        """Trägt eine CLAUDE.md, Regel oder AGENTS.md in `claudeMdExcludes` ein: Claude Code lädt sie dann nicht mehr,
        die Datei selbst bleibt, wie sie ist. Nur ein ganzer Pfad zu einer .md-Datei: Der Wert kommt aus dem Inventar,
        und Claude Code liest den Eintrag als Muster — mit einem * träfe er mehr als die eine Datei."""
        if not (isinstance(pfad, str) and pfad.startswith("/") and pfad.endswith(".md") and not set(pfad) & set("*?[]{}")):
            return {"ok": False, "ausgabe": "Das ist kein Pfad zu einer Anweisungsdatei."}

        def aendern(daten):
            liste = daten.setdefault("claudeMdExcludes", [])
            if pfad not in liste:
                liste.append(pfad)
        return self.json_aendern(self.settings, aendern) or {"ok": True, "ausgabe": f"claudeMdExcludes: {json.dumps(pfad, ensure_ascii=False)}"}

    def output_style(self, name):
        """Wählt einen Output-Style in ~/.claude/settings.json oder nimmt die Wahl weg (`name` None): Dann gilt der Standard."""
        if name is None:
            if "outputStyle" not in json.loads(text_lesen(self.settings)):
                return {"ok": False, "ausgabe": f"outputStyle steht nicht in {self.settings}"}  # ohne die Datei anzufassen
            return self.json_aendern(self.settings, lambda daten: daten.pop("outputStyle")) or {"ok": True, "ausgabe": "outputStyle entfernt"}
        if not isinstance(name, str) or not name:
            return {"ok": False, "ausgabe": "Das ist kein Name eines Output-Styles."}
        return self.json_aendern(self.settings, lambda daten: daten.update(outputStyle=name)) \
            or {"ok": True, "ausgabe": f"outputStyle: {json.dumps(name, ensure_ascii=False)}"}

    def hook_entfernen(self, schluessel):
        """Nimmt den Block eines Ereignisses (oder die Statuszeile) aus settings.json; zurück führt die Sicherung."""
        ort = lambda daten: daten if schluessel == "statusLine" else daten.get("hooks")
        vorhanden = ort(json.loads(text_lesen(self.settings)))
        if not isinstance(vorhanden, dict) or schluessel not in vorhanden:
            return {"ok": False, "ausgabe": f"{schluessel} steht nicht in {self.settings}"}  # ohne die Datei anzufassen

        def aendern(daten):
            del ort(daten)[schluessel]
            if daten.get("hooks") == {}:
                del daten["hooks"]
        return self.json_aendern(self.settings, aendern) or {"ok": True, "ausgabe": f"{self.settings} → {schluessel} entfernt"}

    def regel_entfernen(self, datei, art, regel):
        """Nimmt eine Regel aus `permissions` einer Einstellungsdatei, auch in einem Projektordner; zurück führt die
        Sicherung. Nur Einstellungsdateien von Claude Code: Der Pfad kommt aus dem Inventar, einer Datei, die auch
        andere schreiben können."""
        datei = Path(datei)
        if datei.parent.name != ".claude" or datei.name not in ("settings.json", "settings.local.json"):
            return {"ok": False, "ausgabe": f"{datei} ist keine Einstellungsdatei von Claude Code"}
        daten = json.loads(text_lesen(datei))
        rechte = daten.get("permissions") if isinstance(daten, dict) else None
        regeln = rechte.get(art) if isinstance(rechte, dict) else None
        if not isinstance(regeln, list) or regel not in regeln:
            return {"ok": False, "ausgabe": f"{json.dumps(regel, ensure_ascii=False)} steht nicht in {datei} ({art})"}  # ohne die Datei anzufassen

        def aendern(daten):
            rechte = daten["permissions"]
            rechte[art] = [r for r in rechte[art] if r != regel]
            if not rechte[art]:
                del rechte[art]
            if not rechte:
                del daten["permissions"]
        return self.json_aendern(datei, aendern) or {"ok": True, "ausgabe": f"{datei} → {art}: {json.dumps(regel, ensure_ascii=False)} entfernt"}

    def toml_tabelle_entfernen(self, pfad):
        """Nimmt einen vertrauten Ordner ([projects."<Pfad>"]) aus config.toml."""
        if not (isinstance(pfad, list) and len(pfad) == 2 and pfad[0] == "projects" and isinstance(pfad[1], str)):
            return {"ok": False, "ausgabe": "Diese Tabelle entfernt der Server nicht."}
        if not self.codex_config.is_file():
            return {"ok": False, "ausgabe": "~/.codex/config.toml gibt es nicht"}
        vorhanden = lambda daten: isinstance(daten.get(pfad[0]), dict) and pfad[1] in daten[pfad[0]]
        alt = self.config_lesen()
        was = f'{pfad[0]}."{pfad[1]}"'
        neu, gefunden = toml_tabelle_entfernen_text(alt, pfad)
        if not gefunden:
            return self.von_hand(was) if vorhanden(tomllib.loads(alt)) else {"ok": False, "ausgabe": f"{was} steht nicht in {self.codex_config}"}
        fehler = self.config_aendern(neu, lambda daten: not vorhanden(daten), was)
        return fehler or {"ok": True, "ausgabe": f"{self.codex_config} → [{was}] entfernt"}
