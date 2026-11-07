---
name: tokenforge
description: Turn repeated or deterministic AI work into tested Python handles that run for ~0 tokens. Use when a task is repeatable, when the same commands are re-derived across sessions, or when the user wants to save tokens. Keeps persistent memory of handles across opencode, Claude Code, Cursor, Windsurf, Codex, Gemini CLI and AGENTS.md-compatible IDEs.
license: MIT
metadata:
  version: 1.0.0
  author: Fabian Melomaciel
---

# TokenForge

Forge once. Run forever.

## THE PRIME RULE

Check memory first. If a handle exists, run it. Only forge when the task is
deterministic and repeatable. Never spend tokens re-deriving what a script can do.

## The loop

CHECK → RUN → FORGE → REGISTER → REUSE.

1. CHECK — read `.forge/memory.json`, or run `forge list` / `forge search <query>`.
2. RUN — execute an existing handle: `python -m forge run <name> --json '{...}'`.
3. FORGE — if no handle fits, create one: `python -m forge new <name> --task "..."`.
4. REGISTER — `new` records metadata in `.forge/memory.json` automatically.
5. REUSE — run it forever. Update the handle when the task changes.

## When NOT to forge

Do not forge:

- creative writing, design, naming, or open-ended analysis;
- one-off tasks that will not repeat;
- ambiguous tasks where inputs or success criteria are unclear;
- anything that needs human judgment, approval, or unverifiable external state;
- tasks that need secrets, network calls, or destructive side effects.

If the task is not deterministic, solve it directly and move on.

## Commands

| Command | Purpose |
| --- | --- |
| `forge init` | Create `.forge/` in the project. |
| `forge new NAME --task "..."` | Forge a handle from the template. |
| `forge run NAME --json '{"k":"v"}'` | Run a handle. Exit code is the handle's. |
| `forge test NAME` | Run the handle's built-in `--selftest`. |
| `forge list` | List handles and savings. |
| `forge show NAME` | Show metadata and file path. |
| `forge search QUERY` | Search names, tasks and tags. |
| `forge rm NAME --yes` | Delete a handle and its memory entry. |
| `forge stats` | Show conservative token savings. |
| `forge doctor` | Check Python, paths, memory and IDEs. |
| `forge install --project` | Install this skill and IDE pointers. |
| `forge uninstall --project` | Remove only TokenForge-marked content. |

Global flags: `--project DIR`, `--quiet`, `--version`.

## Forging rules

- The handle must be deterministic and standard-library only.
- Read input from `--json` (a JSON object or `-` for stdin). Write one JSON object to stdout.
- Keep exit codes: 0 success, 1 runtime error, 2 usage error.
- Put errors on stderr as `{"ok": false, "error": "..."}`.
- Run `forge test NAME` before registering or relying on the handle.
- Edit the generated `handle()` function. Keep `--selftest` meaningful.
- Never use `eval`, `exec`, `shell=True`, or network calls by default.

## Memory and safety

- `.forge/memory.json` stores metadata only: name, task, signature, path, uses,
  timestamps, tags and a conservative savings estimate. It never stores prompts,
  outputs, environment variables or file contents.
- Task descriptions are redacted before they are persisted.
- Do not put secrets in `--task`. TokenForge redacts common patterns, but it is
  not a guarantee.
- Handles marked `--danger` require `--yes` when run non-interactively.
- `run` refuses handles that are not registered in memory and paths outside
  `.forge/handles/`.
- Review every generated file. A forged handle is a starting point, not a proof.

## Worked example

```text
$ python -m forge init
$ python -m forge new hello --task "say hello"
$ python -m forge test hello
{"handle": "hello", "ok": true, "selftest": "passed"}
$ python -m forge run hello
{"message": "Hello, world!", "ok": true}
$ python -m forge list
```

The template ships with a working greeting. Replace `handle()` with your
logic, update `_selftest()`, then run `forge test <name>` again.
