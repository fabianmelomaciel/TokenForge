# Contributing

TokenForge is deliberately small. Contributions are welcome if they keep it
small.

## Development setup

Python 3.8+ and nothing else. There are no runtime or development dependencies.

```bash
git clone https://github.com/fabianmelomaciel/TokenForge.git
cd TokenForge
python -m compileall -q forge install.py
```

Run the CLI from the checkout:

```bash
python -m forge --help
python -m forge doctor
```

Use a throwaway project for manual checks:

```bash
mkdir /tmp/forge-demo && cd /tmp/forge-demo
python -m forge init
python -m forge new hello --task "say hello"
python -m forge test hello
python -m forge run hello --json '{"name": "world"}'
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Add new tests under `tests/`. Every change to the CLI, registry, memory,
installer or estimator should include a test that fails without the change.
Tests must not depend on the user's real home directory or installed IDEs;
use temporary directories and explicit paths.

## Style

- Standard library only. Do not add a runtime dependency.
- Four-space indentation, UTF-8, LF line endings.
- Type hints on public functions; `from __future__ import annotations` where it
  keeps 3.8 compatibility.
- Keep CLI output stable. Humans read the tables; agents read `--json`.
- Keep exit codes: `0` success, `1` runtime error, `2` usage error.
- Never introduce `eval`, `exec`, `shell=True`, or a network call in core.
- Redact before persisting. Treat task text as untrusted.
- Preserve memory/config version `1` unless you provide a migration.

## Pull requests

- One concern per pull request. Keep the diff readable.
- Explain the user-visible behavior and the failure mode you are fixing.
- Run `python -m compileall -q forge install.py` and the test suite before
  opening the PR.
- Update `README.md`, `README.es.md`, `SKILL.md` or `docs/ARCHITECTURE.md` when
  commands, paths or behavior change. Keep the English and Spanish docs in
  sync.
- Do not commit generated handles, `.forge/` state, virtual environments or
  build artifacts.
- Do not put generated code in `forge/`. The package is the forge; handles are
  user data.

Security reports do not belong in a public issue. Follow [SECURITY.md](SECURITY.md).

By contributing, you agree that your contribution is licensed under the MIT
License.
