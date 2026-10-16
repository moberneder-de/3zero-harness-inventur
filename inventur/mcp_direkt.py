"""MCP-Server über HTTP direkt fragen: `initialize` liefert den Anweisungsblock, `tools/list` Namen und Schemata der
Werkzeuge. So gibt es Werte auch für Server, die noch keine Sitzung geladen hat, und unter Codex, das beides nicht
protokolliert.

Nur HTTP-Server ohne Zugangsdaten: stdio-Server zu starten hieße fremde Programme auszuführen, und Header, Tokens oder
OAuth gehen nie mit. Was der Server antwortet, ist fremder Text; er wird nur gezählt, nichts davon landet im Inventar.
"""

import hashlib
import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

VERSION = "2025-06-18"
GRENZE = 2_000_000  # mehr liest die Inventur von einer Antwort nicht
SEITEN = 20  # höchstens so viele Seiten tools/list
ZUGANG = ("headers", "headersHelper", "oauth", "bearer_token", "bearer_token_env_var", "http_headers", "env_http_headers")


class KeineUmleitung(urllib.request.HTTPRedirectHandler):
    """Eine Umleitung führte die Anfrage an ein Ziel, das nicht in der Konfiguration steht; urllib folgte auch nach ftp:."""

    def redirect_request(self, *args, **kwargs):
        return None


OEFFNER = urllib.request.build_opener(KeineUmleitung)


def adresse(c):
    """Die URL eines HTTP-Servers, den die Inventur ohne Zugangsdaten fragen darf, sonst None. Claude Code schreibt
    `type: http`, Codex nur `url`; `sse` ist ein anderes Protokoll. `${…}` setzt erst der Harness ein."""
    url = c.get("url")
    if not isinstance(url, str) or c.get("type", "http") != "http" or "${" in url or any(k in c for k in ZUGANG):
        return None
    return url if urlparse(url).scheme in ("http", "https") else None


def antwort(roh, art, nummer):
    """Die JSON-RPC-Antwort mit dieser Nummer, als JSON oder als Event-Stream mit einer oder mehreren Nachrichten."""
    text = roh.decode("utf-8")
    if art != "text/event-stream":
        return json.loads(text)
    for block in text.replace("\r\n", "\n").split("\n\n"):
        daten = "\n".join(z[5:].lstrip(" ") for z in block.split("\n") if z.startswith("data:"))
        if daten:
            n = json.loads(daten)
            if isinstance(n, dict) and n.get("id") == nummer:
                return n
    return None


def senden(url, nachricht, sitzung, version, zeitgrenze, methode="POST"):
    kopf = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "User-Agent": "3zero-harness-inventur"}
    if sitzung:
        kopf["Mcp-Session-Id"] = sitzung
    if version:
        kopf["MCP-Protocol-Version"] = version
    daten = json.dumps(nachricht).encode() if nachricht else None
    anfrage = urllib.request.Request(url, data=daten, headers=kopf, method=methode)
    try:
        r = OEFFNER.open(anfrage, timeout=zeitgrenze)
    except urllib.error.HTTPError as fehler:
        fehler.close()  # trägt die offene Antwort; ungeschlossen warnte Python beim Aufräumen
        raise
    with r:
        roh = r.read(GRENZE)
        neu = r.headers.get("Mcp-Session-Id") or sitzung
        if not nachricht or "id" not in nachricht or not roh:
            return neu, None
        return neu, antwort(roh, r.headers.get_content_type(), nachricht["id"])


