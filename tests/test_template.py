"""Tests for the generated handle template and the example handles."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import unittest
from pathlib import Path

try:
    from .helpers import REPO_ROOT, TempProject, run_script
except ImportError:  # direct discovery from inside tests/
    from helpers import REPO_ROOT, TempProject, run_script  # type: ignore[no-redef]

from forge import template


EXAMPLES_DIR = REPO_ROOT / "examples" / "handles"
SAFE_IMPORTS = {"argparse", "json", "sys", "re", "unicodedata", "__future__"}


def load_generated(source, filename="generated_handle.py"):
    namespace = {"__name__": "tokenforge_generated_test"}
    exec(compile(source, filename, "exec"), namespace)
    return namespace


def call_main(namespace, argv):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = namespace["main"](argv)
    return code, stdout.getvalue(), stderr.getvalue()


def assert_no_dangerous_code(testcase, source, label):
    """Fail if the AST contains eval/exec, shell=True, subprocess or os."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Name):
                testcase.assertNotIn(
                    function.id, {"eval", "exec"}, "{} uses {}".format(label, function.id)
                )
            if isinstance(function, ast.Attribute):
                testcase.assertNotIn(
                    function.attr,
                    {"system", "popen"},
                    "{} uses .{}".format(label, function.attr),
                )
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    value = keyword.value
                    testcase.assertFalse(
                        isinstance(value, ast.Constant) and value.value is True,
                        "{} passes shell=True".format(label),
                    )
        if isinstance(node, ast.Import):
            for alias in node.names:
                testcase.assertNotIn(
                    alias.name,
                    {"subprocess", "os"},
                    "{} imports {}".format(label, alias.name),
                )
        if isinstance(node, ast.ImportFrom):
            testcase.assertNotIn(
                node.module,
                {"subprocess", "os"},
                "{} imports from {}".format(label, node.module),
            )


class RenderTests(unittest.TestCase):
    TRICKY_TASKS = [
        'a """ b',
        'x __TASK_REPR__ """ y',
        'x __TASK_DOC__ y',
        'x __NAME__ y',
        'x __TAGS_REPR__ y',
        'x __SIGNATURE__ y',
        'x __VERSION__ y',
        'x __DANGER__ y',
        "ends with a backslash \\",
        "single ' and double \" quotes",
        "unicode: ñandú ✓ 日本語",
        "line1\nline2\nline3",
        "tabs\tand\tspaces",
        r"read C:\Users\me\file.txt",
        r"match \d+ digits",
        r"newline \N{BULLET} and hex \x41",
        r"emoji \U0001F600",
    ]

    def test_tricky_tasks_compile(self):
        for task in self.TRICKY_TASKS:
            with self.subTest(task=task):
                source = template.render_handle(name="tricky", task=task)
                compile(source, "tricky.py", "exec")

    def test_placeholders_are_not_rescanned(self):
        task = "keep __TASK_REPR__ and __NAME__ literal"
        source = template.render_handle(name="literal", task=task)

        namespace = load_generated(source)

        self.assertEqual(namespace["TASK"], task)
        self.assertEqual(namespace["HANDLE_NAME"], "literal")
        self.assertIn(repr(task), source)
        compile(source, "literal.py", "exec")

    def test_triple_quotes_do_not_break_docstring(self):
        task = 'render """quoted""" text'
        source = template.render_handle(name="quotes", task=task)

        namespace = load_generated(source)

        self.assertEqual(namespace["TASK"], task)
        self.assertIn("'''quoted'''", source)

    def test_windows_path_task_keeps_original_text(self):
        task = r"read C:\Users\me\file.txt"
        source = template.render_handle(name="paths", task=task)

        namespace = load_generated(source)

        self.assertEqual(namespace["TASK"], task)
        self.assertIn(task, namespace["__doc__"])
        compile(source, "paths.py", "exec")

    def test_metadata_constants(self):
        source = template.render_handle(
            name="meta",
            task="do the thing",
            tags=["one", "two"],
            danger=True,
            signature="deadbeef",
            version="9.9.9",
        )

        namespace = load_generated(source)

        self.assertEqual(namespace["HANDLE_NAME"], "meta")
        self.assertEqual(namespace["TASK"], "do the thing")
        self.assertEqual(namespace["TAGS"], ["one", "two"])
        self.assertIs(namespace["DANGER"], True)
        self.assertIn("Generated by TokenForge 9.9.9", source)
        self.assertIn("Task signature: deadbeef", source)
        self.assertIn("Tags: one, two", source)
        self.assertIn("Danger: yes", source)

    def test_no_dangerous_code_in_template(self):
        source = template.render_handle(name="safe", task="safe task")
        assert_no_dangerous_code(self, source, "template")

        tree = ast.parse(source)
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertEqual(imports, {"argparse", "json", "sys", "__future__"})


