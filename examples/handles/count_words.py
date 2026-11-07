#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TokenForge example handle: count_words

TASK:
    Count words, lines and characters in a text string.

USAGE:
    python examples/handles/count_words.py --json '{"text": "one two three"}'
    python examples/handles/count_words.py --json - < input.json
    python examples/handles/count_words.py --selftest
    python examples/handles/count_words.py --dry-run --json '{"text": "hi"}'

CONTRACT:
    Standard library only, deterministic, no network. Reads one JSON object
    from --json (or stdin with -), writes one JSON object to stdout.
    Exit codes: 0 success, 1 runtime error, 2 usage error.
    Errors go to stderr as {"ok": false, "error": "..."}.
"""
from __future__ import annotations

import argparse
import json
import sys

HANDLE_NAME = "count_words"
TASK = "Count words, lines and characters in a text string."
TAGS = ["text", "count"]
DANGER = False


def count(text):
    """Return word, line and character counts for ``text``."""
    if text == "":
        return {"words": 0, "lines": 0, "chars": 0}
    return {
        "words": len(text.split()),
        "lines": len(text.splitlines()),
        "chars": len(text),
    }


def handle(payload, dry_run=False):
    """Return ``{"ok": true, "words": ..., "lines": ..., "chars": ...}``."""
    text = payload.get("text")
    if not isinstance(text, str):
        raise ValueError("'text' must be a string")
    result = {"ok": True}
    result.update(count(text))
    if dry_run:
        result["dry_run"] = True
    return result


def _read_payload(raw):
    if raw == "-":
        raw = sys.stdin.read()
    if raw is None or not str(raw).strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON input: {}".format(exc))
    if not isinstance(data, dict):
        raise ValueError("JSON input must be an object")
    return data


def _selftest_fail(detail):
    print(
        json.dumps({"ok": False, "error": "selftest failed: " + detail}),
        file=sys.stderr,
    )
    return 1


def _selftest():
    checks = [
        ({"text": "one two three"}, {"words": 3, "lines": 1, "chars": 13}),
        ({"text": "a\nb\n"}, {"words": 2, "lines": 2, "chars": 4}),
        ({"text": ""}, {"words": 0, "lines": 0, "chars": 0}),
        ({"text": "  padded   text  "}, {"words": 2, "lines": 1, "chars": 17}),
    ]
    for payload, expected in checks:
        try:
            result = handle(payload)
        except Exception as exc:  # pragma: no cover - defensive
            return _selftest_fail("{}: {}".format(type(exc).__name__, exc))
        for key, value in expected.items():
            if result.get(key) != value:
                return _selftest_fail(
                    "{!r} produced {}={!r}, expected {!r}".format(
                        payload, key, result.get(key), value
                    )
                )

    try:
        handle({"text": None})
    except ValueError:
        pass
    else:
        return _selftest_fail("non-string text did not raise ValueError")

    dry = handle({"text": "Dry Run"}, dry_run=True)
    if dry.get("dry_run") is not True:
        return _selftest_fail("dry_run flag was not reported")

    print(
        json.dumps(
            {"ok": True, "selftest": "passed", "handle": HANDLE_NAME},
            sort_keys=True,
        )
    )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="TokenForge example: count_words"
    )
    parser.add_argument(
        "--json",
        default="{}",
        metavar="JSON",
        help="JSON object to process, or '-' to read from stdin",
    )
    parser.add_argument("--selftest", action="store_true", help="run built-in checks")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate input without producing side effects",
    )
    parser.add_argument("extra", nargs="*", metavar="ARG")
    args, unknown = parser.parse_known_args(argv)

    if args.selftest:
        return _selftest()

    try:
        payload = _read_payload(args.json)
        extra = list(args.extra) + list(unknown)
        if extra:
            payload.setdefault("_args", extra)
        result = handle(payload, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except ValueError as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # pragma: no cover - defensive
        print(
            json.dumps(
                {"ok": False, "error": "{}: {}".format(type(exc).__name__, exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
