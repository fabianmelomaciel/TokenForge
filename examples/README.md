# TokenForge examples

Two real, runnable handles that follow the same contract as the files
`python -m forge new` generates. They are **examples, not registered handles**:
nothing in this repository installs them into `.forge/memory.json`.

| File | What it does |
| --- | --- |
| `handles/slugify.py` | Turn text into a lowercase ASCII URL slug. |
| `handles/count_words.py` | Count words, lines and characters in text. |

## The handle contract

Every handle is a standalone Python 3.8+ script that:

- uses the standard library only and is deterministic by default;
- reads one JSON **object** from `--json '{"...": ...}'` (or from stdin with
  `--json -`);
- writes exactly one JSON object to stdout;
- supports `--selftest` and `--dry-run`;
- uses exit code `0` for success, `1` for a runtime error and `2` for a usage
  error;
- writes errors to stderr as `{"ok": false, "error": "..."}`;
- avoids `eval`, `exec`, `shell=True` and network access.

## Run them

```text
$ python examples/handles/slugify.py --json '{"text": "Hello World"}'
{"ok": true, "slug": "hello-world"}

$ python examples/handles/slugify.py --json - < input.json
{"ok": true, "slug": "..."}

$ python examples/handles/slugify.py --selftest
{"handle": "slugify", "ok": true, "selftest": "passed"}

$ python examples/handles/slugify.py --dry-run --json '{"text": "Hello World"}'
{"dry_run": true, "ok": true, "slug": "hello-world"}

$ python examples/handles/count_words.py --json '{"text": "one two three"}'
{"chars": 13, "lines": 1, "ok": true, "words": 3}
```

Bad input exits with code `2` and a JSON error on stderr:

```text
$ python examples/handles/slugify.py --json '{"text": 7}'
{"ok": false, "error": "'text' must be a string"}
$ echo $?        # 2 (Windows: echo %ERRORLEVEL%)
```

## Use one as a project handle

Copy the file into `.forge/handles/` in a TokenForge project and register it in
`.forge/memory.json`, or use it as a starting point:

```text
$ python -m forge new slugify --task "convert text to a URL slug"
```

Then edit `.forge/handles/slugify.py` and replace the generated `handle()`
function with the logic from `examples/handles/slugify.py`. Keep `--selftest`
meaningful, run `python -m forge test slugify`, and only then rely on it.

## Why these are examples

The point of TokenForge is that a handle should be written once, tested, and
then run for approximately zero tokens. These two files show the shape of that
work without pretending to cover every task. Anything creative, ambiguous, or
dependent on secrets and live network state should not become a handle.