class GeneratedHandleTests(unittest.TestCase):
    def setUp(self):
        self._temp_project = TempProject()
        self.project = self._temp_project.__enter__()
        self.addCleanup(self._temp_project.cleanup)
        self.source = template.render_handle(
            name="greet", task="say hello", tags=["text"]
        )
        self.path = self.project.root / "greet.py"
        self.path.write_text(self.source, encoding="utf-8")

    def test_selftest_passes(self):
        result = run_script(self.path, ["--selftest"])

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["selftest"], "passed")
        self.assertEqual(payload["handle"], "greet")

    def test_json_contract(self):
        result = run_script(self.path, ["--json", '{"name": "world"}'])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        lines = result.stdout.strip().splitlines()
        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["message"], "Hello, world!")

    def test_reads_json_from_stdin(self):
        result = run_script(
            self.path, ["--json", "-"], input='{"name": "piped"}'
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["message"], "Hello, piped!")

    def test_dry_run_flag(self):
        result = run_script(self.path, ["--dry-run", "--json", "{}"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["dry_run"])

    def test_invalid_json_exit_2(self):
        result = run_script(self.path, ["--json", "{"])

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertFalse(json.loads(result.stderr)["ok"])

    def test_non_object_json_exit_2(self):
        result = run_script(self.path, ["--json", "[1, 2]"])

        self.assertEqual(result.returncode, 2)
        self.assertIn("object", json.loads(result.stderr)["error"])

    def test_missing_option_value_exit_2(self):
        result = run_script(self.path, ["--json"])

        self.assertEqual(result.returncode, 2)

    def test_runtime_error_exit_1(self):
        namespace = load_generated(self.source)

        def boom(payload, dry_run=False):
            raise RuntimeError("boom")

        namespace["handle"] = boom
        code, stdout, stderr = call_main(namespace, ["--json", "{}"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        payload = json.loads(stderr)
        self.assertFalse(payload["ok"])
        self.assertIn("RuntimeError", payload["error"])

    def test_selftest_failure_exit_1(self):
        namespace = load_generated(self.source)

        def always_fails(payload, dry_run=False):
            raise ValueError("always fails")

        namespace["handle"] = always_fails
        code, stdout, stderr = call_main(namespace, ["--selftest"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        payload = json.loads(stderr)
        self.assertFalse(payload["ok"])
        self.assertIn("selftest failed", payload["error"])

    def test_extra_arguments_are_available_to_handle(self):
        namespace = load_generated(self.source)
        seen = {}

        def capture(payload, dry_run=False):
            seen.update(payload)
            return {"ok": True}

        namespace["handle"] = capture
        code, _, _ = call_main(namespace, ["--json", "{}", "one", "two"])

        self.assertEqual(code, 0)
        self.assertEqual(seen["_args"], ["one", "two"])


class ExampleHandleTests(unittest.TestCase):
    def test_examples_exist(self):
        found = sorted(path.name for path in EXAMPLES_DIR.glob("*.py"))
        self.assertEqual(found, ["count_words.py", "slugify.py"])

    def test_examples_follow_contract(self):
        for path in sorted(EXAMPLES_DIR.glob("*.py")):
            with self.subTest(example=path.name):
                source = path.read_text(encoding="utf-8")
                assert_no_dangerous_code(self, source, path.name)
                tree = ast.parse(source)
                imports = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imports.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        imports.add(node.module)
                self.assertTrue(
                    imports.issubset(SAFE_IMPORTS),
                    "{} imports {}".format(path.name, imports - SAFE_IMPORTS),
                )

                selftest = run_script(path, ["--selftest"])
                self.assertEqual(selftest.returncode, 0, selftest.stderr)
                payload = json.loads(selftest.stdout)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["selftest"], "passed")
                self.assertEqual(payload["handle"], path.stem)

                normal = run_script(
                    path, ["--json", '{"text": "Hello World"}']
                )
                self.assertEqual(normal.returncode, 0, normal.stderr)
                result = json.loads(normal.stdout)
                self.assertIsInstance(result, dict)
                self.assertTrue(result["ok"])

                dry = run_script(
                    path, ["--dry-run", "--json", '{"text": "Hello World"}']
                )
                self.assertEqual(dry.returncode, 0, dry.stderr)
                self.assertTrue(json.loads(dry.stdout)["dry_run"])

                invalid = run_script(path, ["--json", "{"])
                self.assertEqual(invalid.returncode, 2)
                self.assertFalse(json.loads(invalid.stderr)["ok"])

    def test_slugify_expected_output(self):
        result = run_script(
            EXAMPLES_DIR / "slugify.py", ["--json", '{"text": "Hello World"}']
        )
        self.assertEqual(json.loads(result.stdout)["slug"], "hello-world")

    def test_count_words_expected_output(self):
        result = run_script(
            EXAMPLES_DIR / "count_words.py",
            ["--json", '{"text": "one two three"}'],
        )
        payload = json.loads(result.stdout)
        self.assertEqual(payload["words"], 3)
        self.assertEqual(payload["lines"], 1)
        self.assertEqual(payload["chars"], 13)


if __name__ == "__main__":
    unittest.main()
