"""Tests for forge.registry: names, containment, lifecycle and search data."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from .helpers import TempProject, create_symlink_or_skip
except ImportError:  # direct discovery from inside tests/
    from helpers import TempProject, create_symlink_or_skip  # type: ignore[no-redef]

from forge import memory, registry


class ValidateNameTests(unittest.TestCase):
    VALID = [
        "a",
        "slugify",
        "count_words",
        "a1",
        "a_b",
        "a-b",
        "a1_b-c",
        "x" * 64,
    ]
    INVALID = [
        "",
        "A",
        "Abc",
        "1abc",
        "-abc",
        "_abc",
        "x" * 65,
        "a b",
        "a/b",
        "a\\b",
        "..",
        ".",
        "../evil",
        "/etc/passwd",
        "C:\\Windows\\evil",
        "a.b",
        "a\nb",
        "ábc",
        "a b c",
    ]

    def test_valid_names_are_returned_unchanged(self):
        for name in self.VALID:
            with self.subTest(name=name):
                self.assertEqual(registry.validate_name(name), name)

    def test_invalid_names_are_rejected(self):
        for name in self.INVALID:
            with self.subTest(name=name):
                with self.assertRaises(registry.RegistryError):
                    registry.validate_name(name)

    def test_trailing_newline_is_rejected(self):
        # Regression: ``$`` matches before a trailing newline, so the pattern
        # must anchor on ``\\Z`` to reject "abc\\n".
        with self.assertRaises(registry.RegistryError):
            registry.validate_name("abc\n")
        self.assertIsNone(registry.NAME_PATTERN.match("abc\n"))

    def test_clean_tags(self):
        self.assertEqual(registry.clean_tags(None), [])
        self.assertEqual(
            registry.clean_tags(["Text", "url", "text", " two words ", "ok!"]),
            ["text", "url", "two-words", "ok"],
        )
        self.assertEqual(len(registry.clean_tags(["x" * 40])[0]), 32)
        self.assertEqual(len(registry.clean_tags(["t{}".format(i) for i in range(30)])), 20)

    def test_handle_relative_path(self):
        self.assertEqual(
            registry.handle_relative_path("slugify"), ".forge/handles/slugify.py"
        )


class CreateHandleTests(unittest.TestCase):
    def setUp(self):
        self._temp_project = TempProject()
        self.project = self._temp_project.__enter__()
        self.addCleanup(self._temp_project.cleanup)
        memory.init_project(self.project.root)

    def test_create_handle_writes_file_and_metadata(self):
        meta, path = registry.create_handle(
            self.project.root,
            "slugify",
            "  Convert   text to a URL slug ",
            tags=["Text", "two words", "text"],
            danger=True,
        )

        self.assertEqual(path, self.project.handle_file("slugify"))
        self.assertTrue(path.is_file())
        compile(path.read_text(encoding="utf-8"), str(path), "exec")

        self.assertEqual(meta["name"], "slugify")
        self.assertEqual(meta["task"], "Convert text to a URL slug")
        self.assertEqual(meta["tags"], ["text", "two-words"])
        self.assertTrue(meta["danger"])
        self.assertEqual(meta["path"], ".forge/handles/slugify.py")
        self.assertEqual(meta["uses"], 0)
        self.assertIsNone(meta["last_used"])
        self.assertEqual(meta["signature"], memory.task_signature(meta["task"]))
        self.assertEqual(
            memory.get_handle(self.project.root, "slugify"), meta
        )

    def test_create_handle_redacts_task(self):
        secret = "ghp_" + "a" * 30
        meta, path = registry.create_handle(
            self.project.root, "deploy", "deploy with token {}".format(secret)
        )
        self.assertNotIn(secret, meta["task"])
        self.assertIn("[REDACTED_GITHUB_TOKEN]", meta["task"])
        self.assertNotIn(secret, path.read_text(encoding="utf-8"))

    def test_create_handle_rejects_empty_task(self):
        for task in ["", "   ", "\n\t"]:
            with self.subTest(task=repr(task)):
                with self.assertRaises(registry.RegistryError):
                    registry.create_handle(self.project.root, "empty", task)
        self.assertFalse(self.project.handle_file("empty").exists())

    def test_create_handle_rejects_oversized_task(self):
        oversized = ("word " * 900).strip()
        self.assertGreater(len(oversized), memory.MAX_TASK_LENGTH)
        with self.assertRaises(registry.RegistryError) as ctx:
            registry.create_handle(self.project.root, "big", oversized)
        self.assertIn("too long", str(ctx.exception))
        self.assertFalse(self.project.handle_file("big").exists())

    def test_create_handle_rejects_invalid_avg_tokens(self):
        for value in [0, -1]:
            with self.subTest(value=value):
                with self.assertRaises(registry.RegistryError):
                    registry.create_handle(
                        self.project.root,
                        "avg",
                        "task",
                        avg_tokens_manual=value,
                    )

    def test_create_handle_stores_avg_override(self):
        meta, _ = registry.create_handle(
            self.project.root, "avg", "task", avg_tokens_manual=1234
        )
        self.assertEqual(meta["avg_tokens_manual"], 1234)
        config = memory.load_config(self.project.root)
        self.assertEqual(config["handles"]["avg"]["avg_tokens_manual"], 1234)
        self.assertEqual(memory.avg_tokens(self.project.root, "avg"), 1234)

    def test_duplicate_name_is_refused(self):
        registry.create_handle(self.project.root, "dup", "first task")
        with self.assertRaises(memory.DuplicateHandleError):
            registry.create_handle(self.project.root, "dup", "second task")
        self.assertEqual(
            memory.get_handle(self.project.root, "dup")["task"], "first task"
        )
        self.assertIn("first task", self.project.handle_file("dup").read_text(encoding="utf-8"))

    def test_existing_file_without_memory_entry_is_refused(self):
        path = self.project.handle_file("stray")
        path.write_text("# hand-written\n", encoding="utf-8")

        with self.assertRaises(registry.RegistryError) as ctx:
            registry.create_handle(self.project.root, "stray", "task")

        self.assertIn("already exists", str(ctx.exception))
        self.assertEqual(path.read_text(encoding="utf-8"), "# hand-written\n")
        self.assertIsNone(memory.get_handle(self.project.root, "stray"))

    def test_invalid_name_is_rejected_before_writing(self):
        escape_name = self.project.root.name + "-escape"
        outside = self.project.root.parent / (escape_name + ".py")
        with self.assertRaises(registry.RegistryError):
            registry.create_handle(self.project.root, "../" + escape_name, "task")
        self.assertFalse(outside.exists())

    def test_read_handle_source(self):
        _, path = registry.create_handle(self.project.root, "readme", "read me")
        self.assertEqual(
            registry.read_handle_source(self.project.root, "readme"),
            path.read_text(encoding="utf-8"),
        )
        with self.assertRaises(registry.RegistryError):
            registry.read_handle_source(self.project.root, "missing")


class PathContainmentTests(unittest.TestCase):
    def setUp(self):
        self._temp_project = TempProject()
        self.project = self._temp_project.__enter__()
        self.addCleanup(self._temp_project.cleanup)
        memory.init_project(self.project.root)

    def test_traversal_names_are_rejected(self):
        for name in ["..", "../evil", "../../etc/passwd", "/etc/passwd", "a/../../b"]:
            with self.subTest(name=name):
                with self.assertRaises(registry.RegistryError):
                    registry.handle_file_path(self.project.root, name)
                with self.assertRaises(registry.RegistryError):
                    registry.resolve_handle_path(self.project.root, name)

    def test_resolve_missing_handle(self):
        path = registry.resolve_handle_path(
            self.project.root, "absent", must_exist=False
        )
        self.assertEqual(
            os.path.normcase(str(path.parent)),
            os.path.normcase(str(self.project.handles_dir.resolve())),
        )
        with self.assertRaises(registry.RegistryError):
            registry.resolve_handle_path(
                self.project.root, "absent", must_exist=True
            )

    def test_resolve_existing_handle(self):
        _, created = registry.create_handle(self.project.root, "hello", "say hello")
        resolved = registry.resolve_handle_path(
            self.project.root, "hello", must_exist=True
        )
        self.assertEqual(
            os.path.normcase(str(resolved)), os.path.normcase(str(created))
        )

    def test_escaping_symlink_is_blocked(self):
        outside_dir = Path(tempfile.mkdtemp(prefix="tokenforge-outside-"))
        self.addCleanup(shutil.rmtree, str(outside_dir), True)
        outside = outside_dir / "outside.py"
        outside.write_text("# outside\n", encoding="utf-8")
        link = self.project.handle_file("escape")
        create_symlink_or_skip(self, outside, link)

        with self.assertRaises(registry.RegistryError) as ctx:
            registry.resolve_handle_path(self.project.root, "escape", must_exist=True)
        self.assertIn("escapes", str(ctx.exception))

        with self.assertRaises(registry.RegistryError):
            registry.delete_handle(self.project.root, "escape")
        self.assertTrue(outside.is_file())

    def test_symlinked_forge_directory_is_refused(self):
        outside_dir = Path(tempfile.mkdtemp(prefix="tokenforge-outside-"))
        self.addCleanup(shutil.rmtree, str(outside_dir), True)
        shutil.rmtree(str(self.project.forge_dir))
        create_symlink_or_skip(
            self, outside_dir, self.project.forge_dir, target_is_directory=True
        )

        with self.assertRaises(registry.RegistryError) as ctx:
            registry.create_handle(self.project.root, "x", "task")
        self.assertIn("symlink", str(ctx.exception))
        self.assertEqual(list(outside_dir.rglob("*")), [])

    def test_symlinked_handles_directory_is_refused(self):
        outside_dir = Path(tempfile.mkdtemp(prefix="tokenforge-outside-"))
        self.addCleanup(shutil.rmtree, str(outside_dir), True)
        shutil.rmtree(str(self.project.handles_dir))
        create_symlink_or_skip(
            self, outside_dir, self.project.handles_dir, target_is_directory=True
        )

        with self.assertRaises(registry.RegistryError) as ctx:
            registry.create_handle(self.project.root, "x", "task")
        self.assertIn("symlink", str(ctx.exception))
        self.assertEqual(list(outside_dir.rglob("*")), [])


class DeleteAndListTests(unittest.TestCase):
    def setUp(self):
        self._temp_project = TempProject()
        self.project = self._temp_project.__enter__()
        self.addCleanup(self._temp_project.cleanup)
        memory.init_project(self.project.root)

    def test_delete_removes_file_and_memory_entry(self):
        meta, path = registry.create_handle(self.project.root, "gone", "delete me")

        removed, real = registry.delete_handle(self.project.root, "gone")

        self.assertEqual(removed, meta)
        self.assertFalse(path.exists())
        self.assertIsNone(memory.get_handle(self.project.root, "gone"))
        self.assertEqual(
            os.path.normcase(str(real)), os.path.normcase(str(path.resolve()))
        )

    def test_delete_missing_handle_returns_none(self):
        removed, real = registry.delete_handle(self.project.root, "missing")
        self.assertIsNone(removed)
        self.assertFalse(real.exists())

    def test_delete_removes_file_without_memory_entry(self):
        path = self.project.handle_file("orphan")
        path.write_text("# orphan\n", encoding="utf-8")

        removed, _ = registry.delete_handle(self.project.root, "orphan")

        self.assertIsNone(removed)
        self.assertFalse(path.exists())

    def test_delete_symlink_unlinks_link_not_target(self):
        real = self.project.handle_file("real")
        real.write_text("# real\n", encoding="utf-8")
        link = self.project.handle_file("alias")
        create_symlink_or_skip(self, real, link)
        memory.register_handle(
            self.project.root, "alias", "alias task", ".forge/handles/alias.py"
        )

        removed, _ = registry.delete_handle(self.project.root, "alias")

        self.assertEqual(removed["name"], "alias")
        self.assertFalse(link.exists())
        self.assertTrue(real.is_file())

    def test_list_handle_files_filters_invalid_names(self):
        handles = self.project.handles_dir
        (handles / "good.py").write_text("", encoding="utf-8")
        (handles / "also_good-1.py").write_text("", encoding="utf-8")
        (handles / "Bad.py").write_text("", encoding="utf-8")
        (handles / "1bad.py").write_text("", encoding="utf-8")
        (handles / "notes.txt").write_text("", encoding="utf-8")

        found = registry.list_handle_files(self.project.root)

        self.assertEqual(sorted(found), ["also_good-1", "good"])
        self.assertTrue(found["good"].name.endswith(".py"))

    def test_list_handle_files_without_directory(self):
        with TempProject() as project:
            self.assertEqual(registry.list_handle_files(project.root), {})


if __name__ == "__main__":
    unittest.main()
