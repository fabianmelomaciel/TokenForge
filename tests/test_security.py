"""Security tests: redaction, traversal, danger handles, metadata hygiene."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from .helpers import (
        ProjectTestCase,
        create_symlink_or_skip,
    )
except ImportError:  # direct discovery from inside tests/
    from helpers import (  # type: ignore[no-redef]
        ProjectTestCase,
        create_symlink_or_skip,
    )

from forge import installer, memory, redact, registry


REDACTION_CASES = [
    ("sk-" + "a" * 24, "[REDACTED_OPENAI_KEY]", "a" * 24),
    ("sk-proj-" + "b" * 24, "[REDACTED_OPENAI_KEY]", "b" * 24),
    ("sk-ant-" + "c" * 24, "[REDACTED_OPENAI_KEY]", "c" * 24),
    ("ghp_" + "d" * 30, "[REDACTED_GITHUB_TOKEN]", "d" * 30),
    ("github_pat_" + "e" * 30, "[REDACTED_GITHUB_TOKEN]", "e" * 30),
    ("AKIA" + "A" * 16, "[REDACTED_AWS_KEY]", "A" * 16),
    ("ASIA" + "B" * 16, "[REDACTED_AWS_KEY]", "B" * 16),
    ("xoxb-" + "1" * 12, "[REDACTED_SLACK_TOKEN]", "1" * 12),
    ("AIza" + "f" * 35, "[REDACTED_GOOGLE_KEY]", "f" * 35),
    ("Bearer abcdefgh12345678", "Bearer [REDACTED]", "abcdefgh12345678"),
    ("password=hunter2", "password=[REDACTED]", "hunter2"),
    ("api_key: abc123", "api_key=[REDACTED]", "abc123"),
    ("client_secret = s3cr3t", "client_secret=[REDACTED]", "s3cr3t"),
    ("secret: shhh", "secret=[REDACTED]", "shhh"),
    ("0123456789abcdef" * 3, "[REDACTED_HEX]", "0123456789abcdef" * 3),
    ("QUJD" * 12, "[REDACTED_BASE64]", "QUJD" * 12),
]


class RedactionTests(unittest.TestCase):
    def test_common_secret_shapes_are_redacted(self):
        for raw, marker, secret in REDACTION_CASES:
            with self.subTest(raw=raw):
                result = redact.redact(raw)
                self.assertIn(marker, result)
                self.assertNotIn(secret, result)
                self.assertTrue(redact.contains_secret(raw))
                self.assertFalse(redact.contains_secret(result))

    def test_private_key_block_is_redacted(self):
        raw = (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEowIBAAKCAQEA1234567890\n"
            "-----END RSA PRIVATE KEY-----"
        )
        result = redact.redact(raw)
        self.assertEqual(result, "[REDACTED_PRIVATE_KEY]")
        self.assertNotIn("MIIEowIBAAKCAQEA1234567890", result)

    def test_url_credentials_are_redacted(self):
        result = redact.redact("https://user:pass@example.com/path")
        self.assertEqual(result, "https://[REDACTED]@example.com/path")
        self.assertNotIn("user:pass", result)

    def test_jwt_is_redacted(self):
        raw = (
            "eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        )
        result = redact.redact(raw)
        self.assertEqual(result, "[REDACTED_JWT]")

    def test_normal_text_is_unchanged(self):
        for text in [
            "convert text to a URL slug",
            "count words in a file",
            "normalize CSV headers",
            "summarize the changelog",
        ]:
            with self.subTest(text=text):
                self.assertEqual(redact.redact(text), text)
                self.assertFalse(redact.contains_secret(text))

    def test_empty_input(self):
        self.assertEqual(redact.redact(""), "")
        self.assertFalse(redact.contains_secret(""))


class PersistenceSecurityTests(ProjectTestCase):
    def test_task_is_redacted_before_persisting(self):
        secret = "sk-" + "z" * 24
        task = "call api with password=hunter2 and key {}".format(secret)

        result = self.cli("new", "deploy", "--task", task)

        self.assertEqual(result.returncode, 0, result.stderr)
        raw = self.project.memory_file.read_text(encoding="utf-8")
        self.assertNotIn("hunter2", raw)
        self.assertNotIn(secret, raw)
        self.assertIn("[REDACTED]", raw)
        meta = self.project.memory()["handles"]["deploy"]
        self.assertEqual(meta["signature"], memory.task_signature(meta["task"]))
        self.assertNotIn(
            "hunter2", self.project.handle_file("deploy").read_text(encoding="utf-8")
        )

    def test_memory_never_stores_stdout_or_environment(self):
        self.project.new("greet", "say hello")
        marker = "STDOUT_MARKER_12345"
        env_secret = "ENV_SECRET_VALUE_67890"

        result = self.cli(
            "run",
            "greet",
            "--json",
            json.dumps({"name": marker}),
            env={"TOKENFORGE_SECURITY_TEST": env_secret},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(marker, result.stdout)
        raw = self.project.memory_file.read_text(encoding="utf-8")
        self.assertNotIn(marker, raw)
        self.assertNotIn(env_secret, raw)

    def test_memory_never_stores_file_contents(self):
        marker = "FILE_CONTENT_MARKER_24680"
        data_file = self.project.root / "input.txt"
        data_file.write_text(marker + "\n", encoding="utf-8")
        self.project.new("reader", "read the file at {}".format(data_file))

        self.assertEqual(self.cli("run", "reader").returncode, 0)

        raw = self.project.memory_file.read_text(encoding="utf-8")
        self.assertNotIn(marker, raw)

    def test_memory_schema_is_metadata_only(self):
        self.project.new("greet", "say hello", "--tags", "text", "--danger")
        self.assertEqual(self.cli("run", "greet", "--yes").returncode, 0)

        meta = self.project.memory()["handles"]["greet"]
        expected = {
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
        }
        self.assertEqual(set(meta), expected)

        forbidden = {
            "stdout",
            "stderr",
            "env",
            "environment",
            "contents",
            "content",
            "prompt",
            "prompts",
            "output",
            "outputs",
            "payload",
        }
        data = self.project.memory()

        def walk_keys(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    yield key
                    for nested in walk_keys(value):
                        yield nested
            elif isinstance(node, list):
                for item in node:
                    for nested in walk_keys(item):
                        yield nested

        self.assertEqual(forbidden.intersection(set(walk_keys(data))), set())


class PathTraversalSecurityTests(ProjectTestCase):
    def test_cli_rejects_traversal_names(self):
        for args in (
            ("show", "../evil"),
            ("run", "../evil"),
            ("rm", "../evil", "--yes"),
            ("show", "/etc/passwd"),
        ):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("invalid handle name", result.stderr)

    def test_new_rejects_traversal_and_writes_nothing(self):
        escape_name = self.project.root.name + "-escape"
        result = self.cli("new", "../" + escape_name, "--task", "task")

        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.project.root.parent / (escape_name + ".py")).exists())
        self.assertFalse(self.project.handle_file(escape_name).exists())
        self.assertEqual(self.project.memory()["handles"], {})

    def test_registry_rejects_traversal_paths(self):
        for name in ["..", "../evil", "../../etc/passwd", "/etc/passwd", "a/../../b"]:
            with self.subTest(name=name):
                with self.assertRaises(registry.RegistryError):
                    registry.handle_file_path(self.project.root, name)
                with self.assertRaises(registry.RegistryError):
                    registry.resolve_handle_path(self.project.root, name)

    def test_trailing_newline_name_is_rejected(self):
        with self.assertRaises(registry.RegistryError):
            registry.validate_name("escape\n")
        result = self.cli("new", "escape\n", "--task", "task")
        self.assertEqual(result.returncode, 2)


class DangerHandleTests(ProjectTestCase):
    def test_danger_handle_requires_yes(self):
        self.project.new("danger", "dangerous task", "--danger")

        blocked = self.cli("run", "danger")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("requires confirmation", blocked.stderr)
        self.assertEqual(self.project.memory()["handles"]["danger"]["uses"], 0)

        allowed = self.cli("run", "danger", "--yes")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(self.project.memory()["handles"]["danger"]["uses"], 1)


class InstallerSymlinkSecurityTests(ProjectTestCase):
    init_project = False

    def test_installer_refuses_symlinked_target(self):
        outside_dir = Path(tempfile.mkdtemp(prefix="tokenforge-outside-"))
        self.addCleanup(shutil.rmtree, str(outside_dir), True)
        outside = outside_dir / "AGENTS.md"
        outside.write_text("outside\n", encoding="utf-8")
        link = self.project.root / "AGENTS.md"
        create_symlink_or_skip(self, outside, link)

        report = installer.install(
            scope="project", project_dir=self.project.root, ides="opencode"
        )

        self.assertFalse(report.ok)
        self.assertIn("symlink", "\n".join(report.lines))
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_installer_refuses_symlinked_parent(self):
        outside_dir = Path(tempfile.mkdtemp(prefix="tokenforge-outside-"))
        self.addCleanup(shutil.rmtree, str(outside_dir), True)
        link = self.project.root / ".cursor"
        create_symlink_or_skip(
            self, outside_dir, link, target_is_directory=True
        )

        report = installer.install(
            scope="project", project_dir=self.project.root, ides="cursor"
        )

        self.assertFalse(report.ok)
        self.assertIn("symlink", "\n".join(report.lines))
        self.assertEqual(list(outside_dir.rglob("*")), [])


if __name__ == "__main__":
    unittest.main()
