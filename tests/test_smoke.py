"""One end-to-end story: init, forge, test, run twice, inspect, find, list."""

from __future__ import annotations

import json
import unittest

try:
    from .helpers import REPO_ROOT, ProjectTestCase, json_stdout, run_script
except ImportError:  # direct discovery from inside tests/
    from helpers import (  # type: ignore[no-redef]
        REPO_ROOT,
        ProjectTestCase,
        json_stdout,
        run_script,
    )


class FullStoryTests(ProjectTestCase):
    init_project = False

    def test_full_story(self):
        # 1. Initialize the project.
        init = self.cli("init")
        self.assertEqual(init.returncode, 0, init.stderr)
        self.assertTrue(self.project.memory_file.is_file())

        # 2. Forge a handle from a plain-English task.
        new = self.cli(
            "new",
            "slugify",
            "--task",
            "convert text to a URL slug",
            "--tags",
            "text,url",
        )
        self.assertEqual(new.returncode, 0, new.stderr)
        handle_file = self.project.handle_file("slugify")
        self.assertTrue(handle_file.is_file())
        self.assertIn("slugify", self.project.memory()["handles"])

        # 3. The generated handle must pass its own selftest.
        tested = self.cli("test", "slugify")
        self.assertEqual(tested.returncode, 0, tested.stderr)
        self.assertEqual(json.loads(tested.stdout)["selftest"], "passed")

        # 4. Run it twice.
        payload = '{"name": "Hello World"}'
        first = self.cli("run", "slugify", "--json", payload)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(json.loads(first.stdout)["ok"])

        second = self.cli("run", "slugify", "--json", payload)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, first.stdout)

        # 5. Stats shows exactly two uses.
        stats_payload = json_stdout(self.cli("stats", "--json"))
        self.assertEqual(stats_payload["handles"], 1)
        self.assertEqual(stats_payload["total_uses"], 2)
        self.assertGreater(stats_payload["tokens_saved_est"], 0)
        self.assertEqual(stats_payload["per_handle"][0]["name"], "slugify")

        # 6. Search finds the handle by name, task and tag.
        for query in ["slug", "URL slug", "url"]:
            found = self.cli("search", query)
            self.assertEqual(found.returncode, 0, found.stderr)
            self.assertIn("slugify", found.stdout)

        # 7. Machine-readable listing parses.
        listing = json_stdout(self.cli("list", "--json"))
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["handles"][0]["name"], "slugify")
        self.assertEqual(listing["handles"][0]["uses"], 2)
        self.assertEqual(listing["handles"][0]["tags"], ["text", "url"])

        # 8. No atomic-write temp files or stale locks are left behind.
        leftovers = [
            entry.name
            for entry in self.project.forge_dir.iterdir()
            if entry.name.endswith(".tmp") or entry.name.endswith(".lock")
        ]
        self.assertEqual(leftovers, [])

    def test_bench_reports_estimates_not_measurements(self):
        result = run_script(
            REPO_ROOT / "bench" / "bench.py",
            ["--handles", "1", "--runs", "1", "--json"],
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["token_savings_are_estimates"])
        self.assertIn("NOT a measurement", payload["disclaimer"])
        self.assertEqual(payload["summary"]["total_uses"], 1)
        self.assertEqual(len(payload["rows"]), 1)


if __name__ == "__main__":
    unittest.main()
