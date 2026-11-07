# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.0.x | Yes |
| < 1.0 | No |

Security fixes land on the latest 1.0.x release. If you run a fork, you own its
patches.

## Reporting a vulnerability

Use GitHub Security Advisories:

**https://github.com/fabianmelomaciel/TokenForge/security/advisories/new**

Do not open a public issue for a vulnerability. Include the affected version,
a minimal reproduction, the impact you observed, and any suggested fix. You will
get an acknowledgement as soon as the maintainer can review it; please allow
time for a coordinated disclosure.

## Threat model

TokenForge is a local developer tool. It writes Python files, registers metadata
and executes those files with the current user's permissions. It is not a
sandbox, a package registry, or a multi-tenant service.

### Assets

- Project files and the user's home directory.
- Environment variables and any credentials available to the user.
- `.forge/memory.json` and `.forge/config.json`.
- IDE configuration files written by the installer.
- The integrity of the TokenForge package itself.

### Trust boundaries

- **Generated handles are untrusted code.** A handle is a Python file that the
  user or an agent can edit. `forge run` executes it as a child process.
- **Task descriptions are untrusted text.** They are redacted and stored as
  metadata, but an agent that reads memory may treat their contents as
  instructions. Treat task text as data, never as a command.
- **IDE configuration is shared state.** The installer edits files owned by
  other tools and must not clobber unrelated content.
- **The local machine is trusted.** TokenForge assumes the operating system,
  Python interpreter and user account are not already compromised.

### Mitigations in the implementation

- Names must match `^[a-z][a-z0-9_-]{0,63}$`.
- Resolved handle paths must stay under `.forge/handles/`; symlinked `.forge/`
  or `handles/` directories are refused.
- `run` refuses handles that are not registered in memory.
- Handles execute through an argument vector (`sys.executable` + file + args),
  never through a shell. `shell=True` is not used.
- `--danger` handles require an interactive confirmation or `--yes`.
- Runs have a timeout (120 seconds by default; 60 seconds for `--selftest`).
- Memory and config writes use a temp file plus `os.replace`, with a lock file
  for cross-process writers.
- Task text is redacted for common private keys, API tokens, JWTs, bearer
  headers, URL credentials and assignment-shaped secrets before persistence.
- The installer touches only known IDE paths, refuses symlink targets, backs up
  modified files to `<file>.tokenforge.bak`, and supports `--dry-run`.
- The CLI and template make no network calls and collect no telemetry.

## What TokenForge does not protect against

- **Malicious handles you approve.** Once a handle runs, it can read files,
  spawn processes, use the network and modify anything your user can modify.
  Review generated code before running it. There is no sandbox.
- **Secrets placed in `--task`.** Redaction is a safety net, not a guarantee.
  Never put credentials in a task description.
- **Environment leakage.** Handles inherit the environment of the process that
  invokes `forge run`, including any secrets exported in that shell.
- **Prompt injection through memory or task text.** An agent reading
  `.forge/memory.json` may see adversarial text. Keep task descriptions short
  and factual; do not treat memory as trusted instructions.
- **A compromised Python interpreter, OS account or repository.** TokenForge
  cannot defend a machine that is already compromised.
- **Third-party IDEs and their configuration formats.** The installer writes
  documented paths and marker blocks, but those tools may change their formats.
- **Denial of service through generated code.** Timeouts limit runtime, not
  memory, CPU or disk consumption.

## Safe usage checklist

1. Read `.forge/handles/<name>.py` before the first run.
2. Keep secrets out of `--task` and out of `.forge/`.
3. Use `--danger` for handles with side effects.
4. Set a realistic `--timeout` for anything that touches the filesystem.
5. Review `forge install --dry-run` output before writing to IDE configs.
6. Report suspected vulnerabilities through GitHub Security Advisories.
