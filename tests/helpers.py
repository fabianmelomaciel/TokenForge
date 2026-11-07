"""Shared fixtures and CLI helpers for the TokenForge test suite.

Standard library only. Every test works against a throw-away project created
under the system temporary directory, so the real user home is never touched.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CLI_TIMEOUT = 120
DEFAULT_PREFIX = "tokenforge-test-"


def base_env(extra=None):
    """Return an isolated environment for child Python processes."""
    env = os.environ.copy()
    entries = [str(REPO_ROOT)]
    existing = env.get("PYTHONPATH")
    if existing:
        entries.append(existing)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("PYTHONHOME", None)
    if extra:
        for key, value in extra.items():
            env[str(key)] = str(value)
    return env


def _run(command, cwd, input=None, env=None, timeout=CLI_TIMEOUT):
    kwargs = {
        "cwd": str(cwd) if cwd is not None else None,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": base_env(env),
        "timeout": timeout,
    }
    if input is None:
        # A real terminal on stdin would make confirmation prompts block.
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = input
    return subprocess.run(command, **kwargs)


def run_cli(args, cwd, input=None, env=None, timeout=CLI_TIMEOUT):
    """Run ``python -m forge ...`` with an isolated environment."""
    command = [sys.executable, "-m", "forge"]
    command.extend(str(item) for item in args)
    return _run(command, cwd, input=input, env=env, timeout=timeout)


def run_script(script, args=(), cwd=None, input=None, env=None, timeout=CLI_TIMEOUT):
    """Run a standalone Python script (a handle or an example)."""
    command = [sys.executable, str(script)]
    command.extend(str(item) for item in args)
    return _run(command, cwd, input=input, env=env, timeout=timeout)


def run_python_code(code, args=(), cwd=None, input=None, env=None, timeout=CLI_TIMEOUT):
    """Run inline Python code, useful for concurrency tests."""
    command = [sys.executable, "-c", code]
    command.extend(str(item) for item in args)
    return _run(command, cwd, input=input, env=env, timeout=timeout)


def json_stdout(result):
    """Parse a completed process' stdout as JSON, failing loudly otherwise."""
    if result.returncode != 0:
        raise AssertionError(
            "command failed with exit code {}\nstdout:\n{}\nstderr:\n{}".format(
                result.returncode, result.stdout, result.stderr
            )
        )
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise AssertionError(
            "stdout is not valid JSON ({}):\n{}".format(exc, result.stdout)
        )


def snapshot_tree(root):
    """Return relative POSIX-style paths for everything under ``root``."""
    root = Path(root)
    found = set()
    if not root.exists():
        return found
    for path in root.rglob("*"):
        found.add(path.relative_to(root).as_posix())
    return found


def temp_leftovers(directory):
    """Return names of leftover atomic-write temp files or lock files."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    return sorted(
        entry.name
        for entry in directory.iterdir()
        if entry.name.endswith(".tmp") or entry.name.endswith(".lock")
    )


def create_symlink_or_skip(testcase, target, link, target_is_directory=False):
    """Create a symlink or skip the test when the OS does not allow it."""
    try:
        os.symlink(
            str(target), str(link), target_is_directory=target_is_directory
        )
    except (OSError, NotImplementedError) as exc:
        testcase.skipTest(
            "symlinks are not permitted on this platform: {}".format(exc)
        )


class TempProject(object):
    """A disposable directory used as a TokenForge project root."""

    def __init__(self, prefix=DEFAULT_PREFIX):
        self._root = None
        self._prefix = prefix

    def __enter__(self):
        self._root = Path(tempfile.mkdtemp(prefix=self._prefix))
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.cleanup()
        return False

    def cleanup(self):
        if self._root is not None:
            shutil.rmtree(str(self._root), ignore_errors=True)
            self._root = None

    @property
    def root(self):
        if self._root is None:
            raise RuntimeError("TempProject used outside its context manager")
        return self._root

    @property
    def forge_dir(self):
        return self.root / ".forge"

    @property
    def handles_dir(self):
        return self.forge_dir / "handles"

    @property
    def memory_file(self):
        return self.forge_dir / "memory.json"

    @property
    def config_file(self):
        return self.forge_dir / "config.json"

    def cli(self, *args, **kwargs):
        return run_cli(args, self.root, **kwargs)

    def init(self):
        result = self.cli("init")
        if result.returncode != 0:
            raise AssertionError(
                "init failed with {}: {}".format(result.returncode, result.stderr)
            )
        return result

    def new(self, name, task, *extra):
        result = self.cli("new", name, "--task", task, *extra)
        if result.returncode != 0:
            raise AssertionError(
                "new {} failed with {}: {}".format(name, result.returncode, result.stderr)
            )
        return result

    def handle_file(self, name):
        return self.handles_dir / "{}.py".format(name)

    def memory(self):
        return json.loads(self.memory_file.read_text(encoding="utf-8"))

    def config(self):
        return json.loads(self.config_file.read_text(encoding="utf-8"))

    def snapshot(self):
        return snapshot_tree(self.root)


class ProjectTestCase(unittest.TestCase):
    """Base test case with a fresh temp project and a CLI shim."""

    init_project = True

    def setUp(self):
        self._temp_project = TempProject()
        self.project = self._temp_project.__enter__()
        self.addCleanup(self._temp_project.cleanup)
        if self.init_project:
            self.project.init()

    def cli(self, *args, **kwargs):
        return self.project.cli(*args, **kwargs)
