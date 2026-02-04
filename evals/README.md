# Evaluations

Braintrust evaluations for the LC Chatbot.

## Setup

1. Ensure `BRAINTRUST_API_KEY` is set (already in `server/.env`)
2. Start the backend server: `./start.sh` or `python manage.py runserver 0.0.0.0:8001`
3. Create a dataset in Braintrust UI

## Running Evaluations

```bash
# Basic run (uses default dataset)
python evals/run_eval.py

# Specify dataset and experiment name
python evals/run_eval.py --dataset "My Dataset" --experiment "Test Run"

# Full options
python evals/run_eval.py \
  --dataset "My Dataset" \
  --experiment "v1.2 Release Test" \
  --concurrency 5 \
  --api-url http://localhost:8001
```

## Configuration

Environment variables:
- `BRAINTRUST_API_KEY` - Required
- `BRAINTRUST_PROJECT` - Project name (default: "On Site Agent")
- `CHATBOT_API_URL` - API base URL (default: http://localhost:8001)
- `CHATBOT_API_KEY` - API key for auth (default: test-key)

## Adding Custom Scorers

### Built-in Scorers

The script includes basic scorers that work without Braintrust UI setup:
- `has_response` - Checks for non-empty response
- `response_length` - Normalized length score
- `contains_citation` - Checks for Sefaria citations

### Braintrust UI Scorers

To use custom scorers defined in Braintrust UI:

```python
from evals.run_eval import create_scorer, run_evaluation

scorers = [
    create_scorer("accuracy-scorer", "Measures factual accuracy"),
    create_scorer("relevance-scorer", "Measures relevance to query"),
    create_scorer("citation-quality", "Evaluates citation quality"),
]

asyncio.run(run_evaluation(scorers=scorers))
```
