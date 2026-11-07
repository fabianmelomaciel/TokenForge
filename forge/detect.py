"""Operating system and IDE detection.

Detection is intentionally conservative: an IDE is considered present when its
well-known configuration directory exists. The installer uses the same paths
and only creates them when ``--force`` is supplied.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

IDE_ORDER: Tuple[str, ...] = (
    "opencode",
    "claude",
    "cursor",
    "windsurf",
    "codex",
    "gemini",
    "copilot",
)

# Directories that identify an IDE inside a project.
PROJECT_MARKERS: Dict[str, str] = {
    "opencode": ".opencode",
    "claude": ".claude",
    "cursor": ".cursor",
    "windsurf": ".windsurf",
    "copilot": ".github",
}


def os_name() -> str:
    """Return a human-readable operating system name."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def home_dir() -> Path:
    """Return the current user's home directory."""
    return Path.home()


def appdata_dir() -> Optional[Path]:
    """Return ``%APPDATA%`` on Windows, or ``None`` elsewhere."""
    value = os.environ.get("APPDATA")
    if value:
        return Path(value)
    return None


def opencode_config_dirs() -> List[Path]:
    """Return existing opencode configuration directories.

    On Windows opencode may live in ``%APPDATA%\\opencode`` or in
    ``~/.config/opencode``. Both are returned when they exist.
    """
    found: List[Path] = []
    primary = home_dir() / ".config" / "opencode"
    if primary.is_dir():
        found.append(primary)
    appdata = appdata_dir()
    if appdata is not None:
        alternate = appdata / "opencode"
        if alternate.is_dir() and alternate not in found:
            found.append(alternate)
    return found


def global_ide_paths() -> Dict[str, List[Path]]:
    """Return existing global configuration roots for every known IDE."""
    home = home_dir()
    return {
        "opencode": opencode_config_dirs(),
        "claude": [home / ".claude"] if (home / ".claude").is_dir() else [],
        "cursor": [home / ".cursor"] if (home / ".cursor").is_dir() else [],
        "windsurf": (
            [home / ".codeium" / "windsurf"]
            if (home / ".codeium" / "windsurf").is_dir()
            else []
        ),
        "codex": [home / ".codex"] if (home / ".codex").is_dir() else [],
        "gemini": [home / ".gemini"] if (home / ".gemini").is_dir() else [],
        "copilot": [],
    }


def project_ide_paths(project_dir: Optional[Path] = None) -> Dict[str, Path]:
    """Return project-local IDE markers that exist."""
    root = Path(project_dir) if project_dir is not None else Path.cwd()
    found: Dict[str, Path] = {}
    for ide, relative in PROJECT_MARKERS.items():
        candidate = root / relative
        if candidate.is_dir():
            found[ide] = candidate
    return found


def detect_ides(project_dir: Optional[Path] = None) -> List[str]:
    """Return IDE names detected globally or in ``project_dir``."""
    found = {
        ide for ide, paths in global_ide_paths().items() if paths
    }
    found.update(project_ide_paths(project_dir))
    return [ide for ide in IDE_ORDER if ide in found]


def detection_details(project_dir: Optional[Path] = None) -> List[Tuple[str, str, str]]:
    """Return ``(ide, scope, path)`` tuples for doctor output."""
    details: List[Tuple[str, str, str]] = []
    for ide, paths in global_ide_paths().items():
        for path in paths:
            details.append((ide, "global", str(path)))
    for ide, path in project_ide_paths(project_dir).items():
        details.append((ide, "project", str(path)))
    return details


def parse_ide_list(value: Optional[str]) -> List[str]:
    """Parse ``--ide`` (comma-separated names or ``all``).

    Raises:
        ValueError: if an unknown IDE name is supplied.
    """
    if value is None or not value.strip() or value.strip().lower() == "all":
        return list(IDE_ORDER)

    names: List[str] = []
    unknown: List[str] = []
    for raw in value.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name not in IDE_ORDER:
            unknown.append(name)
        elif name not in names:
            names.append(name)
    if unknown:
        raise ValueError(
            "unknown IDE name(s): {}. Valid names: {} or all".format(
                ", ".join(unknown), ", ".join(IDE_ORDER)
            )
        )
    return names
