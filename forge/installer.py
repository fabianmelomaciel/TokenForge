"""Cross-IDE, idempotent installer and uninstaller.

Only well-known IDE paths are touched. Modified files are backed up to
``<file>.tokenforge.bak`` before the first change. Symlinked targets are
refused, and ``--dry-run`` writes nothing.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import sysconfig
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import detect
from .memory import atomic_write_text
from .version import VERSION

BEGIN_MARKER = "<!-- tokenforge:begin -->"
END_MARKER = "<!-- tokenforge:end -->"
_BLOCK_RE = re.compile(
    r"<!-- tokenforge:begin -->.*?<!-- tokenforge:end -->", re.DOTALL
)

_GITHUB_SKILL_URL = (
    "https://raw.githubusercontent.com/fabianmelomaciel/TokenForge/main/SKILL.md"
)

_FALLBACK_SKILL = """---
name: tokenforge
description: Turn repeated or deterministic AI work into tested Python handles that run for ~0 tokens.
license: MIT
metadata:
  version: {version}
  author: Fabian Melomaciel
---

# TokenForge

Forge once. Run forever.

## THE PRIME RULE

Check `.forge/memory.json` first. Run an existing handle before re-deriving a
repeatable task. Only forge when the task is deterministic and repeatable.

## The loop

CHECK -> RUN -> FORGE -> REGISTER -> REUSE.

- CHECK: `python -m forge list` or `python -m forge search <query>`.
- RUN: `python -m forge run <name> --json '{{...}}'`.
- FORGE: `python -m forge new <name> --task "..."`.
- REGISTER: `new` writes metadata to `.forge/memory.json`.
- REUSE: run the handle for ~0 tokens.

## Safety

