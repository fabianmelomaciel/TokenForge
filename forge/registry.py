"""Handle naming, path validation and file lifecycle."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import memory, redact, template
from .stats import avg_tokens_for_handle
from .version import VERSION

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}\Z")
_TAG_CLEAN = re.compile(r"[^a-z0-9_-]")


class RegistryError(RuntimeError):
    """Raised for invalid names, unsafe paths or duplicate handles."""


def validate_name(name: str) -> str:
    """Validate and return a handle name.

    Names must match ``^[a-z][a-z0-9_-]{0,63}$``.
    """
    value = str(name or "")
    if not NAME_PATTERN.match(value):
        raise RegistryError(
            "invalid handle name {!r}: use 1-64 characters, start with a "
            "lowercase letter, then lowercase letters, digits, '_' or '-'".format(
                value
            )
        )
    return value


def clean_tags(tags: Optional[List[str]]) -> List[str]:
    """Normalize tags to lowercase slugs, preserving order and removing dupes."""
    result: List[str] = []
    for raw in tags or []:
        tag = str(raw).strip().lower().replace(" ", "-")
        tag = _TAG_CLEAN.sub("", tag).strip("-_")
        if tag and tag not in result:
            result.append(tag[:32])
    return result[:20]


def _contained_paths(project_dir: Path, name: str) -> Tuple[Path, Path]:
    """Return ``(candidate, realpath)`` after containment validation."""
    project = Path(project_dir).expanduser().resolve()
    validate_name(name)
    forge = memory.forge_path(project)
    if forge.is_symlink():
        raise RegistryError(".forge is a symlink; refusing to follow it")
    base = memory.handles_path(project)
    if base.is_symlink():
        raise RegistryError(
            "handles directory is a symlink; refusing to follow it"
        )
    candidate = base / "{}.py".format(name)

    base_real = os.path.realpath(str(base))
    candidate_real = os.path.realpath(str(candidate))
    base_compare = os.path.normcase(base_real)
    candidate_compare = os.path.normcase(candidate_real)

    try:
        common = os.path.commonpath([base_compare, candidate_compare])
    except ValueError:
        raise RegistryError(
            "handle path for '{}' resolves to a different drive".format(name)
        )
    if common != base_compare or candidate_compare == base_compare:
        raise RegistryError(
            "handle path for '{}' escapes {}/".format(name, base)
        )
    return candidate, Path(candidate_real)


def resolve_handle_path(
    project_dir: Path, name: str, must_exist: bool = False
) -> Path:
    """Return the real path of a handle file, blocking traversal and escapes."""
    _, real = _contained_paths(project_dir, name)
    if must_exist and not real.is_file():
        raise RegistryError(
            "handle file not found for '{}': {}".format(name, real)
        )
    return real


def handle_file_path(project_dir: Path, name: str) -> Path:
    """Return the registry path (the symlink itself, not its target)."""
    candidate, _ = _contained_paths(project_dir, name)
    return candidate


def handle_relative_path(name: str) -> str:
    """Return the portable relative path stored in memory."""
    return ".forge/handles/{}.py".format(name)


def create_handle(
    project_dir: Path,
    name: str,
    task: str,
    tags: Optional[List[str]] = None,
    danger: bool = False,
    avg_tokens_manual: Optional[int] = None,
) -> Tuple[Dict[str, Any], Path]:
    """Forge a new handle, write it atomically and register it in memory."""
    project = Path(project_dir).expanduser().resolve()
    name = validate_name(name)

    cleaned_task = memory.normalize_task(redact.redact(task))
    if not cleaned_task:
        raise RegistryError("task description is required")
    if len(cleaned_task) > memory.MAX_TASK_LENGTH:
        raise RegistryError(
            "task description is too long ({} characters, max {})".format(
                len(cleaned_task), memory.MAX_TASK_LENGTH
            )
        )

    cleaned_tags = clean_tags(tags)
    forge = memory.forge_path(project)
    if forge.is_symlink():
        raise RegistryError(".forge is a symlink; refusing to follow it")
    handles = memory.handles_path(project)
    if handles.is_symlink():
        raise RegistryError(
            "handles directory is a symlink; refusing to follow it"
        )
    handles.mkdir(parents=True, exist_ok=True)
    candidate, real = _contained_paths(project, name)

    config = memory.load_config(project)
    if avg_tokens_manual is not None:
        try:
            avg = int(avg_tokens_manual)
        except (TypeError, ValueError):
            raise RegistryError("--avg-tokens must be a positive integer")
        if avg <= 0:
            raise RegistryError("--avg-tokens must be a positive integer")
    else:
        avg = avg_tokens_for_handle(config, name)

    signature = memory.task_signature(cleaned_task)
    source = template.render_handle(
        name=name,
        task=cleaned_task,
        tags=cleaned_tags,
        danger=danger,
        signature=signature,
        version=VERSION,
    )

    try:
        compile(source, str(candidate), "exec")
    except SyntaxError as exc:
        raise RegistryError("internal template error: {}".format(exc))

    with memory.locked(project):
        data = memory.load_memory(project)
        if name in data["handles"]:
            raise memory.DuplicateHandleError(
                "handle '{}' is already registered".format(name)
            )
        if candidate.exists() or real.exists():
            raise RegistryError(
                "handle file already exists: {}".format(handle_relative_path(name))
            )
        try:
            memory.atomic_write_text(candidate, source)
            meta = memory.build_handle_meta(
                name=name,
                task=cleaned_task,
                path_rel=handle_relative_path(name),
                tags=cleaned_tags,
                danger=danger,
                avg_tokens_manual=avg,
            )
            data["handles"][name] = meta
            memory.save_memory(project, data)
        except Exception:
            try:
                if candidate.exists() and not candidate.is_symlink():
                    candidate.unlink()
            except OSError:
                pass
            raise

    if avg_tokens_manual is not None:
        try:
            memory.set_handle_avg_tokens(project, name, avg)
        except (memory.MemoryError, OSError):
            # The memory entry already carries the value; config.json is only an
            # optional override, so a failed config write must not fail creation.
            pass
    return meta, candidate


def delete_handle(project_dir: Path, name: str) -> Tuple[Optional[Dict[str, Any]], Path]:
    """Delete a handle file and its memory entry."""
    project = Path(project_dir).expanduser().resolve()
    name = validate_name(name)
    candidate, real = _contained_paths(project, name)

    with memory.locked(project):
        data = memory.load_memory(project)
        meta = data["handles"].pop(name, None)
        if candidate.is_symlink():
            candidate.unlink()
        elif candidate.is_file():
            candidate.unlink()
        elif candidate.exists():
            raise RegistryError("handle path is not a regular file: {}".format(candidate))
        if meta is not None:
            memory.save_memory(project, data)

    memory.remove_handle_config(project, name)
    return meta, real


def read_handle_source(project_dir: Path, name: str) -> str:
    """Return the source of a registered handle file."""
    path = resolve_handle_path(project_dir, name, must_exist=True)
    return path.read_text(encoding="utf-8")


def list_handle_files(project_dir: Path) -> Dict[str, Path]:
    """Return ``{name: path}`` for every valid handle file on disk."""
    directory = memory.handles_path(project_dir)
    if not directory.is_dir():
        return {}
    found: Dict[str, Path] = {}
    for entry in sorted(directory.iterdir()):
        if not entry.name.endswith(".py"):
            continue
        name = entry.name[:-3]
        if NAME_PATTERN.match(name):
            found[name] = entry
    return found
