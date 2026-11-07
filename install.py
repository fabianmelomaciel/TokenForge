#!/usr/bin/env python3
"""Thin bootstrap for TokenForge.

Usage:
    python install.py [--global|--project] [--ide LIST|all] [--dry-run] [--force]

This file adds the repository root to ``sys.path`` so the ``forge`` package can
be imported without installing the project, then delegates to
``forge.installer.main``.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from forge.installer import main  # noqa: E402  (path bootstrap must run first)


if __name__ == "__main__":
    raise SystemExit(main())
