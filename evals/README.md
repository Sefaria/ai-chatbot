# Evaluations

Braintrust evaluations for the LC Chatbot.

This code uses the Braintrust SDK to trigger "experiment" evaluation runs against the Sefaria AI bot. It can be configured to run against specific scroers (defined in Braintrust) or all scorers, specific datasets, and against local or prod. 

## Setup

1. Ensure `BRAINTRUST_API_KEY` is set (already in `server/.env`)
2. Set `CHATBOT_USER_TOKEN` - encrypted auth token (contact engineering team to obtain)
3. Start the backend server: `./start.sh` or `python manage.py runserver 0.0.0.0:8001`
4. Create a dataset in Braintrust UI

## Running Evaluations

```bash
# Run with specific scorers (local server)
python evals/run_eval.py --scorers "politics-7365,non-psak-e2b5"

# Run with all scorers from Braintrust UI (excludes TEST_ prefixed scorers)
python evals/run_eval.py --all-scorers

# Run against production API
python evals/run_eval.py --prod --scorers "politics-7365"

# Specify dataset and experiment name
python evals/run_eval.py --dataset "My Dataset" --experiment "Test Run" --scorers "politics-7365"

# Full options
python evals/run_eval.py \
  --dataset "Benchmark" \
  --experiment "v1.2 Release Test" \
  --concurrency 5 \
  --all-scorers
```

## CLI Options

| Option | Description |
|--------|-------------|
| `--dataset, -d` | Braintrust dataset name (default: "Benchmark") |
| `--experiment, -e` | Experiment name (default: auto-generated with timestamp) |
| `--concurrency, -c` | Max concurrent evaluations (default: 3) |
| `--api-url` | Custom API URL |
| `--prod` | Use production API (https://chat-dev.sefaria.org) |
| `--scorers, -s` | Comma-separated list of Braintrust scorer slugs |
| `--all-scorers` | Use all scorers defined in Braintrust UI (excludes TEST_ prefixed) |

## Configuration

Environment variables:
- `BRAINTRUST_API_KEY` - Required
- `CHATBOT_USER_TOKEN` - Required, encrypted auth token (contact engineering team)
- `BRAINTRUST_PROJECT` - Project name (default: "On Site Agent")
- `CHATBOT_API_URL` - API base URL (default: http://localhost:8001)

## Scorers

All scorers are defined in Braintrust UI. Use `--all-scorers` to fetch and run all available scorers (excluding those prefixed with `TEST_`), or specify individual slugs with `--scorers`.

To see available scorers:
```bash
python -c "
import os
os.chdir('server')
from dotenv import load_dotenv
load_dotenv()
import sys
sys.path.insert(0, '..')
from evals.run_eval import get_all_project_scorers
get_all_project_scorers()
"
```

### Using Scorers Programmatically

```python
import asyncio
from evals.run_eval import create_scorer, run_evaluation

scorers = [
    create_scorer("politics-7365"),
    create_scorer("non-psak-e2b5"),
    create_scorer("link-are-valid-06b8"),
]

asyncio.run(run_evaluation(scorers=scorers))
```
