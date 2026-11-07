"""End-to-end tests for ``python -m forge`` using subprocesses."""

from __future__ import annotations

import json
import unittest

try:
    from .helpers import (
        ProjectTestCase,
        TempProject,
        json_stdout,
        run_cli,
    )
except ImportError:  # direct discovery from inside tests/
    from helpers import (  # type: ignore[no-redef]
        ProjectTestCase,
        TempProject,
        json_stdout,
        run_cli,
    )

from forge import stats
from forge.version import VERSION


class InitCommandTests(ProjectTestCase):
    init_project = False

    def test_init_creates_layout(self):
        result = self.cli("init")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Initialized TokenForge", result.stdout)
        self.assertTrue(self.project.forge_dir.is_dir())
        self.assertTrue(self.project.handles_dir.is_dir())
        self.assertTrue(self.project.memory_file.is_file())
        self.assertTrue(self.project.config_file.is_file())

    def test_init_is_idempotent(self):
        self.assertEqual(self.cli("init").returncode, 0)
        second = self.cli("init")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("already initialized", second.stdout)

    def test_init_quiet_suppresses_output(self):
        result = self.cli("init", "--quiet")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")


class NewCommandTests(ProjectTestCase):
    def test_new_creates_handle_and_memory_entry(self):
        result = self.cli(
            "new", "greet", "--task", "say hello", "--tags", "Text, util"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Forged handle 'greet'", result.stdout)
        self.assertTrue(self.project.handle_file("greet").is_file())
        meta = self.project.memory()["handles"]["greet"]
        self.assertEqual(meta["name"], "greet")
        self.assertEqual(meta["task"], "say hello")
        self.assertEqual(meta["tags"], ["text", "util"])
        self.assertFalse(meta["danger"])
        self.assertEqual(meta["uses"], 0)
        self.assertIsNone(meta["last_used"])

    def test_new_danger_flag_is_recorded(self):
        self.assertEqual(
            self.cli("new", "danger", "--task", "dangerous", "--danger").returncode,
            0,
        )
        self.assertTrue(self.project.memory()["handles"]["danger"]["danger"])

    def test_new_duplicate_exit_2(self):
        self.project.new("greet", "say hello")
        result = self.cli("new", "greet", "--task", "say hello again")

        self.assertEqual(result.returncode, 2)
        self.assertIn("already registered", result.stderr)

    def test_new_invalid_name_exit_2(self):
        result = self.cli("new", "Bad Name", "--task", "x")

        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid handle name", result.stderr)
        self.assertEqual(self.project.memory()["handles"], {})

    def test_new_requires_task(self):
        result = self.cli("new", "greet")

        self.assertEqual(result.returncode, 2)
        self.assertIn("required", result.stderr)

    def test_new_empty_task_exit_2(self):
        result = self.cli("new", "greet", "--task", "")

        self.assertEqual(result.returncode, 2)
        self.assertIn("task description is required", result.stderr)

    def test_new_redacts_secret_in_task(self):
        secret = "ghp_" + "a" * 30
        result = self.cli("new", "deploy", "--task", "use token {}".format(secret))

        self.assertEqual(result.returncode, 0, result.stderr)
        raw = self.project.memory_file.read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", raw)

    def test_new_quiet_suppresses_output(self):
        result = self.cli("new", "quiet", "--task", "quiet task", "--quiet")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("quiet", self.project.memory()["handles"])


class RunCommandTests(ProjectTestCase):
    def test_run_success_updates_usage(self):
        self.project.new("greet", "say hello")

        result = self.cli("run", "greet", "--json", '{"name": "world"}')

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertIn("world", payload["message"])
        self.assertEqual(self.project.memory()["handles"]["greet"]["uses"], 1)

    def test_run_reads_json_from_stdin(self):
        self.project.new("greet", "say hello")

        result = self.cli(
            "run", "greet", "--json", "-", input='{"name": "piped"}'
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("piped", json.loads(result.stdout)["message"])

    def test_run_unregistered_exit_1(self):
        result = self.cli("run", "missing")

        self.assertEqual(result.returncode, 1)
        self.assertIn("not registered", result.stderr)

    def test_run_invalid_json_exit_2_and_no_usage(self):
        self.project.new("greet", "say hello")

        result = self.cli("run", "greet", "--json", "{")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)["ok"], False)
        self.assertEqual(self.project.memory()["handles"]["greet"]["uses"], 0)

    def test_run_danger_requires_yes(self):
        self.project.new("danger", "dangerous task", "--danger")

        blocked = self.cli("run", "danger")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("--yes", blocked.stderr)
        self.assertEqual(self.project.memory()["handles"]["danger"]["uses"], 0)

        allowed = self.cli("run", "danger", "--yes")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertEqual(self.project.memory()["handles"]["danger"]["uses"], 1)

    def test_run_timeout_exit_124(self):
        self.project.new("sleeper", "sleep")
        self.project.handle_file("sleeper").write_text(
            "import time\ntime.sleep(10)\n", encoding="utf-8"
        )

        result = self.cli("run", "sleeper", "--timeout", "1")

        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out", json.loads(result.stderr)["error"])
        self.assertEqual(self.project.memory()["handles"]["sleeper"]["uses"], 0)

    def test_run_failure_is_not_counted(self):
        self.project.new("boom", "boom")
        self.project.handle_file("boom").write_text(
            "raise SystemExit(3)\n", encoding="utf-8"
        )

        result = self.cli("run", "boom")

        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.project.memory()["handles"]["boom"]["uses"], 0)


