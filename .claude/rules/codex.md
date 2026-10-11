---
paths:
  - "codex.py"
  - "test_codex.py"
---

# codex.py

Den Codex-Bestand liest `codex.py` aus `~/.codex` und `~/.agents`, in derselben Datenform.
Ein Skill liegt dort in zwei Formaten vor: als `SKILL.md` oder als `agents/openai.yaml` mit einem `interface`-Block (so `openai-docs`); die Nutzung hängt deshalb am Ordner, nicht an der Datei, sonst zählte ein Formatwechsel als neuer Skill.
Aus demselben Grund lässt der Schlüssel bei Plugins Marktplatz und Version weg — beide ändern sich, ohne dass der Skill ein anderer wird.
Umgebungswerte von MCP-Servern übernimmt es nie, die Nutzung zählt es in `~/.codex/sessions`.
Unter Codex führt der Server dieselben Aktionen aus: `codex plugin remove` (bei Plugins aus dem ChatGPT-Konto nur, wenn der Kontokatalog es erlaubt: `remote_installationsregeln()` liest je Plugin die `installation_policy` aus dem neuesten `~/.codex/cache/remote_plugin_catalog/*.json`, zugeordnet über die `remote_plugin_id` in `.codex-remote-plugin-install.json`; nur `AVAILABLE` heißt, der Benutzer hat es selbst installiert, bei jeder anderen oder unbekannten Regel bietet `entfernen_gesperrt()` weder Aktion noch Befehl an, einzelne Skills lassen sich weiter abschalten), `codex mcp remove`, `npx skills`, dazu `enabled = false` und `[[skills.config]]` in `~/.codex/config.toml` und `agents/openai.yaml` im Skill-Ordner.
