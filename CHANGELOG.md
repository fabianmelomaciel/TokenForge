# Changelog

## [1.0.0] — 2026-11-07

Initial release of TokenForge.

- Single AI skill (`SKILL.md`) compatible with opencode, Claude Code, Cursor,
  Windsurf, Codex, Gemini CLI and any AGENTS.md-compatible IDE.
- Python CLI (`forge`) that forges deterministic, tested `.py` handles once and
  runs them for ~0 tokens thereafter. Zero runtime dependencies, Python 3.8+.
- Persistent project memory in `.forge/memory.json`: metadata only, atomic
  writes, file locking, redaction of credential patterns, self-healing on
  corruption.
- Handle lifecycle: `init`, `new`, `run`, `test`, `list`, `show`, `search`,
  `rm`, `stats`, `doctor`, plus conservative token-savings estimation.
- Idempotent cross-IDE installer (`forge install`) with `--project` and
  `--global`, `--dry-run`, `--force`, `--ide` filtering, symlink refusal and
  backups of modified config files.
- 163 tests (155 run, 8 symlink skips on unprivileged Windows), 100% stdlib,
  CI on Ubuntu/macOS/Windows × Python 3.8, 3.9, 3.12, 3.13.
