# The LA personalization lab kit

Every aspect of LA personalization is a branch (`lab/<aspect>/aspect`), and every way of doing it
is a case branch (`lab/<aspect>/<case>`) with a mock test, three live runs and a pass bar written
before the runs. Every number is produced by code and explained by code. This directory is the kit
that makes that so. Design: the wiki's `meta/spec-2026-11-10-la-personalization-lab` and its test
tree `meta/spec-2026-11-10-la-personalization-lab-test-tree`.

## How a case works (the contract every case follows)

1. **Manifest first.** A case is a `case.json`: the question, why, the hypothesis, the datasets,
   the metrics, and the pass bar ("keep if"). The case (manifest, mock and pipeline) is committed
   and pushed before any live run. The runner refuses a live run otherwise (`guard`), every result
   records the case's hash (`case_sha`), and a case changed after its runs fails `check`.
2. **Mock.** A deterministic test with a fake model and fixed fixtures. It proves the plumbing and
   the scoring, costs nothing, and runs in CI.
3. **Three live runs.** Model output is not deterministic and the chatbot's API wrapper drops
   `temperature`, so one run proves nothing. Ranges are reported; two cases are called different
   only if their run ranges do not overlap.
4. **Result files.** `mock.json`, `run1.json` to `run3.json` (and `real_run1.json` to
   `real_run3.json` for a real dataset), each with the dataset and its checksum, the model, every
   metric, the cost and a `steps` list (what happened at each step and how many items went in and
   out). For synthetic data the per-item rows are saved too, so any number can be traced to the
   persona and turn behind it.
5. **Verdict by code.** `verdict.json` compares the runs with the pass bar and says keep, drop or
   inconclusive. A person can add a `note`; a person cannot change the computed verdict.
6. **Record.** The tree is regenerated and the wiki pages are published before the next case starts.

## A case directory

```
L/cases/<aspect>/aspect.json        {id, question, why, source}
L/cases/<aspect>/metrics.py         optional: METRICS = {name: Metric(...)}, each with a formula
L/cases/<aspect>/<case>/case.json   the manifest
                       pipeline.py  the code under test (below)
                       test_mock.py the mock, run in CI
                       results/     mock.json, run1..3.json, verdict.json, calls_run<n>.json
```

`case.json` fields: `question, why, source, hypothesis, engine ("offline"|"app"), task
("replay"|"label"|"pairs"|"script"), config, datasets, metrics, keep_if, cost_estimate, status
("planned"|"blocked"), blocked_reason`. `load_case` refuses an unknown metric or dataset, an empty
hypothesis, a planned case with no bars, a blocked case with no reason, and status `draft`.

A bar: `{metric, agg, op, value | ref, dataset, expected_to_fail}`. `metric` is `name` or
`name@variant`; `agg` is `mean`, `min`, `max`, `pooled` (adds the runs' numerators and
denominators) or `each` (must hold in every run). A `ref` bar compares with
`factor x (another case's aggregated metric) + offset`, read from `origin/lab/<aspect>/<case>` with
`git show`; a missing reference makes the bar `inconclusive`.

Metric keys in a result: `name`, `name@variant` (a design or arm of `config.variants`), and for a
metric with `params` (`fill_rate_at_k`: 2, 4, 8) `name@param` or `name@variant/param`.

### The pipeline

`pipeline.py` exposes one function by `task`:

- `replay`: `build(config, llm, typed=None) -> Pipeline` with `on_turn(user_id, turn)`,
  `run_job(user_id, now)`, `profile(user_id) -> {slot: value}` and `steps() -> [names from steps.py]`.
  Optional methods (profile detail, proposals, observations, sent texts, call rows, stats, step
  counts, `set_stated`, `apply_interview`, `conflicts_marked`) are listed in `replay.Pipeline`.
  The runner builds one pipeline per persona and per variant. `replay.strategy_pipeline` is a ready
  `build` for the five iteration-1 designs (`config.strategy`, or each of `config.variants`).
- `label`: `predict(item, config, llm) -> str` (`"unknown"` is a legal label; `"invalid"` counts as invalid).
- `pairs`, `script`: run by the lab app (see the plan, Task 15).

Optional hooks for `LAB run <id> --mock`: `mock_llm()`, `mock_typed()`, `mock_items()`. Without
them the mock uses an empty fake model on the case's first dataset. Other optional hooks:
`select_items(items, config, dataset) -> items` (a reproducible subset, such as a seeded sample,
applied before every run) and `explain_lines(case, read) -> [str]` (extra lines for `LAB explain`;
`read(name)` returns a result file or None).

A `replay` pipeline module may also define `counts(variant_config, replays, llm) -> dict`, called once per
variant, whose result becomes that variant's `RunData.counts` (a case that makes no extraction calls
uses it for `overhead_tokens` and `recount_gap`).

A pipeline for answer-side metrics reports `RunData.counts`: a ratio metric reads `counts[name]`
over `counts[name + "_of"]`; a count metric reads `counts[name]`.

## Datasets

`data/datasets.json` registers each dataset. A synthetic one lists its files and a sha256 over
them; `load_dataset` raises `DatasetChanged` when they differ, and refuses a dataset with no
checksum yet. A real dataset is read only from `$LA_LAB_DATA_DIR/<name>/` (`items.json`, `sha256`),
outside every repository. A real-dataset result carries numbers only (`results.write` raises
`RealDataLeak` otherwise), run progress stays under `$LA_LAB_DATA_DIR/partial/<id>/`, and Jev is
refused on non-anonymized real data (`JevOnIdentifiableData`). Persona schema: see `data/syn1/personas.json`, plus optional
`turns[].tags` and `golden_timeline`.

## Command line

`scripts/lab.sh <command>` (from the worktree), or `python -m chat.V2.personalization.lab`.

| Command | What it does |
|---|---|
| `validate <id>` / `validate --all --min-cases <n>` | load the manifest(s); `--all` fails if fewer than `n` cases exist |
| `run <id> --mock` | run the case on its mock fixture, write `results/mock.json` |
| `run <id> --live --runs 3 [--dataset <name>] [--resume]` | refuse unless pre-registered; run and write `run<n>.json` (or `real_run<n>.json` for a real dataset) |
| `verdict <id>` | compute `results/verdict.json` from the result files (keeps the person's `note`) |
| `explain <id>` | the question, hypothesis, each metric's formula, the bars and the numbers so far |
| `check <id>` | exit 0 only if `mock.json`, `run1..3.json` and `verdict.json` exist, share the current `case_sha`, and every synthetic dataset checksum still matches; 3 for a blocked case; 1 otherwise |

Model calls go through headless Claude Code (`llm.ClaudeCodeLLMClient`) and Codex (`judge.CodexJudge`) only.