Never store secrets in task descriptions. Test a handle with
`python -m forge test <name>` before relying on it. Danger handles require
`--yes` when run non-interactively. Review every generated file.
""".format(version=VERSION)


class InstallerError(RuntimeError):
    """Raised when a target cannot be written or removed safely."""


@dataclass
class Target:
    """One file the installer knows how to manage."""

    ide: str
    kind: str  # "skill", "rule" or "pointer"
    path: Path
    scope: str
    label: str
    required_base: Optional[Path] = None
    skill_ref: Optional[str] = None


@dataclass
class Report:
    """Collected per-target install/uninstall results."""

    lines: List[str] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    failures: int = 0

    @property
    def ok(self) -> bool:
        return self.failures == 0

    def add(self, status: str, label: str, path: Path, detail: str = "") -> None:
        suffix = " ({})".format(detail) if detail else ""
        self.lines.append("  [{}] {}: {}{}".format(status, label, path, suffix))
        self.counts[status] = self.counts.get(status, 0) + 1
        if status == "fail":
            self.failures += 1

    def summary(self) -> str:
        order = ("ok", "unchanged", "rm", "dry", "skip", "fail")
        parts = [
            "{} {}".format(self.counts[key], key)
            for key in order
            if self.counts.get(key)
        ]
        return "Summary: {}.".format(", ".join(parts) if parts else "nothing to do")


def skill_text() -> str:
    """Load the canonical SKILL.md, falling back to an embedded copy."""
    root = Path(__file__).resolve().parent.parent
    candidates = [
        root / "SKILL.md",
        Path(__file__).resolve().parent / "SKILL.md",
    ]
    data_dir = sysconfig.get_path("data")
    if data_dir:
        candidates.append(Path(data_dir) / "share" / "tokenforge" / "SKILL.md")

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
    return _FALLBACK_SKILL


def pointer_block(skill_ref: str) -> str:
    """Return the canonical TokenForge pointer block."""
    return (
        "{begin}\n"
        "## TokenForge\n"
        "Before re-deriving a repeatable task, check `.forge/memory.json` and run "
        "an existing handle with `python -m forge run <name>`. If none exists, "
        "forge one: `python -m forge new <name> --task \"...\"`. Full rules: "
        "{ref}.\n"
        "{end}"
    ).format(begin=BEGIN_MARKER, ref=skill_ref, end=END_MARKER)


def upsert_block(existing: str, block: str) -> str:
    """Replace existing TokenForge block(s) or append a new one."""
    if _BLOCK_RE.search(existing):
        return _BLOCK_RE.sub(lambda _match: block, existing)
    if existing.strip():
        return existing.rstrip("\n") + "\n\n" + block + "\n"
    return block + "\n"


def remove_block(existing: str) -> str:
    """Remove every TokenForge block, returning the remaining text."""
    if not _BLOCK_RE.search(existing):
        return existing
    result = _BLOCK_RE.sub("", existing)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = result.strip("\n")
    return result + "\n" if result else ""


def _symlink_component(path: Path) -> Optional[Path]:
    """Return the first symlink in ``path`` or its existing parents."""
    current = Path(path)
    while True:
        try:
            if current.is_symlink():
                return current
        except OSError:
            pass
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _assert_safe(path: Path) -> None:
    link = _symlink_component(path)
    if link is not None:
        raise InstallerError("refusing to follow symlink: {}".format(link))


def _backup(path: Path) -> Path:
    """Back up a file once to ``<file>.tokenforge.bak``."""
    backup = Path(str(path) + ".tokenforge.bak")
    _assert_safe(backup)
    if not backup.exists():
        shutil.copy2(str(path), str(backup))
    return backup


def _cursor_rule(skill_ref: str) -> str:
    """Build a Cursor .mdc rule containing the TokenForge pointer."""
    return (
        "---\n"
        "description: TokenForge - forge once, run forever.\n"
        "globs:\n"
        "alwaysApply: false\n"
        "---\n\n" + pointer_block(skill_ref) + "\n"
    )


def _add_target(
    targets: List[Target], seen: Set[Tuple[str, str]], target: Target
) -> None:
    key = (str(target.path), target.kind)
    if key in seen:
        return
    seen.add(key)
    targets.append(target)


def _project_skill_ref(selected: Set[str]) -> str:
    if "opencode" in selected:
        return ".opencode/skills/tokenforge/SKILL.md"
    if "claude" in selected:
        return ".claude/skills/tokenforge/SKILL.md"
    return _GITHUB_SKILL_URL


def _global_skill_ref(
    selected: Set[str], opencode_dirs: List[Path], claude_available: bool
) -> str:
    if "opencode" in selected and opencode_dirs:
        return (
            opencode_dirs[0] / "skills" / "tokenforge" / "SKILL.md"
        ).as_posix()
    if "claude" in selected and claude_available:
        return (
            detect.home_dir() / ".claude" / "skills" / "tokenforge" / "SKILL.md"
        ).as_posix()
    return _GITHUB_SKILL_URL


def _build_targets(
    scope: str,
    project_dir: Path,
    selected: Set[str],
    force: bool,
) -> List[Target]:
    """Build the target list for a scope, filtered by selected IDE names."""
    targets: List[Target] = []
    seen: Set[Tuple[str, str]] = set()
    home = detect.home_dir()

    if scope == "project":
        root = Path(project_dir)
        skill_ref = _project_skill_ref(selected)

        if "opencode" in selected:
            skill = root / ".opencode" / "skills" / "tokenforge" / "SKILL.md"
            _add_target(
                targets,
                seen,
                Target(
                    "opencode",
                    "skill",
                    skill,
                    "project",
                    "opencode skill",
                    skill_ref=".opencode/skills/tokenforge/SKILL.md",
                ),
            )
            _add_target(
                targets,
                seen,
                Target(
                    "opencode",
                    "pointer",
                    root / "AGENTS.md",
                    "project",
                    "opencode AGENTS.md",
                    skill_ref=".opencode/skills/tokenforge/SKILL.md",
                ),
            )

        if "codex" in selected:
            _add_target(
                targets,
                seen,
                Target(
                    "codex",
                    "pointer",
                    root / "AGENTS.md",
                    "project",
                    "codex AGENTS.md",
                    skill_ref=skill_ref,
                ),
            )

        if "claude" in selected:
            _add_target(
                targets,
                seen,
                Target(
                    "claude",
                    "skill",
                    root / ".claude" / "skills" / "tokenforge" / "SKILL.md",
                    "project",
                    "claude skill",
                    skill_ref=".claude/skills/tokenforge/SKILL.md",
                ),
            )
            _add_target(
                targets,
                seen,
                Target(
                    "claude",
                    "pointer",
                    root / "CLAUDE.md",
                    "project",
                    "claude CLAUDE.md",
                    skill_ref=".claude/skills/tokenforge/SKILL.md",
                ),
            )

        if "cursor" in selected:
            _add_target(
                targets,
                seen,
                Target(
                    "cursor",
                    "rule",
                    root / ".cursor" / "rules" / "tokenforge.mdc",
                    "project",
                    "cursor rule",
                    skill_ref=skill_ref,
                ),
            )

        if "windsurf" in selected:
            _add_target(
                targets,
                seen,
                Target(
                    "windsurf",
                    "rule",
                    root / ".windsurf" / "rules" / "tokenforge.md",
                    "project",
                    "windsurf rule",
                    skill_ref=skill_ref,
                ),
            )

        if "gemini" in selected:
            _add_target(
                targets,
                seen,
                Target(
                    "gemini",
                    "pointer",
                    root / "GEMINI.md",
                    "project",
                    "gemini GEMINI.md",
                    skill_ref=skill_ref,
                ),
            )

        if "copilot" in selected:
            _add_target(
                targets,
                seen,
                Target(
                    "copilot",
                    "pointer",
                    root / ".github" / "copilot-instructions.md",
                    "project",
                    "copilot instructions",
                    skill_ref=skill_ref,
                ),
            )
        return targets

    # Global scope.
    existing_opencode_dirs = detect.opencode_config_dirs()
    opencode_dirs = existing_opencode_dirs or [home / ".config" / "opencode"]
    claude_available = (home / ".claude").exists() or force
    ref_opencode_dirs = existing_opencode_dirs
    if not ref_opencode_dirs and force:
        ref_opencode_dirs = [home / ".config" / "opencode"]
    global_ref = _global_skill_ref(
        selected, ref_opencode_dirs, claude_available
    )

    if "opencode" in selected:
        for base in opencode_dirs:
            skill = base / "skills" / "tokenforge" / "SKILL.md"
            _add_target(
                targets,
                seen,
                Target(
                    "opencode",
                    "skill",
                    skill,
                    "global",
                    "opencode skill",
                    required_base=base,
                    skill_ref=skill.as_posix(),
                ),
            )
            _add_target(
                targets,
                seen,
                Target(
                    "opencode",
                    "pointer",
                    base / "AGENTS.md",
                    "global",
                    "opencode AGENTS.md",
                    required_base=base,
                    skill_ref=skill.as_posix(),
                ),
            )

    if "claude" in selected:
        base = home / ".claude"
        skill = base / "skills" / "tokenforge" / "SKILL.md"
        _add_target(
            targets,
            seen,
            Target(
                "claude",
                "skill",
                skill,
                "global",
                "claude skill",
                required_base=base,
                skill_ref=skill.as_posix(),
            ),
        )
        _add_target(
            targets,
            seen,
            Target(
                "claude",
                "pointer",
                base / "CLAUDE.md",
                "global",
                "claude CLAUDE.md",
                required_base=base,
                skill_ref=skill.as_posix(),
            ),
        )

    if "cursor" in selected:
        _add_target(
            targets,
            seen,
            Target(
                "cursor",
                "rule",
                home / ".cursor" / "rules" / "tokenforge.mdc",
                "global",
                "cursor rule",
                required_base=home / ".cursor",
                skill_ref=global_ref,
            ),
        )

    if "windsurf" in selected:
        _add_target(
            targets,
            seen,
            Target(
                "windsurf",
                "pointer",
                home / ".codeium" / "windsurf" / "memories" / "global_rules.md",
                "global",
                "windsurf global rules",
                required_base=home / ".codeium" / "windsurf",
                skill_ref=global_ref,
            ),
        )

    if "codex" in selected:
        _add_target(
            targets,
            seen,
            Target(
                "codex",
                "pointer",
                home / ".codex" / "AGENTS.md",
                "global",
                "codex AGENTS.md",
                required_base=home / ".codex",
                skill_ref=global_ref,
            ),
        )

    if "gemini" in selected:
        _add_target(
            targets,
            seen,
            Target(
                "gemini",
                "pointer",
                home / ".gemini" / "GEMINI.md",
                "global",
                "gemini GEMINI.md",
                required_base=home / ".gemini",
                skill_ref=global_ref,
            ),
        )

    return targets


def _write_dedicated(
    target: Target, content: str, dry_run: bool, report: Report
) -> None:
    path = target.path
    _assert_safe(path)

    if path.exists() and not path.is_file():
        raise InstallerError("target is not a regular file: {}".format(path))

    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing == content:
            report.add("unchanged", target.label, path)
            return
        if dry_run:
            report.add("dry", target.label, path, "would update")
            return
        _backup(path)
        atomic_write_text(path, content)
        report.add("ok", target.label, path, "updated")
        return

    if dry_run:
        report.add("dry", target.label, path, "would create")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, content)
    report.add("ok", target.label, path, "created")


def _write_pointer(target: Target, dry_run: bool, report: Report) -> None:
    path = target.path
    _assert_safe(path)

    if path.exists() and not path.is_file():
        raise InstallerError("target is not a regular file: {}".format(path))

    existing = ""
    if path.is_file():
        existing = path.read_text(encoding="utf-8")

    block = pointer_block(target.skill_ref or _GITHUB_SKILL_URL)
    updated = upsert_block(existing, block)
    if updated == existing:
        report.add("unchanged", target.label, path)
        return
    if dry_run:
        report.add(
            "dry",
            target.label,
            path,
            "would update block" if path.is_file() else "would create",
        )
        return
    if path.is_file():
        _backup(path)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, updated)
    report.add("ok", target.label, path, "block written")


def _install_target(target: Target, dry_run: bool, report: Report) -> None:
    if target.kind == "skill":
        _write_dedicated(target, skill_text(), dry_run, report)
    elif target.kind == "rule":
        if target.path.suffix == ".mdc":
            content = _cursor_rule(target.skill_ref or _GITHUB_SKILL_URL)
        else:
            content = pointer_block(target.skill_ref or _GITHUB_SKILL_URL) + "\n"
        _write_dedicated(target, content, dry_run, report)
    elif target.kind == "pointer":
        _write_pointer(target, dry_run, report)
    else:
        raise InstallerError("unknown target kind: {}".format(target.kind))


def install(
    scope: str = "global",
    project_dir: Optional[Path] = None,
    ides: Optional[str] = None,
    dry_run: bool = False,
    force: bool = False,
) -> Report:
    """Install the skill and IDE pointers for one scope."""
    report = Report()
    if scope not in ("global", "project"):
        report.add("fail", "scope", Path(scope), "must be global or project")
        return report

    try:
        selected = set(detect.parse_ide_list(ides))
    except ValueError as exc:
        report.add("fail", "--ide", Path("."), str(exc))
        return report

    project = Path(project_dir or Path.cwd()).expanduser().resolve()
    report.lines.append(
        "TokenForge {} install: scope={} dry_run={} force={}".format(
            VERSION, scope, "yes" if dry_run else "no", "yes" if force else "no"
        )
    )

    targets = _build_targets(scope, project, selected, force)
    for target in targets:
        if target.required_base is not None and not target.required_base.exists():
            if target.ide == "windsurf":
                report.add(
                    "skip", target.label, target.path, "windsurf config not found"
                )
                continue
            if not force:
                report.add(
                    "skip", target.label, target.path, "not detected; use --force"
                )
                continue
        try:
            _install_target(target, dry_run, report)
        except (OSError, UnicodeError, InstallerError) as exc:
            report.add("fail", target.label, target.path, str(exc))

    if scope == "global" and "copilot" in selected:
        report.add(
            "skip",
            "copilot",
            Path(".github/copilot-instructions.md"),
            "project-only target",
        )

    report.lines.append(report.summary())
    return report


def _is_tokenforge_file(text: str) -> bool:
    lowered = text.lower()
    return (
        BEGIN_MARKER in text
        or "name: tokenforge" in lowered
        or "tokenforge" in lowered
    )


def _remove_dedicated(target: Target, dry_run: bool, report: Report) -> None:
    path = target.path
    _assert_safe(path)
    if not path.exists():
        report.add("skip", target.label, path, "not installed")
        return
    if not path.is_file():
        raise InstallerError("target is not a regular file: {}".format(path))

    text = path.read_text(encoding="utf-8")
    if not _is_tokenforge_file(text):
        report.add("skip", target.label, path, "not a TokenForge file")
        return
    if dry_run:
        report.add("dry", target.label, path, "would remove")
        return

    path.unlink()
    report.add("rm", target.label, path, "removed")
    for parent in (path.parent, path.parent.parent):
        try:
            parent.rmdir()
        except OSError:
            break


def _remove_pointer(target: Target, dry_run: bool, report: Report) -> None:
    path = target.path
    _assert_safe(path)
    if not path.is_file():
        report.add("skip", target.label, path, "not installed")
        return

    existing = path.read_text(encoding="utf-8")
    if not _BLOCK_RE.search(existing):
        report.add("skip", target.label, path, "no TokenForge block")
        return
    updated = remove_block(existing)
    if dry_run:
        report.add("dry", target.label, path, "would remove block")
        return
    if updated.strip():
        atomic_write_text(path, updated)
        report.add("ok", target.label, path, "block removed")
    else:
        path.unlink()
        report.add("rm", target.label, path, "removed empty file")


def _remove_target(target: Target, dry_run: bool, report: Report) -> None:
    if target.kind == "pointer":
        _remove_pointer(target, dry_run, report)
    else:
        _remove_dedicated(target, dry_run, report)


def uninstall(
    scope: str = "global",
    project_dir: Optional[Path] = None,
    dry_run: bool = False,
) -> Report:
    """Remove only TokenForge-marked content from one scope."""
    report = Report()
    if scope not in ("global", "project"):
        report.add("fail", "scope", Path(scope), "must be global or project")
        return report

    project = Path(project_dir or Path.cwd()).expanduser().resolve()
    report.lines.append(
        "TokenForge {} uninstall: scope={} dry_run={}".format(
            VERSION, scope, "yes" if dry_run else "no"
        )
    )

    targets = _build_targets(scope, project, set(detect.IDE_ORDER), force=True)
    for target in targets:
        if target.required_base is not None and not target.required_base.exists():
            report.add("skip", target.label, target.path, "not installed")
            continue
        try:
            _remove_target(target, dry_run, report)
        except (OSError, UnicodeError, InstallerError) as exc:
            report.add("fail", target.label, target.path, str(exc))

    report.lines.append(report.summary())
    return report


def main(argv: Optional[List[str]] = None) -> int:
    """Standalone entry point used by ``install.py``."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install the TokenForge skill and IDE pointers.",
    )
    scope_group = parser.add_mutually_exclusive_group()
    scope_group.add_argument(
        "--global",
        dest="scope",
        action="store_const",
        const="global",
        help="install for the current user (default)",
    )
    scope_group.add_argument(
        "--project",
        dest="scope",
        action="store_const",
        const="project",
        help="install into the current project",
    )
    parser.set_defaults(scope="global")
    parser.add_argument(
        "--ide",
        default=None,
        metavar="LIST",
        help="comma-separated IDE names or 'all'",
    )
    parser.add_argument("--dry-run", action="store_true", help="write nothing")
    parser.add_argument(
        "--force", action="store_true", help="create missing IDE config directories"
    )
    args = parser.parse_args(argv)

    report = install(
        scope=args.scope,
        project_dir=Path.cwd(),
        ides=args.ide,
        dry_run=args.dry_run,
        force=args.force,
    )
    for line in report.lines:
        print(line)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
