#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Honest TokenForge micro-benchmark.

Creates a throw-away project, forges N handles, runs each one M times and
reports wall-clock milliseconds per run. The token column is TokenForge's
conservative ESTIMATE, not a measurement of real LLM tokens.

Standard library only. Python 3.8+.
"""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge import memory, registry, stats  # noqa: E402

DISCLAIMER = (
    "NOTE: wall-clock time is measured. \"tokens saved\" is TokenForge's\n"
    "conservative ESTIMATE (80% of an assumed {:,} manual tokens per run).\n"
    "It is NOT a measurement of real LLM tokens. See bench/README.md for a\n"
    "manual protocol that measures actual tokens with an agent."
).format(stats.DEFAULT_AVG_TOKENS_MANUAL)


def positive_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be an integer")
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def run_handle(path, timeout):
    """Run one handle once and return its wall-clock seconds."""
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, str(path), "--json", '{"name": "bench"}'],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    elapsed = time.perf_counter() - started
    if completed.returncode != 0:
        raise RuntimeError(
            "handle {} exited with {}: {}".format(
                path.name, completed.returncode, completed.stderr.strip()
            )
        )
    try:
        payload = json.loads(completed.stdout)
    except ValueError:
        raise RuntimeError(
            "handle {} did not print JSON: {!r}".format(path.name, completed.stdout)
        )
    if not payload.get("ok"):
        raise RuntimeError(
            "handle {} reported failure: {}".format(path.name, completed.stdout.strip())
        )
    return elapsed


def forge_handles(project, count):
    """Create ``count`` registered handles and return their names."""
    names = []
    for index in range(count):
        name = "bench_{:02d}".format(index)
        registry.create_handle(
            project,
            name,
            "benchmark task number {}".format(index),
            tags=["bench"],
        )
        names.append(name)
    return names


def measure(project, names, runs, timeout):
    """Measure every handle and return ``(rows, summary)``."""
    rows = []
    for name in names:
        path = registry.resolve_handle_path(project, name, must_exist=True)
        run_handle(path, timeout)  # warm-up, not measured

        timings = [run_handle(path, timeout) for _ in range(runs)]

        # Account for the successful runs using the real memory code path. This
        # happens outside the timed section so lock writes do not inflate ms/run.
        for _ in range(runs):
            memory.update_usage(project, name)

        rows.append(
            {
                "name": name,
                "runs": runs,
                "total_ms": sum(timings) * 1000.0,
                "mean_ms": statistics.mean(timings) * 1000.0,
                "median_ms": statistics.median(timings) * 1000.0,
                "min_ms": min(timings) * 1000.0,
                "max_ms": max(timings) * 1000.0,
            }
        )

    summary = stats.summarize(
        memory.load_memory(project), memory.load_config(project)
    )
    estimate_by_name = {
        row["name"]: row["tokens_saved_est"] for row in summary["per_handle"]
    }
    for row in rows:
        row["tokens_saved_est"] = estimate_by_name.get(row["name"], 0)

    totals = [row["total_ms"] for row in rows]
    return rows, {
        "total_ms": sum(totals),
        "runs": len(rows) * runs,
        "mean_ms": sum(totals) / (len(rows) * runs) if rows else 0.0,
        "tokens_saved_est": summary["tokens_saved_est"],
        "total_uses": summary["total_uses"],
    }


def print_table(rows, summary):
    headers = ["HANDLE", "RUNS", "TOTAL MS", "MEAN MS/RUN", "EST TOKENS SAVED"]
    widths = [len(header) for header in headers]
    body = []
    for row in rows:
        cells = [
            row["name"],
            str(row["runs"]),
            "{:.1f}".format(row["total_ms"]),
            "{:.1f}".format(row["mean_ms"]),
            stats.format_number(row["tokens_saved_est"]),
        ]
        body.append(cells)
        for index, cell in enumerate(cells):
            widths[index] = max(widths[index], len(cell))

    def render(cells):
        return "  ".join(
            cell.ljust(widths[index]) for index, cell in enumerate(cells)
        ).rstrip()

    print(render(headers))
    print("  ".join("-" * width for width in widths))
    for cells in body:
        print(render(cells))
    print(
        render(
            [
                "overall",
                str(summary["runs"]),
                "{:.1f}".format(summary["total_ms"]),
                "{:.1f}".format(summary["mean_ms"]),
                stats.format_number(summary["tokens_saved_est"]),
            ]
        )
    )


def build_parser():
    parser = argparse.ArgumentParser(
        prog="bench.py",
        description=(
            "Measure TokenForge handle wall-clock time. Token savings are "
            "estimates, never measured LLM tokens."
        ),
    )
    parser.add_argument(
        "--handles",
        type=positive_int,
        default=5,
        metavar="N",
        help="number of handles to forge (default: 5)",
    )
    parser.add_argument(
        "--runs",
        type=positive_int,
        default=10,
        metavar="M",
        help="measured runs per handle (default: 10)",
    )
    parser.add_argument(
        "--timeout",
        type=positive_int,
        default=60,
        metavar="SECONDS",
        help="per-run timeout (default: 60)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="keep the temporary project directory",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable summary instead of a table",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    project = Path(tempfile.mkdtemp(prefix="tokenforge-bench-"))
    try:
        memory.init_project(project)
        names = forge_handles(project, args.handles)
        rows, summary = measure(project, names, args.runs, args.timeout)

        if args.json:
            print(
                json.dumps(
                    {
                        "project": str(project),
                        "handles": args.handles,
                        "runs_per_handle": args.runs,
                        "rows": rows,
                        "summary": summary,
                        "tokens_saved_est": summary["tokens_saved_est"],
                        "token_savings_are_estimates": True,
                        "disclaimer": DISCLAIMER,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        print("TokenForge micro-benchmark")
        print("  project:         {}".format(project))
        print("  handles:         {}".format(args.handles))
        print("  runs per handle: {}".format(args.runs))
        print("")
        print(DISCLAIMER)
        print("")
        print_table(rows, summary)
        print("")
        print(
            "Mean ms/run includes Python interpreter startup for every run."
        )
        print(
            "The estimate assumes {} uses and {} tokens of manual work per run."
            .format(
                summary["total_uses"],
                stats.format_number(stats.DEFAULT_AVG_TOKENS_MANUAL),
            )
        )
        if args.keep:
            print("Kept temporary project: {}".format(project))
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(str(project), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
