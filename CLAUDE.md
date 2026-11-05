# Autopilot blueprint repo

This repo is a blueprint, not a running cockpit.

- To set up a cockpit for the person using this machine, run the `bootstrap` skill
  (`.claude/skills/bootstrap/SKILL.md`). Read `BLUEPRINT.md` first.
- `kit/` is the reference implementation. Its `.claude/` folder is the *generated cockpit's* config: it does not
  apply to sessions opened in this repo.
- Changes to the design go into `BLUEPRINT.md` and `kit/` together. Keep `kit/` free of any personal or employer
  details: names, hosts, repos, ticket keys and ids belong only in a generated cockpit's `config.json`.
- Run the kit tests after any script change:
  `cd kit/.claude/skills/autopilot && python3 -m unittest discover -s tests`.
- Bootstrap writes local files only. Pushing this repo, creating remotes or publishing anything needs the owner's
  explicit yes for that item.
