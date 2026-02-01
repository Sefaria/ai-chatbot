# Core Prompt Implementation

## Summary

Added support for a "core" system prompt (slug: `core-8fbc`) that is loaded from Braintrust and prepended to all chat sessions with the chatbot.

## What Was Added

### 1. Core Prompt Loading in Braintrust Client

**File**: [braintrust_client.py](server/chat/V2/router/braintrust_client.py)

Added `get_core_prompt()` method:
```python
from chat.router import get_braintrust_client

client = get_braintrust_client()
core_prompt = client.get_core_prompt(version="stable")  # Loads from slug "core-8fbc"
```

**Features**:
- Fetches from Braintrust with slug `core-8fbc`
- Caches for performance
- Falls back to hardcoded prompt if Braintrust unavailable
- Handles multiple prompt formats flexibly

### 2. Integration with Prompt Service

**File**: [prompt_service.py](server/chat/V2/prompts/prompt_service.py)

Updated `get_prompt_bundle()` to use the new core prompt:
- Changed default `core_prompt_id` from `"bt_prompt_core"` to `"core-8fbc"`
- Routes core prompt loading through the router's Braintrust client
- Maintains backward compatibility with legacy prompt IDs

### 3. Fallback Core Prompt

If Braintrust is unavailable, the system uses a hardcoded fallback prompt that:
- Defines the chatbot as a Jewish learning assistant
- Outlines key principles (accuracy, humility, context, sources, respect)
- Provides guidance on halacha discussions
- Lists available tools and capabilities

## Usage

### Automatic (Default Behavior)

The core prompt is automatically loaded and prepended to all chat sessions:

```python
from chat.prompts import get_prompt_service

service = get_prompt_service()
bundle = service.get_prompt_bundle(flow="HALACHIC")

# bundle.core_prompt contains the core-8fbc prompt
# bundle.system_prompt is the combined core + flow prompt
```

### Manual Access

You can also access the core prompt directly:

```python
from chat.router import get_braintrust_client

client = get_braintrust_client()
core_prompt = client.get_core_prompt(version="stable")
print(core_prompt)
```

### Different Versions

```python
# Use development version
core_prompt = client.get_core_prompt(version="dev")

# Use specific version
core_prompt = client.get_core_prompt(version="v1.2.3")

# Use stable (default)
core_prompt = client.get_core_prompt()  # defaults to "stable"
```

## Setting Up in Braintrust

### 1. Create Prompt in Braintrust UI

1. Navigate to your Braintrust project (`sefaria-chatbot` by default)
2. Create a new prompt with:
   - **Slug**: `core-8fbc`
   - **Type**: Text/System prompt
   - **Content**: Your core system prompt text

Example structure:
```
You are a knowledgeable Jewish learning assistant...

Key principles:
1. Accuracy: ...
2. Humility: ...
...

[Your full core prompt here]
```

### 2. Version the Prompt

Create versions for different environments:
- `dev`: For development and testing
- `staging`: For pre-production testing
- `stable`: For production use

### 3. Remote Updates

Once set up, you can update the core prompt in Braintrust without code deployment:

```python
# Changes take effect after cache expires (default: 5 minutes)
# Or force immediate reload:
from chat.router import get_braintrust_client
get_braintrust_client().invalidate_cache()
```

## Testing

The test suite has been updated to test core prompt loading:

```bash
# Run test suite
python manage.py shell
>>> from chat.router.test_ai_system import run_tests
>>> run_tests()
```

Expected output:
```
=== Testing Braintrust Client ===
✓ Braintrust client initialized
  - API Key configured: True
  - Project: sefaria-chatbot
✓ Core prompt loaded (slug: core-8fbc)
  - Prompt length: 1234 chars
  - Preview: You are a knowledgeable Jewish learning assistant...
```

## Configuration

### Environment Variables

No new environment variables required. Uses existing Braintrust configuration:

```bash
BRAINTRUST_API_KEY=your_api_key        # Optional - for remote prompts
BRAINTRUST_PROJECT=sefaria-chatbot     # Optional - project name
```

### Fallback Behavior

