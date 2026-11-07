<!-- tokenforge:begin -->
# TokenForge

Read `SKILL.md` before doing repeatable work. Use the loop:

1. CHECK `.forge/memory.json` (`python -m forge list` or `search`).
2. RUN an existing handle: `python -m forge run <name> --json '{...}'`.
3. FORGE only when deterministic: `python -m forge new <name> --task "..."`.
4. TEST: `python -m forge test <name>`.
5. REUSE, and edit the handle when the task changes.

Never re-derive what a handle already does. Never put secrets in `--task`.
<!-- tokenforge:end -->
