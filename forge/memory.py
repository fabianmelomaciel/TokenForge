"""Persistent JSON memory with atomic writes, locking and deduplication.

The memory file stores metadata only. It never stores prompts, stdout, stderr,
environment variables or file contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from .stats import (
    DEFAULT_AVG_TOKENS_MANUAL,
    avg_tokens_for_handle,
    estimate_tokens_saved,
)

MEMORY_VERSION = 1
CONFIG_VERSION = 1
STALE_LOCK_SECONDS = 10.0
LOCK_ATTEMPTS = 50
LOCK_DELAY_SECONDS = 0.02
MAX_TASK_LENGTH = 4000


class MemoryError(RuntimeError):
    """Raised when persistent memory cannot be read or written safely."""


class MemoryLockError(MemoryError):
    """Raised when the cross-process memory lock cannot be acquired."""


class DuplicateHandleError(MemoryError):
    """Raised when a handle name is already registered."""


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with second precision."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_task(text: str) -> str:
    """Collapse whitespace and strip a task description."""
    return " ".join(str(text or "").split())


def task_signature(task: str) -> str:
    """Return a stable SHA-256 signature for a normalized task."""
    normalized = normalize_task(task).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def forge_path(project_dir: Path) -> Path:
    """Return the ``.forge`` directory for a project."""
    return Path(project_dir) / ".forge"


def handles_path(project_dir: Path) -> Path:
    """Return the handle directory for a project."""
    return forge_path(project_dir) / "handles"


def memory_path(project_dir: Path) -> Path:
    """Return the memory JSON path for a project."""
    return forge_path(project_dir) / "memory.json"


def config_path(project_dir: Path) -> Path:
    """Return the config JSON path for a project."""
    return forge_path(project_dir) / "config.json"


def lock_path(project_dir: Path) -> Path:
    """Return the memory lock path for a project."""
    return forge_path(project_dir) / "memory.lock"


def default_memory(project_dir: Path) -> Dict[str, Any]:
    """Build a fresh memory document."""
    project = Path(project_dir)
    return {
        "version": MEMORY_VERSION,
        "project": project.name or "project",
        "created": utc_now(),
        "handles": {},
    }


def default_config() -> Dict[str, Any]:
    """Build a fresh config document."""
    return {
        "version": CONFIG_VERSION,
        "avg_tokens_manual": DEFAULT_AVG_TOKENS_MANUAL,
        "default_timeout": 120,
        "handles": {},
    }


def atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` as UTF-8/LF to ``path`` using a temp file + os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = -1
    tmp_name = ""
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix="." + path.name + ".",
            suffix=".tmp",
            dir=str(path.parent),
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, str(path))
        tmp_name = ""
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _dump_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def save_memory(project_dir: Path, data: Dict[str, Any]) -> None:
    """Atomically persist a memory document."""
    atomic_write_text(memory_path(project_dir), _dump_json(data))


def save_config(project_dir: Path, data: Dict[str, Any]) -> None:
    """Atomically persist a config document."""
    atomic_write_text(config_path(project_dir), _dump_json(data))


def _backup_corrupt(path: Path) -> Path:
    """Move a corrupt JSON file aside and return the backup path."""
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = path.with_name("{}.corrupt.{}{}".format(path.stem, stamp, path.suffix))
    counter = 1
    while backup.exists():
        backup = path.with_name(
            "{}.corrupt.{}-{}{}".format(path.stem, stamp, counter, path.suffix)
        )
        counter += 1
    os.replace(str(path), str(backup))
    return backup


def _default_warn(message: str) -> None:
    sys.stderr.write("warning: {}\n".format(message))


def inspect_memory(project_dir: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read memory without modifying anything.

    Returns:
        ``(data, None)`` when valid, ``(None, reason)`` otherwise.
    """
    path = memory_path(project_dir)
    if not path.is_file():
        return None, "missing"
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError) as exc:
        return None, "unreadable: {}".format(exc)
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: {}".format(exc)
    if not isinstance(data, dict):
        return None, "memory root must be a JSON object"
    if not isinstance(data.get("handles"), dict):
        return None, "memory must contain a 'handles' object"
    return data, None


