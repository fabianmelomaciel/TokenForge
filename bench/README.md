# TokenForge benchmark

`bench.py` is a deliberately boring micro-benchmark. It answers one question
honestly:

> How long does a forged handle take to run on this machine?

It does **not** answer "how many LLM tokens did I save", because a Python
script cannot observe an agent's token usage. The token column it prints is
TokenForge's own conservative estimate, and the output says so twice.

## Run it

```text
$ python bench/bench.py
$ python bench/bench.py --handles 10 --runs 20
$ python bench/bench.py --json
$ python bench/bench.py --keep
```

Options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--handles N` | 5 | How many handles to forge in the temporary project. |
| `--runs M` | 10 | Measured runs per handle (one warm-up run is extra). |
| `--timeout SECONDS` | 60 | Per-run timeout. |
| `--json` | off | Print a machine-readable summary instead of a table. |
| `--keep` | off | Keep the temporary project and print its path. |

The script creates a project under the system temporary directory, forges
`bench_00`, `bench_01`, ... handles, runs each one through
`sys.executable handle.py --json '{"name": "bench"}'`, verifies the JSON
contract, and deletes the project unless `--keep` is used. It never touches
your home directory.

## What the numbers mean

- **TOTAL MS** — wall-clock time for all measured runs of one handle.
- **MEAN MS/RUN** — arithmetic mean wall-clock time per run.
- **EST TOKENS SAVED** — `uses x assumed_manual_tokens x 0.8`, the same
  conservative estimate `python -m forge stats` shows. It is **not** measured
  LLM usage.

Every run starts a fresh Python interpreter, so `MEAN MS/RUN` is dominated by
interpreter startup plus your handle's work. That is realistic for the way
agents invoke handles (`python -m forge run ...`), but it is not a pure
function benchmark. If you want to isolate your logic, time `handle(payload)`
directly in your own harness.

Memory accounting (`update_usage`) runs *after* the timed section so lock
writes do not inflate `ms/run`.

## Measuring real tokens with an agent

If you need a real number, measure it by hand and report the raw data. Do not
copy the estimate.

1. **Pick one deterministic task** and freeze the exact prompt text. Write it
   down so every baseline run receives byte-identical input.
2. **Baseline.** In a fresh agent session with no TokenForge skill and no
   memory, run the prompt `N` times (`N >= 5`). For each run, record the
   provider-reported input and output tokens. Keep the transcripts.
3. **Forge.** Create and test the handle
   (`python -m forge new ...`, edit `handle()`, `python -m forge test ...`).
   Record the tokens spent forging and reviewing it as a one-off cost.
4. **Handle runs.** Run the handle `N` times. If an agent is involved at all,
   give it only the command to run and ask it to report stdout. Record tokens
   the same way.
5. **Compute.** `savings_per_run = median(baseline_tokens) -
   median(handle_tokens)`. Report the median, the min/max, `N`, the model and
   version, the date, and the exact prompts.
6. **Break even.** `forging_cost / savings_per_run` is the number of reuses
   needed before the handle pays for itself. A handle that is run twice rarely
   breaks even; that is expected.
7. **Do not extrapolate.** Savings depend on prompt length, model, caching,
   tool-call overhead and how much of the answer the agent would have written
   anyway. A number from one task is not a product-wide claim.

### Result table template

Use this shape when you publish a measurement, and leave cells empty rather
than inventing values:

| Task | Model | Baseline median tokens | Handle median tokens | Savings/run | Forging cost | Runs to break even |
| --- | --- | --- | --- | --- | --- | --- |
| _your task_ | _model + version_ | _measured_ | _measured_ | _computed_ | _measured_ | _computed_ |

## Why the estimate exists

`forge stats` needs a stable, conservative signal that does not require
network access or provider credentials. It assumes each successful run replaces
a manual derivation of `avg_tokens_manual` tokens (default 1500) and keeps only
80% of that to account for forging, review and verification overhead. You can
change the assumption per handle with `python -m forge new ... --avg-tokens N`
or in `.forge/config.json`. Changing the assumption changes the estimate; it
does not change reality.

If you report savings, say which number you are reporting: **measured tokens**
or **TokenForge estimate**. Mixing them is how benchmarks become fiction.
