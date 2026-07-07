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

### Running the beta baseline

Anyone with the repo and the required credentials can run the baseline against
`chat-beta.sefaria.org`.

Required credentials:

1. Braintrust API access
   - set `BRAINTRUST_API_KEY` in your shell or local env
2. Beta chatbot auth
   - create `server/.env.beta.local`
   - add the beta secret:

```bash
CHATBOT_BETA_BASE_URL=https://chat-beta.sefaria.org
CHATBOT_USER_TOKEN_SECRET_BETA=...
```

To create a beta chatbot token manually from that secret:

```bash
python evals/generate_prod_token.py --secret "$CHATBOT_USER_TOKEN_SECRET_BETA" --user-id eval-user
```

That prints a token you can save as:

```bash
CHATBOT_USER_TOKEN_BETA=...
```

In practice, the beta baseline script will generate `CHATBOT_USER_TOKEN_BETA`
for you automatically from `CHATBOT_USER_TOKEN_SECRET_BETA`, so storing the
secret is usually enough.

`server/.env.beta.local` is local-only and ignored by git.

How to run:

1. Run the beta baseline dataset:

```bash
python3 latency/scripts/run_baseline_beta_from_braintrust.py
```

2. Analyze the latest beta experiment:

```bash
python3 latency/scripts/analyze_experiment_latency.py
```

### Running the local agent-loop probe

For detailed local JSONL traces of the agent loop, edit the `PROBE_CONFIG`
block at the top of:

```bash
latency/scripts/run_agent_loop_probe.py
```

Then run the probe directly:

```bash
python3 latency/scripts/run_agent_loop_probe.py
```

The probe starts a local Django backend with `AGENT_FILE_TRACE_ENABLED=1`, replays
the configured Braintrust dataset against `http://127.0.0.1:8001`, and writes:

```text
latency/runs/<run_id>/
  agent_debug_events.jsonl
  agent_debug_turn_summary.csv
  results.jsonl
  summary.json
  manifest.json
  backend.log
```

Set `PROBE_CONFIG["limit"] = None` for the full dataset. Use
`PROBE_CONFIG["max_concurrency"] = 1` for the clearest per-turn trace ordering.