class TestCommandTests(ProjectTestCase):
    def test_selftest_passes(self):
        self.project.new("greet", "say hello")

        result = self.cli("test", "greet")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["selftest"], "passed")

    def test_missing_handle_exit_1(self):
        result = self.cli("test", "missing")

        self.assertEqual(result.returncode, 1)
        self.assertIn("not registered", result.stderr)

    def test_failing_selftest_exit_1(self):
        self.project.new("greet", "say hello")
        self.project.handle_file("greet").write_text(
            "raise SystemExit(1)\n", encoding="utf-8"
        )

        result = self.cli("test", "greet")

        self.assertEqual(result.returncode, 1)


class ListShowSearchTests(ProjectTestCase):
    def test_list_json_parses_and_is_sorted(self):
        self.project.new("beta", "second task", "--tags", "two")
        self.project.new("alpha", "first task", "--tags", "one")

        payload = json_stdout(self.cli("list", "--json"))

        self.assertEqual(payload["count"], 2)
        self.assertEqual([item["name"] for item in payload["handles"]], ["alpha", "beta"])
        self.assertEqual(payload["handles"][0]["tags"], ["one"])

    def test_list_text_output(self):
        self.project.new("alpha", "first task")

        result = self.cli("list")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NAME", result.stdout)
        self.assertIn("alpha", result.stdout)
        self.assertIn("Conservative estimate", result.stdout)

    def test_list_empty_project(self):
        result = self.cli("list")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No handles yet", result.stdout)

    def test_show_prints_metadata(self):
        self.project.new(
            "greet", "say hello", "--tags", "text", "--danger", "--avg-tokens", "1000"
        )

        result = self.cli("show", "greet")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("name:               greet", result.stdout)
        self.assertIn("say hello", result.stdout)
        self.assertIn("danger:             yes", result.stdout)
        self.assertIn("avg tokens manual:  1000", result.stdout)

    def test_show_missing_handle_exit_1(self):
        result = self.cli("show", "missing")

        self.assertEqual(result.returncode, 1)
        self.assertIn("not registered", result.stderr)

    def test_show_reports_missing_file(self):
        self.project.new("greet", "say hello")
        self.project.handle_file("greet").unlink()

        result = self.cli("show", "greet")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("MISSING FILE", result.stdout)

    def test_search_matches_name_task_and_tags(self):
        self.project.new(
            "slugify", "convert text to a url slug", "--tags", "text,url"
        )
        self.project.new("other", "unrelated work", "--tags", "misc")

        for query in ["slug", "url slug", "text", "url"]:
            with self.subTest(query=query):
                result = self.cli("search", query)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("slugify", result.stdout)

        no_match = self.cli("search", "zzz-nothing")
        self.assertEqual(no_match.returncode, 0)
        self.assertIn("No handles match", no_match.stdout)


class StatsCommandTests(ProjectTestCase):
    def test_stats_json_reflects_runs(self):
        self.project.new("greet", "say hello")
        for _ in range(2):
            result = self.cli("run", "greet", "--json", '{"name": "x"}')
            self.assertEqual(result.returncode, 0, result.stderr)

        payload = json_stdout(self.cli("stats", "--json"))

        self.assertEqual(payload["handles"], 1)
        self.assertEqual(payload["total_uses"], 2)
        self.assertEqual(
            payload["tokens_saved_est"],
            stats.estimate_tokens_saved(2, stats.DEFAULT_AVG_TOKENS_MANUAL),
        )
        self.assertIn("Conservative estimate", payload["estimate"]["label"])
        self.assertEqual(payload["per_handle"][0]["uses"], 2)
        self.assertIn("project_path", payload)

    def test_stats_text_output(self):
        self.project.new("greet", "say hello")

        result = self.cli("stats")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("handles:", result.stdout)
        self.assertIn("tokens saved (est)", result.stdout)

    def test_stats_honours_avg_tokens(self):
        self.project.new("greet", "say hello", "--avg-tokens", "1000")
        self.assertEqual(
            self.cli("run", "greet", "--json", "{}").returncode, 0
        )

        payload = json_stdout(self.cli("stats", "--json"))

        self.assertEqual(payload["tokens_saved_est"], 800)
        self.assertEqual(payload["per_handle"][0]["avg_tokens_manual"], 1000)


