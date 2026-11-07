"""Tests for forge.memory: init, atomic writes, recovery, locking, usage."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest

try:
    from .helpers import (
        TempProject,
        base_env,
        temp_leftovers,
    )
except ImportError:  # direct discovery from inside tests/
    from helpers import (  # type: ignore[no-redef]
        TempProject,
        base_env,
        temp_leftovers,
    )

from forge import memory, redact, registry, stats


class InitProjectTests(unittest.TestCase):
    def test_init_creates_layout(self):
        with TempProject() as project:
            result = memory.init_project(project.root)

            self.assertTrue(project.forge_dir.is_dir())
            self.assertTrue(project.handles_dir.is_dir())
            self.assertTrue(project.memory_file.is_file())
            self.assertTrue(project.config_file.is_file())
            self.assertTrue(result["initialized"])
            self.assertEqual(len(result["created"]), 4)

            data = json.loads(project.memory_file.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], memory.MEMORY_VERSION)
            self.assertEqual(data["handles"], {})
            self.assertEqual(data["project"], project.root.name)

            config = json.loads(project.config_file.read_text(encoding="utf-8"))
            self.assertEqual(config["version"], memory.CONFIG_VERSION)
            self.assertEqual(config["avg_tokens_manual"], stats.DEFAULT_AVG_TOKENS_MANUAL)
            self.assertEqual(config["default_timeout"], 120)

    def test_init_is_idempotent_and_keeps_existing_memory(self):
        with TempProject() as project:
            memory.init_project(project.root)
            memory.register_handle(
                project.root, "keep", "keep this task", ".forge/handles/keep.py"
            )

            second = memory.init_project(project.root)

            self.assertFalse(second["initialized"])
            self.assertEqual(second["created"], [])
            self.assertIsNotNone(memory.get_handle(project.root, "keep"))
            self.assertEqual(memory.get_handle(project.root, "keep")["task"], "keep this task")

    def test_default_documents(self):
        with TempProject() as project:
            fresh = memory.default_memory(project.root)
            self.assertEqual(fresh["handles"], {})
            self.assertEqual(fresh["project"], project.root.name)
            self.assertIn("created", fresh)

            config = memory.default_config()
            self.assertEqual(config["avg_tokens_manual"], 1500)
            self.assertEqual(config["default_timeout"], 120)
            self.assertEqual(config["handles"], {})

    def test_paths_are_under_forge(self):
        with TempProject() as project:
            self.assertEqual(memory.forge_path(project.root), project.root / ".forge")
            self.assertEqual(
                memory.handles_path(project.root), project.root / ".forge" / "handles"
            )
            self.assertEqual(
                memory.memory_path(project.root),
                project.root / ".forge" / "memory.json",
            )
            self.assertEqual(
                memory.config_path(project.root),
                project.root / ".forge" / "config.json",
            )
            self.assertEqual(
                memory.lock_path(project.root), project.root / ".forge" / "memory.lock"
            )


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_replaces_content_without_temp_files(self):
        with TempProject() as project:
            target = project.root / "nested" / "note.txt"
            memory.atomic_write_text(target, "first\n")
            memory.atomic_write_text(target, "second\n")

            self.assertEqual(target.read_text(encoding="utf-8"), "second\n")
            self.assertEqual(temp_leftovers(target.parent), [])

    def test_atomic_write_uses_lf_newlines(self):
        with TempProject() as project:
            target = project.root / "lines.txt"
            memory.atomic_write_text(target, "a\nb\nc\n")
            raw = target.read_bytes()
            self.assertNotIn(b"\r\n", raw)
            self.assertEqual(raw, b"a\nb\nc\n")

    def test_atomic_write_preserves_unicode(self):
        with TempProject() as project:
            target = project.root / "unicode.txt"
            text = "ñandú ✓ 日本語\n"
            memory.atomic_write_text(target, text)
            self.assertEqual(target.read_text(encoding="utf-8"), text)

    def test_save_memory_is_atomic_and_valid(self):
        with TempProject() as project:
            memory.init_project(project.root)
            data = memory.load_memory(project.root)
            data["handles"]["x"] = memory.build_handle_meta(
                "x", "task", ".forge/handles/x.py"
            )
            memory.save_memory(project.root, data)

            self.assertEqual(temp_leftovers(project.forge_dir), [])
            reloaded = json.loads(project.memory_file.read_text(encoding="utf-8"))
            self.assertIn("x", reloaded["handles"])


class CorruptJsonTests(unittest.TestCase):
    def test_corrupt_memory_is_backed_up_and_reset(self):
        with TempProject() as project:
            memory.init_project(project.root)
            project.memory_file.write_text("{ this is not json", encoding="utf-8")
            warnings = []

            data = memory.load_memory(project.root, warn=warnings.append)

            self.assertEqual(data["handles"], {})
            self.assertEqual(len(warnings), 1)
            self.assertIn("corrupt", warnings[0])
            backups = sorted(project.forge_dir.glob("memory.corrupt.*.json"))
            self.assertEqual(len(backups), 1)
            self.assertIn(
                "this is not json", backups[0].read_text(encoding="utf-8")
            )
            fresh = json.loads(project.memory_file.read_text(encoding="utf-8"))
            self.assertEqual(fresh["handles"], {})

    def test_corrupt_config_is_backed_up_and_reset(self):
        with TempProject() as project:
            memory.init_project(project.root)
            project.config_file.write_text("[]", encoding="utf-8")
            warnings = []

            config = memory.load_config(project.root, warn=warnings.append)

            self.assertEqual(config["avg_tokens_manual"], 1500)
            self.assertEqual(len(warnings), 1)
            self.assertIn("config.json was corrupt", warnings[0])
            backups = sorted(project.forge_dir.glob("config.corrupt.*.json"))
            self.assertEqual(len(backups), 1)

    def test_non_object_memory_root_is_corrupt(self):
        with TempProject() as project:
            memory.init_project(project.root)
            project.memory_file.write_text("[1, 2, 3]", encoding="utf-8")
            data, error = memory.inspect_memory(project.root)
            self.assertIsNone(data)
            self.assertIn("JSON object", error or "")

    def test_handles_must_be_an_object(self):
        with TempProject() as project:
            memory.init_project(project.root)
            project.memory_file.write_text('{"handles": []}', encoding="utf-8")
            data, error = memory.inspect_memory(project.root)
            self.assertIsNone(data)
            self.assertIn("handles", error or "")

    def test_inspect_memory_missing_file(self):
        with TempProject() as project:
            data, error = memory.inspect_memory(project.root)
            self.assertIsNone(data)
            self.assertEqual(error, "missing")

    def test_load_memory_does_not_write_when_missing(self):
        with TempProject() as project:
            data = memory.load_memory(project.root)
            self.assertEqual(data["handles"], {})
            self.assertFalse(project.memory_file.exists())

    def test_load_config_does_not_write_when_missing(self):
        with TempProject() as project:
            config = memory.load_config(project.root)
            self.assertEqual(config["avg_tokens_manual"], 1500)
            self.assertFalse(project.config_file.exists())


class SignatureTests(unittest.TestCase):
    def test_signature_is_whitespace_and_case_stable(self):
        first = memory.task_signature("  Convert   Text to a URL Slug ")
        second = memory.task_signature("convert text to a url slug")

        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertNotEqual(first, memory.task_signature("convert text to a file slug"))

    def test_normalize_task_collapses_whitespace(self):
        self.assertEqual(
            memory.normalize_task("  a \n\t b   c  "), "a b c"
        )
        self.assertEqual(memory.normalize_task(None), "")
        self.assertEqual(memory.normalize_task(""), "")


class LockTests(unittest.TestCase):
    def test_lock_creates_forge_and_is_released(self):
        with TempProject() as project:
            lock = memory.lock_path(project.root)
            with memory.locked(project.root):
                self.assertTrue(lock.is_file())
            self.assertFalse(lock.exists())

    def test_lock_is_released_when_body_raises(self):
        with TempProject() as project:
            memory.init_project(project.root)
            lock = memory.lock_path(project.root)
            with self.assertRaises(RuntimeError):
                with memory.locked(project.root):
                    raise RuntimeError("boom")
            self.assertFalse(lock.exists())

    def test_stale_lock_is_broken(self):
        with TempProject() as project:
            memory.init_project(project.root)
            lock = memory.lock_path(project.root)
            lock.write_text("12345\n", encoding="ascii")
            old = time.time() - (memory.STALE_LOCK_SECONDS + 60)
            os.utime(str(lock), (old, old))

            with memory.locked(project.root):
                self.assertTrue(lock.exists())

            self.assertFalse(lock.exists())

    def test_fresh_lock_blocks_and_times_out(self):
        with TempProject() as project:
            memory.init_project(project.root)
            lock = memory.lock_path(project.root)
            lock.write_text("999999\n", encoding="ascii")

            attempts = memory.LOCK_ATTEMPTS
            delay = memory.LOCK_DELAY_SECONDS
            memory.LOCK_ATTEMPTS = 3
            memory.LOCK_DELAY_SECONDS = 0.01
            try:
                with self.assertRaises(memory.MemoryLockError):
                    with memory.locked(project.root):
                        self.fail("lock should not have been acquired")
            finally:
                memory.LOCK_ATTEMPTS = attempts
                memory.LOCK_DELAY_SECONDS = delay
                try:
                    lock.unlink()
                except OSError:
                    pass

    def test_memory_lock_error_is_a_memory_error(self):
        self.assertTrue(issubclass(memory.MemoryLockError, memory.MemoryError))
        self.assertTrue(issubclass(memory.DuplicateHandleError, memory.MemoryError))


class UsageTests(unittest.TestCase):
    def test_update_usage_increments_and_estimates(self):
        with TempProject() as project:
            memory.init_project(project.root)
            meta = memory.register_handle(
                project.root, "hello", "say hello", ".forge/handles/hello.py"
            )
            self.assertEqual(meta["uses"], 0)
            self.assertIsNone(meta["last_used"])

            first = memory.update_usage(project.root, "hello")
            self.assertEqual(first["uses"], 1)
            self.assertEqual(
                first["tokens_saved_est"],
                stats.estimate_tokens_saved(1, stats.DEFAULT_AVG_TOKENS_MANUAL),
            )
            self.assertTrue(first["last_used"])

            second = memory.update_usage(project.root, "hello")
            self.assertEqual(second["uses"], 2)
            self.assertEqual(
                second["tokens_saved_est"],
                stats.estimate_tokens_saved(2, stats.DEFAULT_AVG_TOKENS_MANUAL),
            )

    def test_failed_usage_does_not_increment(self):
        with TempProject() as project:
            memory.init_project(project.root)
            memory.register_handle(
                project.root, "hello", "say hello", ".forge/handles/hello.py"
            )
            memory.update_usage(project.root, "hello")
            unchanged = memory.update_usage(project.root, "hello", success=False)

            self.assertEqual(unchanged["uses"], 1)
            self.assertEqual(memory.get_handle(project.root, "hello")["uses"], 1)

    def test_update_usage_unknown_handle_raises(self):
        with TempProject() as project:
            memory.init_project(project.root)
            with self.assertRaises(memory.MemoryError):
                memory.update_usage(project.root, "missing")

    def test_stored_avg_survives_usage_updates(self):
        """Regression: the per-handle value must beat the global config value."""
        with TempProject() as project:
            memory.init_project(project.root)
            memory.register_handle(
                project.root,
                "stored",
                "stored average",
                ".forge/handles/stored.py",
                avg_tokens_manual=700,
            )

            updated = memory.update_usage(project.root, "stored")

            self.assertEqual(updated["avg_tokens_manual"], 700)
            self.assertEqual(
                updated["tokens_saved_est"], stats.estimate_tokens_saved(1, 700)
            )
            summary = stats.summarize(
                memory.load_memory(project.root), memory.load_config(project.root)
            )
            row = summary["per_handle"][0]
            self.assertEqual(row["avg_tokens_manual"], 700)
            self.assertEqual(row["tokens_saved_est"], stats.estimate_tokens_saved(1, 700))

    def test_avg_tokens_precedence(self):
        config = {
            "avg_tokens_manual": 1500,
            "handles": {"a": {"avg_tokens_manual": 900}},
        }
        self.assertEqual(stats.avg_tokens_for_handle(config, "a", 1000), 900)
        self.assertEqual(stats.avg_tokens_for_handle(config, "b", 1000), 1000)
        self.assertEqual(stats.avg_tokens_for_handle(config, "b"), 1500)
        self.assertEqual(stats.avg_tokens_for_handle({}, "b", None), 1500)
        self.assertEqual(stats.avg_tokens_for_handle({}, "b", 0), 1500)

    def test_set_and_remove_avg_override(self):
        with TempProject() as project:
            memory.init_project(project.root)
            memory.set_handle_avg_tokens(project.root, "x", 250)
            self.assertEqual(memory.avg_tokens(project.root, "x"), 250)

            memory.remove_handle_config(project.root, "x")
            self.assertEqual(memory.avg_tokens(project.root, "x"), 1500)


class HandleLookupTests(unittest.TestCase):
    def test_get_find_and_remove(self):
        with TempProject() as project:
            memory.init_project(project.root)
            meta = memory.register_handle(
                project.root,
                "slugify",
                "convert text to a url slug",
                ".forge/handles/slugify.py",
                tags=["text"],
            )

            self.assertEqual(memory.get_handle(project.root, "slugify"), meta)
            self.assertIsNone(memory.get_handle(project.root, "missing"))
            self.assertEqual(
                memory.find_by_signature(project.root, meta["signature"]), "slugify"
            )
            self.assertIsNone(memory.find_by_signature(project.root, "0" * 64))

            removed = memory.remove_handle(project.root, "slugify")
            self.assertEqual(removed, meta)
            self.assertIsNone(memory.remove_handle(project.root, "slugify"))
            self.assertIsNone(memory.get_handle(project.root, "slugify"))

    def test_duplicate_registration_is_refused(self):
        with TempProject() as project:
            memory.init_project(project.root)
            memory.register_handle(
                project.root, "dup", "first", ".forge/handles/dup.py"
            )
            with self.assertRaises(memory.DuplicateHandleError):
                memory.register_handle(
                    project.root, "dup", "second", ".forge/handles/dup.py"
                )
            self.assertEqual(memory.get_handle(project.root, "dup")["task"], "first")

    def test_build_handle_meta_rejects_oversized_task(self):
        with self.assertRaises(memory.MemoryError):
            memory.build_handle_meta(
                "x", "a" * (memory.MAX_TASK_LENGTH + 1), ".forge/handles/x.py"
            )


class RedactionBeforePersistTests(unittest.TestCase):
    def test_create_handle_redacts_before_persisting(self):
        with TempProject() as project:
            memory.init_project(project.root)
            secret = "sk-abcdefghijklmnopqrstuvwx"
            task = "call the api with password=hunter2 and key {}".format(secret)

            meta, path = registry.create_handle(project.root, "secret-task", task)

            self.assertNotIn("hunter2", meta["task"])
            self.assertNotIn(secret, meta["task"])
            self.assertIn("[REDACTED]", meta["task"])
            self.assertEqual(meta["signature"], memory.task_signature(meta["task"]))

            raw_memory = project.memory_file.read_text(encoding="utf-8")
            self.assertNotIn("hunter2", raw_memory)
            self.assertNotIn(secret, raw_memory)

            source = path.read_text(encoding="utf-8")
            self.assertNotIn("hunter2", source)
            self.assertNotIn(secret, source)

    def test_redact_helper_still_detects_common_shapes(self):
        self.assertTrue(redact.contains_secret("password=hunter2"))
        self.assertFalse(redact.contains_secret("convert markdown to html"))


class ConcurrencyTests(unittest.TestCase):
    UPDATE_SCRIPT = (
        "import sys\n"
        "from pathlib import Path\n"
        "from forge import memory\n"
        "project = Path(sys.argv[1])\n"
        "for _ in range(3):\n"
        "    memory.update_usage(project, 'busy')\n"
    )

    def test_concurrent_updates_do_not_lose_writes(self):
        with TempProject() as project:
            memory.init_project(project.root)
            memory.register_handle(
                project.root, "busy", "busy task", ".forge/handles/busy.py"
            )

            processes = []
            for _ in range(4):
                processes.append(
                    subprocess.Popen(
                        [sys.executable, "-c", self.UPDATE_SCRIPT, str(project.root)],
                        cwd=str(project.root),
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        env=base_env(),
                    )
                )
            for process in processes:
                stdout, stderr = process.communicate(timeout=120)
                self.assertEqual(
                    process.returncode,
                    0,
                    "worker failed: {}".format(stderr.decode("utf-8", "replace")),
                )

            meta = memory.get_handle(project.root, "busy")
            self.assertEqual(meta["uses"], 12)
            json.loads(project.memory_file.read_text(encoding="utf-8"))
            self.assertFalse(memory.lock_path(project.root).exists())


if __name__ == "__main__":
    unittest.main()
