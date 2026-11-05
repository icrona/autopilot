#!/usr/bin/env python3
"""Run the intake adapters listed in config.json → intake.adapters and write days/<date>/intake-<name>.json each.

  intake_adapters.py            # run all enabled adapters
  intake_adapters.py <name>     # run one

An adapter is an executable at adapters/intake/<name> that prints one JSON object to stdout:
  {"items": [{"source", "id", "title", "url", "kind", "created", "updated", "asks_me", "excerpt"}, ...]}
It must be read-only. See adapters/README.md for the full contract."""
import json
import subprocess
import sys

import ap

ADAPTERS = ap.SCRIPTS.parent / "adapters" / "intake"


def run(name):
    path = ADAPTERS / name
    if not path.exists():
        return {"error": f"no adapter at {path}"}
    try:
        result = subprocess.run([str(path)], capture_output=True, text=True, timeout=120, cwd=str(ap.ROOT))
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"error": str(error)[:300]}
    if result.returncode != 0:
        return {"error": (result.stderr or f"exit {result.returncode}").strip()[:300]}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        return {"error": f"adapter printed invalid JSON: {error}"}
    return {"items": data.get("items", [])}


def main():
    enabled = [entry["name"] if isinstance(entry, dict) else entry
               for entry in (ap.config().get("intake") or {}).get("adapters", [])
               if not isinstance(entry, dict) or entry.get("enabled", True)]
    names = sys.argv[1:] or enabled
    summary = {}
    for name in names:
        result = {"generated": ap.now().isoformat(timespec="seconds"), "adapter": name, **run(name)}
        ap.write_json(ap.day_dir() / f"intake-{name}.json", result)
        summary[name] = result.get("error") or f"{len(result['items'])} item(s)"
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