If `BRAINTRUST_API_KEY` is not set or Braintrust is unavailable:
- System uses hardcoded fallback core prompt
- Logs warning but continues to function
- No impact on chat functionality

## File Changes

### New Functionality
- [braintrust_client.py](server/chat/V2/router/braintrust_client.py)
  - Added `get_core_prompt()` method
  - Added `_get_fallback_core_prompt()` helper

### Modified Files
- [prompt_service.py](server/chat/V2/prompts/prompt_service.py)
  - Updated `get_prompt_bundle()` to use `core-8fbc`
  - Added router Braintrust client integration
  - Changed default core_prompt_id parameter

- [test_ai_system.py](server/chat/V2/router/test_ai_system.py)
  - Added core prompt loading test

- [README.md](server/chat/V2/router/README.md)
  - Documented core prompt feature
  - Updated Braintrust setup instructions

## Migration Notes

### For Existing Deployments

**No Breaking Changes**: The system is fully backward compatible.

1. **Without Braintrust**: Uses fallback core prompt (works immediately)
2. **With Braintrust**: Set up the `core-8fbc` prompt, or system uses fallback
3. **Gradual Migration**: Can test core-8fbc in dev before promoting to stable

### Updating from Legacy Core Prompt

If you were using `bt_prompt_core`:

```python
# Old (still works)
bundle = service.get_prompt_bundle(
    flow="HALACHIC",
    core_prompt_id="bt_prompt_core"  # Uses old loading method
)

# New (default)
bundle = service.get_prompt_bundle(
    flow="HALACHIC"  # Defaults to core-8fbc
)
```

## Benefits

1. **Remote Updates**: Update core prompt without code deployment
2. **Version Control**: Test changes in dev before production
3. **Consistency**: Single source of truth for chatbot identity
4. **Flexibility**: Easy to customize for different environments
5. **Reliability**: Automatic fallback ensures system always works

## Example Workflow

### Development Flow

1. Create new core prompt version in Braintrust:
   ```
   Slug: core-8fbc
   Version: dev
   Content: [Your updated prompt]
   ```

2. Test with development configuration:
   ```python
   client = get_braintrust_client()
   dev_prompt = client.get_core_prompt(version="dev")
   ```

3. Verify in chat sessions (dev environment automatically uses dev version)

4. Promote to stable when ready:
   - Copy from `dev` version to `stable` version in Braintrust
   - Changes propagate to production after cache expiry

### Production Updates

1. Update `stable` version in Braintrust UI
2. Changes take effect after cache TTL (5 minutes default)
3. Force immediate update if needed:
   ```python
   get_braintrust_client().invalidate_cache()
   ```

## Troubleshooting

### Core Prompt Not Loading

1. **Check Braintrust API Key**:
   ```bash
   echo $BRAINTRUST_API_KEY
   ```

2. **Verify Prompt Exists**:
   - Login to Braintrust
   - Check project has prompt with slug `core-8fbc`
   - Verify desired version exists

3. **Check Logs**:
   ```bash
   grep "core prompt" logs/app.log
   ```

4. **Test Loading**:
   ```python
   from chat.router import get_braintrust_client
   client = get_braintrust_client()
   try:
       prompt = client.get_core_prompt()
       print(f"Loaded: {len(prompt)} chars")
   except Exception as e:
       print(f"Error: {e}")
   ```

### Using Fallback Instead of Braintrust

If system keeps using fallback:
- Verify `BRAINTRUST_API_KEY` is set correctly
- Check Braintrust project name matches `BRAINTRUST_PROJECT` env var
- Ensure slug is exactly `core-8fbc` (case-sensitive)
- Try invalidating cache: `client.invalidate_cache()`

## Next Steps

1. **Create Core Prompt in Braintrust**: Set up the `core-8fbc` prompt
2. **Test Different Versions**: Experiment with dev/staging/stable
3. **Monitor Usage**: Check logs to verify prompt loading
4. **Iterate**: Update prompt based on chat interactions

## Related Documentation

- [Main Router & Guardrails README](server/chat/V2/router/README.md)
- [Implementation Summary](IMPLEMENTATION_SUMMARY.md)
- [Prompt Service Documentation](server/chat/V2/prompts/README.md)
