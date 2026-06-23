# Running Evals

Evaluations run the LC Chatbot against a [Braintrust](https://www.braintrust.dev/) dataset, score responses using LLM-based scorers defined in the Braintrust UI, and log results for comparison across experiments.

## Prerequisites

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `BRAINTRUST_API_KEY` | Yes | API key for Braintrust |
| `CHATBOT_USER_TOKEN` | Yes | Encrypted auth token for the chatbot API |
| `BRAINTRUST_PROJECT` | No | Project name (default: `"On Site Agent"`) |

These are loaded automatically from `server/.env` if present.

### Install Dependencies

```bash
pip install braintrust httpx python-dotenv
```

## Usage

```bash
# Run with all project scorers (most common)
python evals/run_eval.py --all-scorers

# Run with specific scorer(s) by slug
python evals/run_eval.py --scorers accuracy-374376eb2
python evals/run_eval.py --scorers scorer-one,scorer-two

# Run against local dev server instead of production
python evals/run_eval.py --all-scorers --local

# Custom dataset and experiment name
python evals/run_eval.py --all-scorers -d "My Dataset" -e "My Experiment"

# Increase concurrency (default: 3)
python evals/run_eval.py --all-scorers -c 5
```

## Options

| Flag | Short | Description |
|---|---|---|
| `--all-scorers` | | Use all scorers defined in Braintrust for the project |
| `--scorers` | `-s` | Comma-separated list of scorer slugs |
| `--dataset` | `-d` | Braintrust dataset name (default: `"Benchmark"`) |
| `--experiment` | `-e` | Experiment name (default: auto-generated with timestamp) |
| `--concurrency` | `-c` | Max concurrent API calls (default: `3`) |
| `--local` | | Use local server (`http://localhost:8001`) instead of production (`https://chat-dev.sefaria.org`) |

## How It Works

1. **Validates** the dataset and scorers exist in Braintrust before running
2. **Loads** the dataset from Braintrust (rows contain a prompt field like `input`, `prompt`, `query`, or `message`)
3. **Sends** each prompt to the chatbot's streaming API, collecting the final markdown response
4. **Scores** each response using the specified Braintrust scorers (LLM-based evaluators)
5. **Logs** results to Braintrust under the given project and experiment name

Results are viewable in the Braintrust UI under the project and experiment name.

## Tests

```bash
pytest evals/test_run_eval.py
```
