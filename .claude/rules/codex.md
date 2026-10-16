---
paths:
  - "inventur/codex.py"
  - "tests/test_codex.py"
---

# codex.py

Den Codex-Bestand liest `codex.py` aus `~/.codex` und `~/.agents`, in derselben Datenform.
Ein Skill liegt dort in zwei Formaten vor: als `SKILL.md` oder als `agents/openai.yaml` mit einem `interface`-Block (so `openai-docs`); die Nutzung hängt deshalb am Ordner, nicht an der Datei, sonst zählte ein Formatwechsel als neuer Skill.
Aus demselben Grund lässt der Schlüssel bei Plugins Marktplatz und Version weg — beide ändern sich, ohne dass der Skill ein anderer wird.
Umgebungswerte von MCP-Servern übernimmt es nie, die Nutzung zählt es in `~/.codex/sessions`.
Eigene Agenten liest `agenten()` aus `~/.codex/agents/*.toml` und `<Projekt>/.codex/agents/*.toml`, unter `name` aus der Datei (laut Doku zählt das Feld, nicht der Dateiname); ohne Namen lädt Codex die Datei nicht, sie fehlt dann auch hier.
Sie stehen ohne Tokens da: Ob Codex ihre Beschreibung in jede Sitzung lädt, sagt die Doku nicht; einzeln abschalten kennt Codex nicht (nur `agents.enabled` für alle), also bleibt der Papierkorb für die Datei.
`model` und `model_reasoning_effort` aus ihren Dateien liest der Tab „Modelle“ noch nicht.
`plugins[].mcp` nennt im Inventar nur die Namen der Server: `MCP_FELDER` lässt `args` und `url` durch, und die standen bis Oktober 2026 unverdeckt im Dashboard; verdeckt stehen sie im Ziel der Einträge unter `mcp`.
Unter Codex führt der Server dieselben Aktionen aus: `codex plugin remove` (bei Plugins aus dem ChatGPT-Konto nur, wenn der Kontokatalog es erlaubt: `remote_installationsregeln()` liest je Plugin die `installation_policy` aus dem neuesten `~/.codex/cache/remote_plugin_catalog/*.json`, zugeordnet über die `remote_plugin_id` in `.codex-remote-plugin-install.json`; nur `AVAILABLE` heißt, der Benutzer hat es selbst installiert, bei jeder anderen oder unbekannten Regel bietet `entfernen_gesperrt()` weder Aktion noch Befehl an, einzelne Skills lassen sich weiter abschalten), `codex mcp remove`, `npx skills`, dazu `enabled = false` und `[[skills.config]]` in `~/.codex/config.toml` und `agents/openai.yaml` im Skill-Ordner.
`umgesetzt()` steht neben `aktionen()` und sagt, ob die eigene Empfehlung eines Eintrags schon gilt, mit den Merkmalen von Codex: „deaktiviert“ oder „aus“, ein Plugin lädt mit jedem Status aus `LAEDT`, „nur manuell“ heißt „nur $“.
Liegt unter Codex derselbe Projekt-Skill in `.agents/skills` und `.codex/skills`, zählt der erste.
Codex kennt keine Mods, aber Hooks je Projekt: `projekt_hooks()` in `codex.py` liest `[hooks]` aus `<Projekt>/.codex/config.toml` (ohne die Vertrauensliste `state`) und `<Projekt>/.codex/hooks.json`, für die Projekte aus `[projects]`, in dasselbe Feld und mit derselben Anzeige.
Belegt an der Doku und an `codex-rs/hooks/src/engine/discovery.rs`, nicht am laufenden Codex ausprobiert.
Der Status folgt dem Vertrauen in das Projekt („aktiv“ oder „nicht vertraut“), wie bei den Projekt-Servern.
Codex führt einen Hook außerdem erst aus, wenn der Benutzer ihn mit `/hooks` geprüft hat; das hängt an einem Hash je Hook in `hooks.state` und steht nicht im Inventar, auch bei den globalen Hooks nicht: „aktiv“ heißt dort nur „würde laden“.
