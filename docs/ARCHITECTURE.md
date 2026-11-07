# Architecture

TokenForge is a small, dependency-free Python package. It has one job: turn a
repeatable task into a file, remember that file, and run it without spending
model tokens again.

## Components

| Module | Responsibility |
|---|---|
| `forge/cli.py` | Argparse surface, command handlers, human and JSON output, exit codes. |
| `forge/registry.py` | Handle names, path containment, file lifecycle, duplicate checks. |
| `forge/memory.py` | `.forge/` layout, JSON persistence, locking, atomic writes, corruption recovery. |
| `forge/stats.py` | Conservative savings estimator and aggregation. |
| `forge/redact.py` | Best-effort redaction of common credential shapes. |
| `forge/template.py` | The Python handle template (`--json`, `--selftest`, `--dry-run`). |
| `forge/installer.py` | Idempotent IDE targets, marker blocks, backups, uninstall. |
| `forge/detect.py` | OS name and conservative IDE config detection. |
| `install.py` | Repository bootstrap; imports `forge/` and delegates to the installer. |
| `SKILL.md` | The agent-facing contract: prime rule, loop, commands, safety. |

There are no runtime dependencies and no plugin system. Every module uses the
Python standard library.

## Data flow

### `init`

Resolve the project (explicit `--project`, else the nearest parent containing
`.forge/`, else the current directory). Create `.forge/`, `.forge/handles/`,
`config.json` and `memory.json` if missing. Existing files are loaded, not
overwritten.

### `new NAME --task "..."`
1. Validate the name against `^[a-z][a-z0-9_-]{0,63}$`.
2. Redact the task, normalize whitespace, hash it with SHA-256, and reject
   empty or overlong descriptions.
3. Render the template, then `compile()` the source as a syntax check.
4. Take `.forge/memory.lock`, confirm the name and file do not already exist.
5. Write the handle atomically and add its metadata to `memory.json`.
6. If `--avg-tokens` was supplied, persist the override in `config.json`.

### `run NAME --json ...`
1. Validate the name and require a registered memory entry.
2. Resolve the real path and require it to stay under `.forge/handles/`.
3. Enforce the danger gate (`--danger` needs a TTY confirmation or `--yes`).
4. Execute `[sys.executable, <handle>, "--json", <payload>]` with no shell.
5. Forward stdout/stderr, enforce the timeout, and return the handle's exit code.
6. On exit code 0 only, increment `uses`, set `last_used`, and recompute the
   savings estimate under the memory lock.

### `test NAME`
Run the handle with `--selftest` and a 60-second timeout. Testing does not
increment usage counters.

### `stats`
Load memory and config, aggregate per-handle usage, apply the estimator, and
emit text or `--json`. It never runs a handle.

### `install` / `uninstall`
Detect IDE config roots, build a target list for the chosen scope and `--ide`
filter, then create dedicated files, marker blocks or Cursor `.mdc` rules.
Uninstall removes only TokenForge-marked content.

## Memory schema

`.forge/memory.json`:

```json
{
  "version": 1,
  "project": "demo",
  "created": "2026-01-01T12:00:00+00:00",
  "handles": {
    "slugify": {
      "name": "slugify",
      "task": "convert text to a URL slug",
      "signature": "<sha256 of the normalized task>",
      "path": ".forge/handles/slugify.py",
      "created": "2026-01-01T12:00:00+00:00",
      "last_used": null,
      "uses": 0,
      "tokens_saved_est": 0,
      "avg_tokens_manual": 1500,
      "tags": ["text", "url"],
      "danger": false
    }
  }
}
```

`.forge/config.json`:

```json
{
  "version": 1,
  "avg_tokens_manual": 1500,
  "default_timeout": 120,
  "handles": {}
}
```

Per-handle overrides live under `handles.<name>.avg_tokens_manual` and win over
the global value. The estimator is:

```text
tokens_saved_est = successful_uses × avg_tokens_manual × 0.8
```

The `0.8` factor is fixed and deliberately conservative. Estimates are labeled
as estimates; TokenForge never measures provider billing.

## Security boundaries

- **Names and paths.** Only lowercase slug names are accepted. `realpath` must
  stay under `.forge/handles/`; symlinked `.forge/` or `handles/` directories
  are refused. Cross-drive resolution is rejected on Windows.
- **Execution.** Handles run as separate Python processes with an argument
  vector. There is no `shell=True`. There is no sandbox: a handle has the same
  filesystem, environment and network access as the user who ran it.
- **Danger gate.** A handle marked `--danger` prompts on a TTY and requires
  `--yes` otherwise.
- **Timeouts.** `run` uses `default_timeout` (120 s) or `--timeout`; `test` uses
  60 s.
- **Persistence.** Memory stores metadata only. Task text is redacted before it
  is written, but redaction is not a guarantee. Never put secrets in `--task`.
- **Installer.** Only known IDE paths are touched. Existing files are backed up
  once to `<file>.tokenforge.bak`, writes are atomic, symlink targets are
  refused, and `--dry-run` writes nothing.
- **No telemetry.** The CLI and template make no network calls.

## Installer targets

| IDE | Global | Project |
|---|---|---|
| opencode | `~/.config/opencode/skills/tokenforge/SKILL.md`, `AGENTS.md` | `.opencode/skills/tokenforge/SKILL.md`, `AGENTS.md` |
| Claude Code | `~/.claude/skills/tokenforge/SKILL.md`, `CLAUDE.md` | `.claude/skills/tokenforge/SKILL.md`, `CLAUDE.md` |
| Cursor | `~/.cursor/rules/tokenforge.mdc` | `.cursor/rules/tokenforge.mdc` |
| Windsurf | `~/.codeium/windsurf/memories/global_rules.md` | `.windsurf/rules/tokenforge.md` |
| Codex | `~/.codex/AGENTS.md` | `AGENTS.md` |
| Gemini CLI | `~/.gemini/GEMINI.md` | `GEMINI.md` |
| VS Code Copilot | — | `.github/copilot-instructions.md` |

Global targets are skipped when the config root is missing; `--force` creates
most of them. Windsurf's global target is only written when
`~/.codeium/windsurf` already exists. Shared files use
`<!-- tokenforge:begin -->` / `<!-- tokenforge:end -->` markers.

## Extension points

- **New command:** add a handler in `cli.py` and register a subparser in
  `build_parser()`.
- **New IDE:** add its name to `IDE_ORDER` in `detect.py`, add project/global
  detection, then add targets in `installer._build_targets()`.
- **Template change:** edit `forge/template.py`; keep the `handle()`,
  `--selftest`, `--json` and exit-code contract stable.
- **Estimator change:** edit `forge/stats.py`; keep the result explicitly
  labeled as an estimate.
- **Redaction rule:** add a pattern to `_RULES` in `forge/redact.py`; order
  specific token shapes before generic assignments.

Keep new code standard-library only, deterministic by default, and covered by
tests in `tests/`.
