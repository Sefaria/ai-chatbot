# JSON Parsing Fix for AI Responses

## Problem

The AI classifiers (guardrails and router) were sometimes returning JSON followed by explanatory text, like:

```
{
  "decision": "ALLOW",
  "reason_codes": [],
  "refusal_message": "",
  "confidence": 1.0
}

Explanation:
This is a legitimate halachic question...
```

This caused `json.loads()` to fail because the extra text after the JSON object is not valid JSON.

## Solution

Implemented a **two-pronged approach**:

### 1. Improved JSON Extraction

Added `_extract_json()` method to both `AIGuardrailChecker` and `AIFlowRouter` that:

- Handles markdown code blocks (```json ... ```)
- Finds JSON object boundaries by counting braces
- Extracts only the JSON portion, ignoring trailing text
- Works even if AI adds explanations before or after

**Implementation** ([ai_guardrails.py](server/chat/V2/router/ai_guardrails.py) and [ai_router.py](server/chat/V2/router/ai_router.py)):

```python
def _extract_json(self, text: str) -> str:
    """Extract JSON from AI response text."""
    # Handle markdown code blocks
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # Find JSON object boundaries
    start_idx = text.find('{')
    if start_idx == -1:
        return text

    # Count braces to find matching closing brace
    brace_count = 0
    end_idx = start_idx
    for i in range(start_idx, len(text)):
        if text[i] == '{':
            brace_count += 1
        elif text[i] == '}':
            brace_count -= 1
            if brace_count == 0:
                end_idx = i + 1
                break

    return text[start_idx:end_idx]
```

### 2. Updated Prompts

Modified fallback prompts in [braintrust_client.py](server/chat/V2/router/braintrust_client.py) to explicitly instruct:

**Before:**
```
Output your decision as JSON with this exact structure:
{...}
```

**After:**
```
CRITICAL: Output ONLY valid JSON, with no additional text before or after. Use this exact structure:
{...}

Remember: OUTPUT ONLY JSON, NO EXPLANATORY TEXT.
```

## How It Works

### Example 1: JSON with Trailing Text

**Input:**
```
{
  "decision": "ALLOW",
  "confidence": 1.0
}

Explanation: This is valid...
```

**Extraction:**
1. Find first `{` at index 0
2. Count braces: open=1, then close=1 at `}` after confidence
3. Extract `text[0:30]` (just the JSON object)
4. Parse successfully ✅

### Example 2: Markdown Code Block

**Input:**
```
```json
{
  "flow": "HALACHIC"
}
```
Here's why...
```

**Extraction:**
1. Detect ` ```json` delimiter
2. Extract content between ` ```json` and ` ``` `
3. Parse extracted JSON ✅

### Example 3: Plain JSON

**Input:**
```json
{"decision": "ALLOW"}
```

**Extraction:**
1. No markdown delimiters found
2. Find `{` at start
3. Count braces, extract full object
4. Parse successfully ✅

## Files Modified

- [ai_guardrails.py](server/chat/V2/router/ai_guardrails.py)
  - Added `_extract_json()` method
  - Updated `_check_with_ai()` to use it

- [ai_router.py](server/chat/V2/router/ai_router.py)
  - Added `_extract_json()` method
  - Updated `_classify_with_ai()` to use it

- [braintrust_client.py](server/chat/V2/router/braintrust_client.py)
  - Updated `_get_fallback_guardrail_prompt()` - emphasized JSON-only output
  - Updated `_get_fallback_router_prompt()` - emphasized JSON-only output

## Testing

The fix handles all these cases:

```python
from chat.router import get_ai_guardrail_checker

checker = get_ai_guardrail_checker()

# Test with various response formats
test_responses = [
    '{"decision": "ALLOW"}',  # Plain JSON
    '{"decision": "ALLOW"}\n\nExplanation...',  # JSON + text
    '```json\n{"decision": "ALLOW"}\n```',  # Markdown
    'Here is my decision:\n{"decision": "ALLOW"}',  # Prefixed
]

# All should parse successfully now
for response in test_responses:
    try:
        json_text = checker._extract_json(response)
        result = json.loads(json_text)
        print(f"✓ Parsed: {result}")
    except Exception as e:
        print(f"✗ Failed: {e}")
```

## Benefits

1. **Robust Parsing**: Handles multiple response formats
2. **Backward Compatible**: Still works with clean JSON responses
3. **Fail-Safe**: Falls back to rule-based if parsing still fails
4. **Future-Proof**: Works even if AI changes response format

## Braintrust Prompt Updates

When updating prompts in Braintrust, make sure to include:

```
CRITICAL: Output ONLY valid JSON, with no additional text before or after.

Remember: OUTPUT ONLY JSON, NO EXPLANATORY TEXT.
```

This helps ensure cleaner responses from the start, though the extraction logic provides a safety net.

## Alternative Approaches Considered

1. **Regex Extraction**: Less reliable for nested JSON
2. **Stricter Temperature**: `temperature=0` already used, but AI still adds text
3. **Response Format Param**: Not available in Anthropic API (yet)
4. **Retry on Parse Failure**: Adds latency and cost

The brace-counting approach is **most reliable** and handles edge cases well.

## Monitoring

Check logs for parsing issues:

```bash
# Look for JSON parsing errors
grep "Failed to parse AI response" logs/app.log

# See what the AI is actually returning
grep "AI guardrail response:" logs/app.log
grep "AI router response:" logs/app.log
```

If you see frequent parsing errors even with the fix, it might indicate:
- Prompts need further refinement
- Model producing malformed JSON
- Need to adjust extraction logic

## Future Improvements

Potential enhancements:

1. **Structured Outputs**: When Anthropic adds native JSON mode
2. **Schema Validation**: Add JSON schema validation
3. **Metrics**: Track parsing success rate
4. **Retry Logic**: Retry with modified prompt on parse failure
