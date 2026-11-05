# Adapters

Adapters are the cockpit's only extension points. Each is a small executable plus one config entry. The core scripts
never import an adapter; they only run it and read its output.

## Intake adapters (`adapters/intake/<name>`)
- **Run by** `scripts/intake_adapters.py` for every entry in `config.json → intake.adapters`
  (`["example-tracker"]` or `[{"name": "hermes", "enabled": false}]`).
- **Must be read-only.** No writes to any shared system, ever.
- **Output:** one JSON object on stdout:

  ```json
  {"items": [{
    "source": "linear",            // adapter / system name
    "id": "ENG-12",                // stable id in that system
    "title": "Fix login redirect",
    "url": "https://…",            // open it before listing it to the owner
    "kind": "ticket",              // ticket | review | message | reminder | event | other
    "created": "2026-01-01T09:00:00Z",
    "updated": "2026-01-02T09:00:00Z",
    "asks_me": true,               // directly assigned / addressed to the owner
    "excerpt": "first 200 chars"
  }]}
  ```
- **Errors:** exit non-zero with the reason on stderr. The runner records `{"error": …}` in
  `days/<date>/intake-<name>.json`, so a failing source is visible instead of looking empty.
- Sources reachable only through MCP (calendar, mail, chat) don't need an adapter: describe the read in
  `references/sources.md` and the cockpit does it in the morning routine.

Shipped: `example-tracker` (GitHub Issues assigned to you, as a pattern for any tracker) and `hermes` (planned slot,
exits 1 until wired).

## Notifier adapters (`adapters/notifier/<channel>`)
- **Selected by** `config.json → notify.channel` (`macos` is built in and needs no file).
- **Called as** `<channel> <title> <message> <kind>` by `ap.notify`, after dedupe and quiet-hours filtering.
- A notifier that leaves the machine (push service, chat) is an outward channel: enable it only with the owner's yes,
  and keep task details out of the message unless the owner wants them there.

Shipped: `ntfy` (example phone push) and `hermes` (planned slot).

## Executor (planned slot)
Today every task runs as a background Claude Code session through `scripts/dispatch.py` (`claude --bg`). A second
executor (e.g. Hermes agent) would plug in as:
- a task flag `"executor": "hermes"` in the registry;
- `dispatch.py` branching on it to call `adapters/executor/<name> launch <task-id> <brief-file>`, which prints a
  session id;
- the executor reporting state into `state/sessions/ap-<id>.json` with the same fields `session_event.py` writes
  (`ap_status`, `pending`, `last_message`, `ended`), so the board needs no change.
The same gates apply: the executor must not take any outward action without the owner's per-item yes.