def fragen(url, zeitgrenze=5):
    """Anweisungen und Schemata in Tokens (Zeichen ÷ 4, wie bei den Transkripten), die Zahl der Werkzeuge, wie viele davon
    der Server als nur lesend auszeichnet (readOnlyHint, eine Angabe des Servers, keine Prüfung) und die Länge ihrer Namen
    in Zeichen. Verlangt er eine Anmeldung, nur {"anmeldung": …}: OAuth, wenn er auf seinen Anbieter verweist
    (resource_metadata). None, wenn er nicht, mit einem anderen Fehler oder unerwartet antwortet."""
    sitzung = version = None
    try:
        sitzung, init = senden(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": VERSION, "capabilities": {}, "clientInfo": {"name": "3zero-harness-inventur", "version": "1"}}},
            None, None, zeitgrenze)
        ergebnis = init["result"]
        version = ergebnis.get("protocolVersion") or VERSION
        anweisungen = ergebnis.get("instructions") or ""
        senden(url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sitzung, version, zeitgrenze)
        werkzeuge, cursor = [], None
        for nummer in range(2, 2 + SEITEN) if "tools" in (ergebnis.get("capabilities") or {}) else ():
            _, liste = senden(url, {"jsonrpc": "2.0", "id": nummer, "method": "tools/list",
                                    **({"params": {"cursor": cursor}} if cursor else {})}, sitzung, version, zeitgrenze)
            werkzeuge += liste["result"]["tools"]
            cursor = liste["result"].get("nextCursor")
            if not cursor:
                break
        if not isinstance(anweisungen, str) or not all(isinstance(w, dict) and isinstance(w.get("name"), str) for w in werkzeuge):
            return None
        schemata = sum(len(json.dumps({k: w.get(k) for k in ("name", "description", "inputSchema")}, ensure_ascii=False))
                       for w in werkzeuge)
        return {"werkzeuge": len(werkzeuge), "anweisungen": round(len(anweisungen) / 4),
                "namen_zeichen": sum(len(w["name"]) + 1 for w in werkzeuge), "schemata": round(schemata / 4),
                "lesend": sum(1 for w in werkzeuge if isinstance(w.get("annotations"), dict) and w["annotations"].get("readOnlyHint") is True)}
    except urllib.error.HTTPError as fehler:
        if fehler.code != 401 or sitzung:  # nur die Antwort auf initialize sagt etwas über die Anmeldung
            return None
        return {"anmeldung": "OAuth" if "resource_metadata" in (fehler.headers.get("WWW-Authenticate") or "") else "nötig"}
    except Exception:  # Netz, Zeitgrenze, HTTP-Fehler, kein JSON, unerwarteter Aufbau: dann fehlt nur diese Angabe
        return None
    finally:
        if sitzung:
            try:
                senden(url, None, sitzung, version, zeitgrenze, "DELETE")
            except Exception:
                pass


def gemessen(c, speicher):
    """Die Messung eines Servers über den Zwischenspeicher der Werkzeuge: je Server ein Versuch am Tag, ohne Antwort
    gilt der letzte Stand. Der Schlüssel ist ein Hash der URL, weil sie ein Geheimnis in der Query tragen kann."""
    url = adresse(c)
    if not url:
        return None
    heute = speicher.heute
    return speicher.hole("mcp:" + hashlib.sha256(url.encode()).hexdigest()[:16],
                         lambda: (lambda d: d and {**d, "am": heute})(fragen(url)))


def felder(d, praefix):
    """Die Felder fürs Inventar. Kontext je Sitzung sind Anweisungen und Werkzeugnamen, wie sie in der Liste stehen
    (`mcp__<Server>__<Name>`); die Schemata lädt der Harness erst bei Bedarf, sie stehen daneben. Nach einer Antwort,
    die nur eine Anmeldung verlangt, gibt es nichts zu übernehmen: Die Werte aus den Transkripten bleiben."""
    if "werkzeuge" not in d:
        return {}
    namen = round((d["namen_zeichen"] + d["werkzeuge"] * len(f"mcp__{praefix}__")) / 4)
    return {"werkzeuge": d["werkzeuge"], "tokens": d["anweisungen"] + namen, "schemata": d["schemata"],
            "anweisungen": d["anweisungen"], "lesend": d.get("lesend"), "messung": "direkt", "gemessen_am": d["am"]}
