"""Command-line interface for TokenForge."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import installer, memory, registry, stats
from .detect import detection_details, os_name
from .version import VERSION


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _fail(message: str, code: int = 2) -> int:
    sys.stderr.write("error: {}\n".format(message))
    return code


def _configure_stdio() -> None:
    """Use UTF-8 for CLI output so JSON survives pipes on every platform."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def _info(args: argparse.Namespace, message: str = "") -> None:
    if not getattr(args, "quiet", False):
        print(message)


def _json_print(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False))


def _confirm(prompt: str) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


def _resolve_project(explicit: Optional[str]) -> Path:
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate.resolve()
    current = Path.cwd().resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / ".forge").is_dir():
            return candidate
    return current


def _ensure_forge(project: Path) -> Optional[str]:
    if not (project / ".forge").is_dir():
        return (
            "no .forge/ directory found in {}; run `python -m forge init` "
            "first".format(project)
        )
    return None


def _short_time(value: Any) -> str:
    if not value:
        return "never"
    return str(value).replace("T", " ")[:19]


def _truncate(text: Any, limit: int = 52) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> None:
    widths = [len(str(header)) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    def render(values: Sequence[Any]) -> str:
        return "  ".join(
            str(value).ljust(widths[index]) for index, value in enumerate(values)
        ).rstrip()

    print(render(headers))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print(render(row))


def _handle_rows(handles: Dict[str, Any]) -> List[List[str]]:
    rows: List[List[str]] = []
    for name in sorted(handles):
        meta = handles[name]
        if not isinstance(meta, dict):
            continue
        rows.append(
            [
                name,
                str(int(meta.get("uses", 0) or 0)),
                _short_time(meta.get("last_used")),
                stats.format_number(int(meta.get("tokens_saved_est", 0) or 0)),
                _truncate(meta.get("task", "")),
            ]
        )
    return rows


def _can_write(directory: Path) -> bool:
    try:
        handle, name = tempfile.mkstemp(
            prefix=".tokenforge-doctor-", dir=str(directory)
        )
        os.close(handle)
        os.unlink(name)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    result = memory.init_project(project)
    forge = project / ".forge"

    if result["created"]:
        _info(args, "Initialized TokenForge in {}".format(forge))
        for path in result["created"]:
            _info(args, "  created {}".format(path))
    else:
        _info(args, "TokenForge is already initialized in {}".format(forge))

    _info(args, "")
    _info(args, "Project: {}".format(project))
    _info(args, "Memory:  {}".format(memory.memory_path(project)))
    _info(args, "Handles: {}".format(memory.handles_path(project)))
    _info(args, "")
    _info(args, "Next steps:")
    _info(args, '  python -m forge new hello --task "say hello"')
    _info(args, "  python -m forge test hello")
    _info(args, '  python -m forge run hello --json \'{"name": "world"}\'')
    _info(args, "  python -m forge install --project")
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    problem = _ensure_forge(project)
    if problem:
        return _fail(problem, 2)

    tags = [part for part in (args.tags or "").split(",") if part.strip()]
    try:
        meta, path = registry.create_handle(
            project_dir=project,
            name=args.name,
            task=args.task,
            tags=tags,
            danger=args.danger,
            avg_tokens_manual=args.avg_tokens,
        )
    except (registry.RegistryError, memory.MemoryError) as exc:
        return _fail(str(exc), 2)

    try:
        similar = memory.find_by_signature(project, meta["signature"])
    except (memory.MemoryError, OSError):
        similar = None
    if similar and similar != meta["name"]:
        _info(
            args,
            "note: handle '{}' already covers a similar task".format(similar),
        )

    _info(args, "Forged handle '{}'".format(meta["name"]))
    _info(args, "  file:   {}".format(path))
    _info(args, "  task:   {}".format(meta["task"]))
    _info(args, "  tags:   {}".format(", ".join(meta["tags"]) or "(none)"))
    _info(args, "  danger: {}".format("yes" if meta["danger"] else "no"))
    _info(args, "")
    _info(args, "Next steps:")
    _info(args, "  python -m forge test {}".format(meta["name"]))
    _info(
        args,
        '  python -m forge run {} --json \'{{"name": "world"}}\''.format(
            meta["name"]
        ),
    )
    return 0


def _timeout_for(args: argparse.Namespace, project: Path) -> Optional[int]:
    if args.timeout is not None:
        return int(args.timeout)
    config = memory.load_config(project)
    try:
        return int(config.get("default_timeout", 120))
    except (TypeError, ValueError):
        return 120


def _subprocess_env() -> Dict[str, str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def cmd_run(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    problem = _ensure_forge(project)
    if problem:
        return _fail(problem, 2)

    try:
        name = registry.validate_name(args.name)
    except registry.RegistryError as exc:
        return _fail(str(exc), 2)

    data = memory.load_memory(project)
    meta = data["handles"].get(name)
    if not isinstance(meta, dict):
        return _fail(
            "handle '{}' is not registered; run `python -m forge list`".format(name),
            1,
        )

    try:
        path = registry.resolve_handle_path(project, name, must_exist=True)
    except registry.RegistryError as exc:
        return _fail(str(exc), 1)

    if meta.get("danger") and not args.yes:
        if not _confirm(
            "Handle '{}' is marked danger. Run it anyway? [y/N] ".format(name)
        ):
            return _fail(
                "danger handle '{}' requires confirmation; pass --yes".format(name),
                2,
            )

    timeout = _timeout_for(args, project)
    if timeout is None or timeout <= 0:
        return _fail("--timeout must be a positive number of seconds", 2)

    command = [sys.executable, str(path)]
    if args.json is not None:
        command.extend(["--json", args.json])
    if args.extra:
        command.extend(list(args.extra))

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            json.dumps(
                {
                    "ok": False,
                    "error": "handle '{}' timed out after {} seconds".format(
                        name, timeout
                    ),
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        return 124
    except OSError as exc:
        return _fail("could not run handle '{}': {}".format(name, exc), 1)

    if completed.stdout:
        sys.stdout.write(completed.stdout)
        sys.stdout.flush()
    if completed.stderr:
        sys.stderr.write(completed.stderr)
        sys.stderr.flush()

    if completed.returncode == 0:
        try:
            memory.update_usage(project, name, success=True)
        except (memory.MemoryError, OSError) as exc:
            _info(args, "warning: could not update memory: {}".format(exc))

    return completed.returncode if completed.returncode >= 0 else 1


def cmd_test(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    problem = _ensure_forge(project)
    if problem:
        return _fail(problem, 2)

    try:
        name = registry.validate_name(args.name)
    except registry.RegistryError as exc:
        return _fail(str(exc), 2)

    if memory.get_handle(project, name) is None:
        return _fail(
            "handle '{}' is not registered; run `python -m forge list`".format(name),
            1,
        )

    try:
        path = registry.resolve_handle_path(project, name, must_exist=True)
    except registry.RegistryError as exc:
        return _fail(str(exc), 1)

    try:
        completed = subprocess.run(
            [sys.executable, str(path), "--selftest"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired:
        return _fail("selftest for '{}' timed out after 60 seconds".format(name), 1)
    except OSError as exc:
        return _fail("could not test handle '{}': {}".format(name, exc), 1)

    if completed.stdout:
        sys.stdout.write(completed.stdout)
        sys.stdout.flush()
    if completed.stderr:
        sys.stderr.write(completed.stderr)
        sys.stderr.flush()
    return completed.returncode


def cmd_list(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    problem = _ensure_forge(project)
    if problem:
        return _fail(problem, 2)

    data = memory.load_memory(project)
    handles = data.get("handles", {})
    ordered = [handles[name] for name in sorted(handles) if isinstance(handles[name], dict)]

    if args.json:
        _json_print(
            {
                "project": data.get("project", ""),
                "count": len(ordered),
                "handles": ordered,
            }
        )
        return 0

    if not ordered:
        print("No handles yet. Forge one with:")
        print('  python -m forge new hello --task "say hello"')
        return 0

    _table(
        ["NAME", "USES", "LAST USED", "TOKENS SAVED", "TASK"],
        _handle_rows(handles),
    )
    print("")
    print(stats.estimate_label())
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    problem = _ensure_forge(project)
    if problem:
        return _fail(problem, 2)

    try:
        name = registry.validate_name(args.name)
    except registry.RegistryError as exc:
        return _fail(str(exc), 2)

    meta = memory.get_handle(project, name)
    if meta is None:
        return _fail("handle '{}' is not registered".format(name), 1)

    try:
        path = registry.resolve_handle_path(project, name, must_exist=False)
    except registry.RegistryError as exc:
        return _fail(str(exc), 1)

    print("name:               {}".format(meta.get("name", name)))
    print("task:               {}".format(meta.get("task", "")))
    print("signature:          {}".format(meta.get("signature", "")))
    print("file:               {}".format(path))
    print("memory path:        {}".format(meta.get("path", "")))
    print("created:            {}".format(meta.get("created", "")))
    print("last used:          {}".format(_short_time(meta.get("last_used"))))
    print("uses:               {}".format(int(meta.get("uses", 0) or 0)))
    print(
        "tokens saved (est): {}".format(
            stats.format_number(int(meta.get("tokens_saved_est", 0) or 0))
        )
    )
    effective_avg = stats.avg_tokens_for_handle(
        memory.load_config(project),
        name,
        int(meta.get("avg_tokens_manual", stats.DEFAULT_AVG_TOKENS_MANUAL) or 0),
    )
    print("avg tokens manual:  {}".format(effective_avg))
    print("tags:               {}".format(", ".join(meta.get("tags") or []) or "(none)"))
    print("danger:             {}".format("yes" if meta.get("danger") else "no"))
    if not path.is_file():
        print("status:             MISSING FILE")
    print("")
    print(stats.estimate_label(effective_avg))
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    problem = _ensure_forge(project)
    if problem:
        return _fail(problem, 2)

    try:
        name = registry.validate_name(args.name)
        path = registry.handle_file_path(project, name)
    except registry.RegistryError as exc:
        return _fail(str(exc), 2)

    meta = memory.get_handle(project, name)
    if meta is None and not path.exists():
        return _fail("handle '{}' not found".format(name), 1)

    if not args.yes:
        if not _confirm("Delete handle '{}' and its memory entry? [y/N] ".format(name)):
            return _fail("confirmation required; pass --yes", 2)

    try:
        removed, real = registry.delete_handle(project, name)
    except (registry.RegistryError, memory.MemoryError) as exc:
        return _fail(str(exc), 1)

    print("Removed handle '{}'".format(name))
    print("  file: {}".format(real))
    if removed is None:
        print("  note: no memory entry was present")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    problem = _ensure_forge(project)
    if problem:
        return _fail(problem, 2)

    query = str(args.query).strip().lower()
    data = memory.load_memory(project)
    matches: Dict[str, Any] = {}
    for name, meta in data.get("handles", {}).items():
        if not isinstance(meta, dict):
            continue
        tags = [str(tag).lower() for tag in meta.get("tags", [])]
        if (
            query in name.lower()
            or query in str(meta.get("task", "")).lower()
            or any(query in tag for tag in tags)
        ):
            matches[name] = meta

    if not matches:
        print("No handles match '{}'.".format(args.query))
        return 0

    _table(
        ["NAME", "USES", "LAST USED", "TOKENS SAVED", "TASK"],
        _handle_rows(matches),
    )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    problem = _ensure_forge(project)
    if problem:
        return _fail(problem, 2)

    data = memory.load_memory(project)
    config = memory.load_config(project)
    summary = stats.summarize(data, config)

    if args.json:
        payload = dict(summary)
        payload["project_path"] = str(project)
        _json_print(payload)
        return 0

    print("TokenForge stats for {}".format(project))
    print("  handles:              {}".format(summary["handles"]))
    print("  total uses:           {}".format(summary["total_uses"]))
    print(
        "  tokens saved (est):   {}".format(
            stats.format_number(summary["tokens_saved_est"])
        )
    )
    print("")
    print(summary["estimate"]["label"])
    print("")

    rows = [
        [
            row["name"],
            str(row["uses"]),
            _short_time(row["last_used"]),
            stats.format_number(row["tokens_saved_est"]),
            _truncate(row["task"]),
        ]
        for row in summary["per_handle"]
    ]
    if rows:
        _table(["NAME", "USES", "LAST USED", "TOKENS SAVED", "TASK"], rows)
    else:
        print("No handles yet.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    failures = 0

    print("TokenForge doctor {}".format(VERSION))
    print("  python:  {} ({})".format(sys.version.split()[0], sys.executable))
    if sys.version_info < (3, 8):
        print("  [fail] python: TokenForge requires Python 3.8 or newer")
        failures += 1
    else:
        print("  [ok] python: supported")
    print("  os:      {}".format(os_name()))
    print("  project: {}".format(project))

    forge = project / ".forge"
    if not forge.is_dir():
        print("  [fail] .forge: not found; run `python -m forge init`")
        return 1
    print("  [ok] .forge: {}".format(forge))

    data, error = memory.inspect_memory(project)
    if error is not None:
        print("  [fail] memory: {}".format(error))
        failures += 1
        handles: Dict[str, Any] = {}
    else:
        handles = data.get("handles", {}) if data else {}
        print("  [ok] memory: {} handle(s)".format(len(handles)))
        required = (
            "name",
            "task",
            "signature",
            "path",
            "created",
            "last_used",
            "uses",
            "tokens_saved_est",
            "avg_tokens_manual",
            "tags",
            "danger",
        )
        for name in sorted(handles):
            meta = handles[name]
            if not isinstance(meta, dict):
                print("  [fail] handle {}: entry is not an object".format(name))
                failures += 1
                continue
            missing = [key for key in required if key not in meta]
            if missing:
                print(
                    "  [warn] handle {}: missing field(s) {}".format(
                        name, ", ".join(missing)
                    )
                )
            expected = memory.task_signature(str(meta.get("task", "")))
            if meta.get("signature") != expected:
                print("  [warn] handle {}: signature does not match task".format(name))
            try:
                handle_path = registry.resolve_handle_path(
                    project, name, must_exist=True
                )
                print("  [ok] handle {}: {}".format(name, handle_path))
            except registry.RegistryError as exc:
                print("  [fail] handle {}: {}".format(name, exc))
                failures += 1

        files = registry.list_handle_files(project)
        extra = sorted(set(files) - set(handles))
        if extra:
            print(
                "  [warn] unregistered handle file(s): {}".format(", ".join(extra))
            )

    config, config_error = memory.inspect_config(project)
    if config_error is not None:
        print("  [fail] config: {}".format(config_error))
        failures += 1
    else:
        print("  [ok] config: {}".format(memory.config_path(project)))

    for label, directory in (
        ("project writable", project),
        (".forge writable", forge),
        ("handles writable", memory.handles_path(project)),
    ):
        if not directory.is_dir():
            print("  [warn] {}: directory missing ({})".format(label, directory))
            continue
        if _can_write(directory):
            print("  [ok] {}: {}".format(label, directory))
        else:
            print("  [fail] {}: not writable ({})".format(label, directory))
            failures += 1

    details = detection_details(project)
    if details:
        print("  [ok] IDEs detected:")
        for ide, scope, path in details:
            print("        {} ({}): {}".format(ide, scope, path))
    else:
        print("  [warn] no IDE config directories detected")

    skill_root = Path(__file__).resolve().parent.parent / "SKILL.md"
    if skill_root.is_file():
        print("  [ok] skill source: {}".format(skill_root))
    else:
        print("  [warn] skill source not found at {}".format(skill_root))

    print("")
    if failures:
        print("Doctor found {} problem(s).".format(failures))
        return 1
    print("Doctor found no problems.")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    report = installer.install(
        scope=args.scope,
        project_dir=project,
        ides=args.ide,
        dry_run=args.dry_run,
        force=args.force,
    )
    for line in report.lines:
        print(line)
    return 0 if report.ok else 1


def cmd_uninstall(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    report = installer.uninstall(
        scope=args.scope,
        project_dir=project,
        dry_run=args.dry_run,
    )
    for line in report.lines:
        print(line)
    return 0 if report.ok else 1


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project",
        metavar="DIR",
        default=argparse.SUPPRESS,
        help="project directory (default: nearest parent with .forge/)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="suppress informational output",
    )


def _add_quiet(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="suppress informational output",
    )


def _add_scope(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--global",
        dest="scope",
        action="store_const",
        const="global",
        help="install for the current user (default)",
    )
    group.add_argument(
        "--project",
        dest="scope",
        action="store_const",
        const="project",
        help="install into the current project",
    )
    parser.set_defaults(scope="global")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge",
        description=(
            "TokenForge - turn repeated or deterministic AI work into tested "
            "Python handles that run for ~0 tokens."
        ),
        epilog="Forge once. Run forever.",
    )
    parser.add_argument(
        "--project",
        metavar="DIR",
        default=None,
        help="project directory (default: nearest parent with .forge/)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress informational output",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="TokenForge {}".format(VERSION),
    )

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")

    init_parser = subparsers.add_parser("init", help="create .forge/ in a project")
    _add_common(init_parser)
    init_parser.set_defaults(func=cmd_init)

    new_parser = subparsers.add_parser("new", help="forge a new handle")
    new_parser.add_argument("name", help="handle name (lowercase slug)")
    new_parser.add_argument(
        "--task", required=True, help="what the handle does (redacted before saving)"
    )
    new_parser.add_argument(
        "--tags", default="", help="comma-separated tags (for example: text,url)"
    )
    new_parser.add_argument(
        "--danger",
        action="store_true",
        help="mark the handle as requiring confirmation when run",
    )
    new_parser.add_argument(
        "--avg-tokens",
        type=int,
        default=None,
        metavar="N",
        help="assumed manual tokens per use (default: 1500)",
    )
    _add_common(new_parser)
    new_parser.set_defaults(func=cmd_new)

    run_parser = subparsers.add_parser("run", help="run an existing handle")
    run_parser.add_argument("name", help="handle name")
    run_parser.add_argument(
        "--json",
        default=None,
        metavar="JSON",
        help="JSON object to pass to the handle, or '-' for stdin",
    )
    run_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        metavar="SECONDS",
        help="execution timeout (default: config.json default_timeout)",
    )
    run_parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm danger handles in non-interactive mode",
    )
    run_parser.add_argument(
        "extra",
        nargs="*",
        metavar="ARG",
        help="extra arguments passed to the handle after --",
    )
    _add_common(run_parser)
    run_parser.set_defaults(func=cmd_run)

    test_parser = subparsers.add_parser("test", help="run a handle's --selftest")
    test_parser.add_argument("name", help="handle name")
    _add_common(test_parser)
    test_parser.set_defaults(func=cmd_test)

    list_parser = subparsers.add_parser("list", help="list registered handles")
    list_parser.add_argument("--json", action="store_true", help="machine-readable output")
    _add_common(list_parser)
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser("show", help="show one handle's metadata")
    show_parser.add_argument("name", help="handle name")
    _add_common(show_parser)
    show_parser.set_defaults(func=cmd_show)

    rm_parser = subparsers.add_parser("rm", help="delete a handle")
    rm_parser.add_argument("name", help="handle name")
    rm_parser.add_argument(
        "--yes", action="store_true", help="confirm deletion non-interactively"
    )
    _add_common(rm_parser)
    rm_parser.set_defaults(func=cmd_rm)

    search_parser = subparsers.add_parser("search", help="search names, tasks and tags")
    search_parser.add_argument("query", help="text to search for")
    _add_common(search_parser)
    search_parser.set_defaults(func=cmd_search)

    stats_parser = subparsers.add_parser("stats", help="show token-savings estimates")
    stats_parser.add_argument("--json", action="store_true", help="machine-readable output")
    _add_common(stats_parser)
    stats_parser.set_defaults(func=cmd_stats)

    doctor_parser = subparsers.add_parser("doctor", help="check the environment")
    _add_common(doctor_parser)
    doctor_parser.set_defaults(func=cmd_doctor)

    install_parser = subparsers.add_parser(
        "install", help="install the skill and IDE pointers"
    )
    _add_scope(install_parser)
    install_parser.add_argument(
        "--ide",
        default=None,
        metavar="LIST",
        help="comma-separated IDE names or 'all'",
    )
    install_parser.add_argument(
        "--dry-run", action="store_true", help="show actions without writing"
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="create missing IDE configuration directories",
    )
    _add_quiet(install_parser)
    install_parser.set_defaults(func=cmd_install)

    uninstall_parser = subparsers.add_parser(
        "uninstall", help="remove TokenForge-marked content"
    )
    _add_scope(uninstall_parser)
    uninstall_parser.add_argument(
        "--dry-run", action="store_true", help="show actions without writing"
    )
    _add_quiet(uninstall_parser)
    uninstall_parser.set_defaults(func=cmd_uninstall)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point."""
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    handler = getattr(args, "func", None)
    if handler is None:
        parser.print_help()
        return 2

    try:
        return int(handler(args))
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        return 130
    except BrokenPipeError:
        return 0
    except (registry.RegistryError, memory.MemoryError, OSError) as exc:
        return _fail(str(exc), 1)
    except Exception as exc:  # pragma: no cover - defensive
        if os.environ.get("TOKENFORGE_DEBUG"):
            raise
        return _fail("{}: {}".format(type(exc).__name__, exc), 1)


if __name__ == "__main__":
    raise SystemExit(main())
