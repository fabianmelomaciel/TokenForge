<!-- tokenforge:begin -->
# TokenForge

Read `SKILL.md` first. It is the source of truth for this repository.

Before re-deriving a repeatable task, check `.forge/memory.json` and run an
existing handle:

    python -m forge run <name> --json '{...}'

If none fits and the task is deterministic, forge and test one:

    python -m forge new <name> --task "..." && python -m forge test <name>

Review generated code. Never put secrets in `--task`.
<!-- tokenforge:end -->
