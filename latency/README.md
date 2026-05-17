## Latency

This folder is organized around the ongoing latency workflow, not one-off exploration.

### Structure

- `scripts/`
  - current active entrypoints and analysis implementation
- `archive/`
  - older or secondary scripts that are not part of the main beta baseline workflow
- `runs/`
  - generated baseline run outputs
- `analysis/`
  - generated latency analysis exports and plots
- `tests/`
  - tests for the latency analysis code

### Main workflow

1. Baseline dataset lives in Braintrust.
2. `run_baseline_beta_from_braintrust.py` pulls that dataset and runs it against beta.
3. Each run writes local artifacts to `runs/` and also logs a Braintrust experiment.
4. `analyze_experiment_latency.py` takes one Braintrust experiment, fetches its trace/span data, and writes generated outputs to `analysis/`.

### Primary entrypoints

- `latency/scripts/run_baseline_beta_from_braintrust.py`
  - run the baseline dataset against beta and create a Braintrust experiment
- `latency/scripts/analyze_experiment_latency.py`
  - analyze one Braintrust experiment end-to-end and produce the plots and tables

### Archived scripts

- `latency/archive/run_baseline_local.py`
  - local replay runner kept for reference
- `latency/archive/sample_braintrust_questions.py`
  - older local sampling utility now superseded by the Braintrust dataset workflow
- `latency/archive/upload_questions_dataset_to_braintrust.py`
  - one-off dataset upload utility kept for reference

### Notes

- Generated data under `runs/` and `analysis/` is not source-of-truth and should stay out of git.
- The intended workflow now starts from an existing Braintrust dataset rather than rebuilding local sampled datasets.
