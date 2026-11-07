"""Tests for forge.installer: project scope, dry-run, idempotency, uninstall."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from .helpers import (
        REPO_ROOT,
        ProjectTestCase,
        create_symlink_or_skip,
        snapshot_tree,
    )
except ImportError:  # direct discovery from inside tests/
    from helpers import (  # type: ignore[no-redef]
        REPO_ROOT,
        ProjectTestCase,
        create_symlink_or_skip,
        snapshot_tree,
    )

from forge import detect, installer


EXPECTED_PROJECT_FILES = [
    ".opencode/skills/tokenforge/SKILL.md",
    "AGENTS.md",
    ".claude/skills/tokenforge/SKILL.md",
    "CLAUDE.md",
    ".cursor/rules/tokenforge.mdc",
    ".windsurf/rules/tokenforge.md",
    "GEMINI.md",
    ".github/copilot-instructions.md",
]


class ProjectInstallTests(ProjectTestCase):
    init_project = False

    def test_install_creates_expected_files(self):
        report = installer.install(scope="project", project_dir=self.project.root)

        self.assertTrue(report.ok, report.lines)
        self.assertEqual(report.counts.get("ok"), len(EXPECTED_PROJECT_FILES))
        for relative in EXPECTED_PROJECT_FILES:
            path = self.project.root / relative
            self.assertTrue(path.is_file(), "missing {}".format(relative))
            self.assertGreater(path.stat().st_size, 0)

        skill = (
            self.project.root / ".opencode/skills/tokenforge/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("TokenForge", skill)
        self.assertIn("Forge once. Run forever.", skill)

        agents = (self.project.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(installer.BEGIN_MARKER, agents)
        self.assertIn(installer.END_MARKER, agents)
        self.assertIn(".opencode/skills/tokenforge/SKILL.md", agents)

    def test_dry_run_writes_nothing(self):
        before = snapshot_tree(self.project.root)

        report = installer.install(
            scope="project", project_dir=self.project.root, dry_run=True
        )

        self.assertTrue(report.ok, report.lines)
        self.assertEqual(report.counts.get("dry"), len(EXPECTED_PROJECT_FILES))
        self.assertEqual(report.counts.get("ok", 0), 0)
        self.assertEqual(snapshot_tree(self.project.root), before)

    def test_second_install_is_idempotent(self):
        installer.install(scope="project", project_dir=self.project.root)
        first_contents = {
            relative: (self.project.root / relative).read_bytes()
            for relative in EXPECTED_PROJECT_FILES
        }

        second = installer.install(
            scope="project", project_dir=self.project.root
        )

        self.assertTrue(second.ok, second.lines)
        self.assertEqual(second.counts.get("unchanged"), len(EXPECTED_PROJECT_FILES))
        self.assertEqual(second.counts.get("ok", 0), 0)
        for relative in EXPECTED_PROJECT_FILES:
            self.assertEqual(
                (self.project.root / relative).read_bytes(),
                first_contents[relative],
                relative,
            )

    def test_existing_file_is_backed_up_on_update(self):
        target = self.project.root / ".cursor/rules/tokenforge.mdc"
        target.parent.mkdir(parents=True)
        target.write_text("old tokenforge rule\n", encoding="utf-8")

        report = installer.install(
            scope="project", project_dir=self.project.root, ides="cursor"
        )

        self.assertEqual(report.counts.get("ok"), 1, report.lines)
        backup = Path(str(target) + ".tokenforge.bak")
        self.assertTrue(backup.is_file())
        self.assertEqual(
            backup.read_text(encoding="utf-8"), "old tokenforge rule\n"
        )
        self.assertIn(
            installer.BEGIN_MARKER, target.read_text(encoding="utf-8")
        )

    def test_ide_filter_only_writes_selected_ide(self):
        report = installer.install(
            scope="project", project_dir=self.project.root, ides="cursor"
        )

        self.assertTrue(report.ok, report.lines)
        self.assertEqual(
            snapshot_tree(self.project.root),
            {".cursor", ".cursor/rules", ".cursor/rules/tokenforge.mdc"},
        )
        self.assertFalse((self.project.root / "AGENTS.md").exists())

    def test_ide_filter_claude(self):
        installer.install(
            scope="project", project_dir=self.project.root, ides="claude"
        )

        self.assertTrue(
            (self.project.root / ".claude/skills/tokenforge/SKILL.md").is_file()
        )
        self.assertTrue((self.project.root / "CLAUDE.md").is_file())
        self.assertFalse((self.project.root / "AGENTS.md").exists())
        self.assertFalse((self.project.root / "GEMINI.md").exists())

    def test_unknown_ide_reports_failure_and_writes_nothing(self):
        report = installer.install(
            scope="project", project_dir=self.project.root, ides="not-an-ide"
        )

        self.assertFalse(report.ok)
        self.assertIn("unknown IDE", "\n".join(report.lines))
        self.assertEqual(snapshot_tree(self.project.root), set())

    def test_no_writes_outside_the_project(self):
        sandbox = Path(tempfile.mkdtemp(prefix="tokenforge-sandbox-"))
        self.addCleanup(shutil.rmtree, str(sandbox), True)
        project = sandbox / "project"
        project.mkdir()
        (sandbox / "outside").mkdir()
        before = snapshot_tree(sandbox)
        skill_before = (REPO_ROOT / "SKILL.md").read_bytes()

        installer.install(scope="project", project_dir=project)

        after = snapshot_tree(sandbox)
        new_paths = after - before
        self.assertTrue(new_paths)
        for relative in new_paths:
            self.assertTrue(
                relative == "project" or relative.startswith("project/"),
                "installer wrote outside the project: {}".format(relative),
            )
        self.assertEqual((REPO_ROOT / "SKILL.md").read_bytes(), skill_before)


class UninstallTests(ProjectTestCase):
    init_project = False

    def test_uninstall_removes_only_marked_blocks(self):
        agents = self.project.root / "AGENTS.md"
        agents.write_text("# My rules\n\nkeep me\n", encoding="utf-8")
        installer.install(
            scope="project", project_dir=self.project.root, ides="opencode"
        )
        self.assertIn(installer.BEGIN_MARKER, agents.read_text(encoding="utf-8"))

        report = installer.uninstall(scope="project", project_dir=self.project.root)

        self.assertTrue(report.ok, report.lines)
        text = agents.read_text(encoding="utf-8")
        self.assertNotIn(installer.BEGIN_MARKER, text)
        self.assertNotIn(installer.END_MARKER, text)
        self.assertTrue(text.startswith("# My rules"))
        self.assertIn("keep me", text)
        self.assertFalse(
            (self.project.root / ".opencode/skills/tokenforge/SKILL.md").exists()
        )

    def test_uninstall_preserves_unmanaged_files(self):
        agents = self.project.root / "AGENTS.md"
        agents.write_text("no markers here\n", encoding="utf-8")
        cursor = self.project.root / ".cursor/rules/tokenforge.mdc"
        cursor.parent.mkdir(parents=True)
        cursor.write_text("my own cursor rules\n", encoding="utf-8")

        report = installer.uninstall(scope="project", project_dir=self.project.root)

        self.assertTrue(report.ok, report.lines)
        self.assertEqual(agents.read_text(encoding="utf-8"), "no markers here\n")
        self.assertEqual(
            cursor.read_text(encoding="utf-8"), "my own cursor rules\n"
        )
        self.assertGreater(report.counts.get("skip", 0), 0)

    def test_uninstall_dry_run_writes_nothing(self):
        installer.install(
            scope="project", project_dir=self.project.root, ides="opencode"
        )
        before = snapshot_tree(self.project.root)

        report = installer.uninstall(
            scope="project", project_dir=self.project.root, dry_run=True
        )

        self.assertTrue(report.ok, report.lines)
        self.assertEqual(snapshot_tree(self.project.root), before)
        self.assertGreater(report.counts.get("dry", 0), 0)


class InstallerCliTests(ProjectTestCase):
    init_project = False

    def test_cli_install_dry_run(self):
        result = self.cli("install", "--project", "--dry-run")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[dry]", result.stdout)
        self.assertEqual(snapshot_tree(self.project.root), set())

    def test_cli_install_then_uninstall(self):
        installed = self.cli("install", "--project", "--ide", "opencode")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertTrue((self.project.root / "AGENTS.md").is_file())

        removed = self.cli("uninstall", "--project")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse((self.project.root / "AGENTS.md").exists())
        self.assertFalse(
            (self.project.root / ".opencode/skills/tokenforge/SKILL.md").exists()
        )

    def test_cli_install_unknown_ide_exit_1(self):
        result = self.cli("install", "--project", "--ide", "nope")

        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown IDE", result.stdout)


class SymlinkRefusalTests(ProjectTestCase):
    init_project = False

    def test_symlinked_target_is_refused(self):
        outside_dir = Path(tempfile.mkdtemp(prefix="tokenforge-outside-"))
        self.addCleanup(shutil.rmtree, str(outside_dir), True)
        outside = outside_dir / "AGENTS.md"
        outside.write_text("outside content\n", encoding="utf-8")
        link = self.project.root / "AGENTS.md"
        create_symlink_or_skip(self, outside, link)

        report = installer.install(
            scope="project", project_dir=self.project.root, ides="opencode"
        )

        self.assertFalse(report.ok)
        self.assertIn("symlink", "\n".join(report.lines))
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside content\n")

    def test_symlinked_directory_is_refused(self):
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


class GlobalDryRunTests(ProjectTestCase):
    init_project = False

    def test_global_dry_run_never_writes(self):
        fake_home = Path(tempfile.mkdtemp(prefix="tokenforge-fakehome-"))
        self.addCleanup(shutil.rmtree, str(fake_home), True)
        (fake_home / ".claude").mkdir()
        (fake_home / ".cursor").mkdir()
        before = snapshot_tree(fake_home)

        original_home = detect.home_dir
        original_appdata = detect.appdata_dir
        detect.home_dir = lambda: fake_home
        detect.appdata_dir = lambda: None
        self.addCleanup(setattr, detect, "home_dir", original_home)
        self.addCleanup(setattr, detect, "appdata_dir", original_appdata)

        report = installer.install(scope="global", dry_run=True)

        self.assertTrue(report.ok, report.lines)
        self.assertEqual(report.counts.get("ok", 0), 0)
        self.assertGreaterEqual(report.counts.get("dry", 0), 3)
        self.assertEqual(snapshot_tree(fake_home), before)

    def test_global_install_skips_undetected_ides_without_force(self):
        fake_home = Path(tempfile.mkdtemp(prefix="tokenforge-fakehome-"))
        self.addCleanup(shutil.rmtree, str(fake_home), True)

        original_home = detect.home_dir
        original_appdata = detect.appdata_dir
        detect.home_dir = lambda: fake_home
        detect.appdata_dir = lambda: None
        self.addCleanup(setattr, detect, "home_dir", original_home)
        self.addCleanup(setattr, detect, "appdata_dir", original_appdata)

        report = installer.install(scope="global")

        self.assertTrue(report.ok, report.lines)
        self.assertEqual(report.counts.get("ok", 0), 0)
        self.assertEqual(report.counts.get("dry", 0), 0)
        self.assertGreater(report.counts.get("skip", 0), 0)
        self.assertEqual(snapshot_tree(fake_home), set())


if __name__ == "__main__":
    unittest.main()