class RmCommandTests(ProjectTestCase):
    def test_rm_yes_deletes_file_and_entry(self):
        self.project.new("greet", "say hello")

        result = self.cli("rm", "greet", "--yes")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Removed handle 'greet'", result.stdout)
        self.assertFalse(self.project.handle_file("greet").exists())
        self.assertNotIn("greet", self.project.memory()["handles"])

    def test_rm_requires_confirmation(self):
        self.project.new("greet", "say hello")

        result = self.cli("rm", "greet")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--yes", result.stderr)
        self.assertTrue(self.project.handle_file("greet").exists())
        self.assertIn("greet", self.project.memory()["handles"])

    def test_rm_missing_exit_1(self):
        result = self.cli("rm", "missing", "--yes")

        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)


class DoctorTests(ProjectTestCase):
    def test_doctor_ok_after_init(self):
        result = self.cli("doctor")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("[ok] python", result.stdout)
        self.assertIn("[ok] .forge", result.stdout)
        self.assertIn("[ok] memory", result.stdout)
        self.assertIn("Doctor found no problems", result.stdout)

    def test_doctor_reports_corrupt_memory(self):
        self.project.memory_file.write_text("{bad", encoding="utf-8")

        result = self.cli("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] memory", result.stdout)


class DoctorWithoutForgeTests(ProjectTestCase):
    init_project = False

    def test_doctor_fails_without_forge(self):
        result = self.cli("doctor")

        self.assertEqual(result.returncode, 1)
        self.assertIn("[fail] .forge", result.stdout)

    def test_commands_without_forge_exit_2(self):
        for args in (["list"], ["new", "x", "--task", "t"], ["stats"], ["search", "x"]):
            with self.subTest(args=args):
                result = self.cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn("no .forge", result.stderr)


class CliMetaTests(ProjectTestCase):
    init_project = False

    def test_version_flag(self):
        result = self.cli("--version")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "TokenForge {}".format(VERSION))

    def test_unknown_command_exit_2(self):
        result = self.cli("frobnicate")

        self.assertEqual(result.returncode, 2)
        self.assertIn("invalid choice", result.stderr)

    def test_no_command_prints_help(self):
        result = self.cli()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: forge", result.stdout)

    def test_corrupt_memory_list_recovers_with_json(self):
        self.project.init()
        self.project.memory_file.write_text("{bad", encoding="utf-8")

        result = self.cli("list", "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("corrupt", result.stderr)
        self.assertEqual(json.loads(result.stdout)["count"], 0)
        backups = [
            entry.name
            for entry in self.project.forge_dir.iterdir()
            if entry.name.startswith("memory.corrupt.")
        ]
        self.assertEqual(len(backups), 1)


class ProjectOverrideTests(ProjectTestCase):
    init_project = False

    def test_project_before_subcommand(self):
        with TempProject() as other:
            result = run_cli(
                ["--project", str(other.root), "init"], self.project.root
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(other.forge_dir.is_dir())
            self.assertFalse(self.project.forge_dir.exists())

    def test_project_after_subcommand(self):
        with TempProject() as other:
            self.assertEqual(
                run_cli(
                    ["init", "--project", str(other.root)], self.project.root
                ).returncode,
                0,
            )
            created = run_cli(
                ["new", "x", "--task", "task", "--project", str(other.root)],
                self.project.root,
            )
            self.assertEqual(created.returncode, 0, created.stderr)

            payload = json.loads(
                run_cli(
                    ["list", "--json", "--project", str(other.root)],
                    self.project.root,
                ).stdout
            )
            self.assertEqual(payload["count"], 1)
            self.assertEqual(payload["handles"][0]["name"], "x")
            self.assertFalse(self.project.forge_dir.exists())

    def test_relative_project_path(self):
        result = self.cli("init", "--project", "nested")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.project.root / "nested" / ".forge").is_dir())
        self.assertFalse(self.project.forge_dir.exists())


if __name__ == "__main__":
    unittest.main()