def load_memory(
    project_dir: Path, warn: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """Load memory, backing up and replacing corrupt JSON with a fresh file."""
    warn = warn or _default_warn
    path = memory_path(project_dir)
    if not path.is_file():
        return default_memory(project_dir)

    data, error = inspect_memory(project_dir)
    if error is None and data is not None:
        data.setdefault("version", MEMORY_VERSION)
        data.setdefault("project", Path(project_dir).name or "project")
        data.setdefault("created", utc_now())
        return data

    backup = _backup_corrupt(path)
    warn(
        "memory.json was corrupt ({}); moved to {} and started fresh".format(
            error, backup.name
        )
    )
    fresh = default_memory(project_dir)
    save_memory(project_dir, fresh)
    return fresh


def inspect_config(project_dir: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read config without modifying anything."""
    path = config_path(project_dir)
    if not path.is_file():
        return None, "missing"
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError) as exc:
        return None, "unreadable: {}".format(exc)
    except json.JSONDecodeError as exc:
        return None, "invalid JSON: {}".format(exc)
    if not isinstance(data, dict):
        return None, "config root must be a JSON object"
    return data, None


def load_config(
    project_dir: Path, warn: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    """Load config, backing up and replacing corrupt JSON with defaults."""
    warn = warn or _default_warn
    path = config_path(project_dir)
    if not path.is_file():
        return default_config()

    data, error = inspect_config(project_dir)
    if error is None and data is not None:
        merged = default_config()
        merged.update(data)
        if not isinstance(merged.get("handles"), dict):
            merged["handles"] = {}
        return merged

    backup = _backup_corrupt(path)
    warn(
        "config.json was corrupt ({}); moved to {} and started fresh".format(
            error, backup.name
        )
    )
    fresh = default_config()
    save_config(project_dir, fresh)
    return fresh


@contextmanager
def locked(project_dir: Path) -> Iterator[None]:
    """Acquire the cross-process memory lock.

    Uses ``O_CREAT | O_EXCL`` on ``.forge/memory.lock`` with ~50 retries at
    20 ms. A lock older than 10 seconds is considered stale and removed.
    """
    directory = forge_path(project_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = lock_path(project_dir)
    fd = -1
    acquired = False

    for attempt in range(LOCK_ATTEMPTS):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, "{}\n".format(os.getpid()).encode("ascii"))
            except OSError:
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = -1
                try:
                    path.unlink()
                except OSError:
                    pass
                raise
            acquired = True
            break
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
                if age > STALE_LOCK_SECONDS:
                    path.unlink()
                    continue
            except OSError:
                pass
            if attempt < LOCK_ATTEMPTS - 1:
                time.sleep(LOCK_DELAY_SECONDS)

    if not acquired:
        raise MemoryLockError(
            "could not acquire {} after {} attempts; remove it if no other "
            "TokenForge process is running".format(path, LOCK_ATTEMPTS)
        )

    try:
        yield
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            path.unlink()
        except OSError:
            pass


def init_project(project_dir: Path) -> Dict[str, Any]:
    """Create ``.forge/``, memory, config and handles directory idempotently."""
    project = Path(project_dir).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    forge = forge_path(project)
    handles = handles_path(project)
    created = []

    if not forge.is_dir():
        forge.mkdir(parents=True)
        created.append(str(forge))
    if not handles.is_dir():
        handles.mkdir(parents=True)
        created.append(str(handles))

    config_file = config_path(project)
    if not config_file.is_file():
        save_config(project, default_config())
        created.append(str(config_file))
    else:
        load_config(project)

    memory_file = memory_path(project)
    if not memory_file.is_file():
        save_memory(project, default_memory(project))
        created.append(str(memory_file))
    else:
        load_memory(project)

    return {
        "project": str(project),
        "forge": str(forge),
        "created": created,
        "initialized": bool(created),
    }


def build_handle_meta(
    name: str,
    task: str,
    path_rel: str,
    tags: Optional[list] = None,
    danger: bool = False,
    avg_tokens_manual: Optional[int] = None,
) -> Dict[str, Any]:
    """Build a memory entry for a new handle."""
    normalized = normalize_task(task)
    if len(normalized) > MAX_TASK_LENGTH:
        raise MemoryError(
            "task description is too long ({} characters, max {})".format(
                len(normalized), MAX_TASK_LENGTH
            )
        )
    return {
        "name": name,
        "task": normalized,
        "signature": task_signature(normalized),
        "path": path_rel,
        "created": utc_now(),
        "last_used": None,
        "uses": 0,
        "tokens_saved_est": 0,
        "avg_tokens_manual": int(avg_tokens_manual or DEFAULT_AVG_TOKENS_MANUAL),
        "tags": list(tags or []),
        "danger": bool(danger),
    }


def register_handle(
    project_dir: Path,
    name: str,
    task: str,
    path_rel: str,
    tags: Optional[list] = None,
    danger: bool = False,
    avg_tokens_manual: Optional[int] = None,
) -> Dict[str, Any]:
    """Register a handle in memory under the cross-process lock."""
    with locked(project_dir):
        data = load_memory(project_dir)
        if name in data["handles"]:
            raise DuplicateHandleError("handle '{}' is already registered".format(name))
        meta = build_handle_meta(
            name, task, path_rel, tags, danger, avg_tokens_manual
        )
        data["handles"][name] = meta
        save_memory(project_dir, data)
    return meta


def update_usage(project_dir: Path, name: str, success: bool = True) -> Dict[str, Any]:
    """Increment usage counters after a successful run."""
    with locked(project_dir):
        data = load_memory(project_dir)
        meta = data["handles"].get(name)
        if not isinstance(meta, dict):
            raise MemoryError("handle '{}' is not registered".format(name))
        if success:
            uses = int(meta.get("uses", 0) or 0) + 1
            meta_avg = int(meta.get("avg_tokens_manual") or DEFAULT_AVG_TOKENS_MANUAL)
            avg = avg_tokens_for_handle(load_config(project_dir), name, meta_avg)
            meta["uses"] = uses
            meta["last_used"] = utc_now()
            meta["avg_tokens_manual"] = avg
            meta["tokens_saved_est"] = estimate_tokens_saved(uses, avg)
            save_memory(project_dir, data)
    return meta


def remove_handle(project_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    """Remove a handle entry from memory, returning the removed entry."""
    with locked(project_dir):
        data = load_memory(project_dir)
        meta = data["handles"].pop(name, None)
        if meta is not None:
            save_memory(project_dir, data)
    return meta


def get_handle(project_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    """Return one handle's metadata, or ``None``."""
    data = load_memory(project_dir)
    meta = data["handles"].get(name)
    return meta if isinstance(meta, dict) else None


def find_by_signature(project_dir: Path, signature: str) -> Optional[str]:
    """Return the name of an existing handle with the same task signature."""
    data = load_memory(project_dir)
    for name, meta in data["handles"].items():
        if isinstance(meta, dict) and meta.get("signature") == signature:
            return name
    return None


def set_handle_avg_tokens(project_dir: Path, name: str, value: int) -> None:
    """Persist a per-handle token assumption in ``config.json``."""
    with locked(project_dir):
        config = load_config(project_dir)
        handles = config.setdefault("handles", {})
        if not isinstance(handles, dict):
            handles = {}
            config["handles"] = handles
        entry = handles.setdefault(name, {})
        if not isinstance(entry, dict):
            entry = {}
            handles[name] = entry
        entry["avg_tokens_manual"] = int(value)
        save_config(project_dir, config)


def remove_handle_config(project_dir: Path, name: str) -> None:
    """Drop per-handle overrides from ``config.json``."""
    config = load_config(project_dir)
    handles = config.get("handles")
    if isinstance(handles, dict) and name in handles:
        del handles[name]
        save_config(project_dir, config)


def avg_tokens(project_dir: Path, name: str) -> int:
    """Resolve the configured manual-token assumption for a handle."""
    return avg_tokens_for_handle(load_config(project_dir), name)
