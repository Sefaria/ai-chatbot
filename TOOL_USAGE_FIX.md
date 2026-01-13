# Tool Usage Fix

## Problem

After implementing the remote prompt system using Braintrust, the AI stopped using Sefaria tools entirely. The chatbot would answer questions from memory instead of using the provided tools (`get_text`, `text_search`, `english_semantic_search`, etc.).

## Root Causes

### 1. Generic Fallback Core Prompt

The fallback core prompt in [braintrust_client.py](server/chat/router/braintrust_client.py) was too generic and lacked explicit tool usage instructions.

**Before (Generic):**
```python
return """You are a knowledgeable Jewish learning assistant with access to Sefaria's library.

You have access to tools for:
- Searching texts
- Retrieving specific passages
- Finding topics and connections

Use these tools actively when answering questions about Jewish sources."""
```

This was too vague and didn't **mandate** tool usage.

### 2. Conditional Logic Issue

The conditional in [prompt_service.py](server/chat/prompts/prompt_service.py) was incorrectly simplified:

**Before (Broken):**
```python
if core_prompt_id:  # Too broad, tries to use router client for all prompt IDs
    try:
        core_prompt = self._router_braintrust_client.get_core_prompt(version)
```

**After (Fixed):**
```python
if core_prompt_id == "core-8fbc" and hasattr(self, '_router_braintrust_client') and self._router_braintrust_client:
    try:
        core_prompt = self._router_braintrust_client.get_core_prompt(version)
```

The simplified condition would try to call `get_core_prompt()` even when the router client wasn't available or for non-core-8fbc prompt IDs.

## Solution

### 1. Updated Fallback Core Prompt

Updated `_get_fallback_core_prompt()` in [braintrust_client.py](server/chat/router/braintrust_client.py) to include **explicit, emphatic** tool usage instructions:

```python
def _get_fallback_core_prompt(self) -> str:
    """Get the hardcoded fallback core system prompt."""
    return """You are a Jewish text scholar and learning companion with access to Sefaria's vast library of Jewish texts.

IDENTITY & VOICE:
• You are knowledgeable, approachable, and deeply respectful of Jewish learning traditions
• You engage users as a study partner (chavruta), not just an information retrieval system
• You balance scholarly rigor with accessibility
• You acknowledge the diversity of Jewish thought and practice

TOOL USAGE (CRITICAL):
• You MUST use the provided Sefaria tools to search for and retrieve Jewish texts
• NEVER answer questions about Jewish texts, sources, or references from memory alone
• For specific text requests: USE get_text
• For finding sources: USE text_search or english_semantic_search
• For topics and figures: USE get_topic_details
• For calendar questions: USE get_current_calendar
• For text connections: USE get_links_between_texts
• When uncertain which tool to use: prefer text_search or english_semantic_search first

RESPONSE REQUIREMENTS:
• Respond in the same language the user asked in
• Gauge user intent - short answers for simple questions, comprehensive for complex ones
• ALL claims must be sourced with Sefaria links: [Source Name](https://www.sefaria.org/Reference)
• If making unsourced claims, explicitly note: "Based on my analysis (not from a specific source):"
• Begin responses directly with substantive content
• FORBIDDEN: "Let me search," "I'll gather," "Now let me," "I found," "Let me look," or any process descriptions
• Users should only see your final scholarly conclusions

HALACHA GUIDANCE:
• When discussing halacha (Jewish law), provide educational information, not definitive rulings
• Make clear that you're not a rabbi and cannot provide authoritative psak
• For serious matters (pikuach nefesh, medical, legal, lifecycle events), direct users to consult a qualified rabbi
• Show the range of opinions where relevant and acknowledge when there's machloket (disagreement)

CITATION FORMAT:
• Always include clickable Sefaria links for all sources cited
• Format: [Book Chapter:Verse](https://www.sefaria.org/Book.Chapter.Verse)
• For Talmud: [Tractate Daf](https://www.sefaria.org/Tractate.Daf)

MARKDOWN FORMATTING:
• Use standard markdown: # headers, **bold**, *italic*
• Links: [Text](URL)
• Lists: - or 1.
• Blockquotes: > for quoted text"""
```

**Key Changes:**
- Added **"TOOL USAGE (CRITICAL)"** section header to draw attention
- Used **"MUST use"** and **"NEVER answer from memory"** - emphatic language
- Listed specific tool usage patterns with explicit instructions
- Added **"FORBIDDEN"** language for process descriptions

### 2. Fixed Conditional Logic

Restored proper conditional check in [prompt_service.py](server/chat/prompts/prompt_service.py:127-139):

```python
# Fetch core prompt using the router's Braintrust client (supports core-8fbc slug)
if core_prompt_id == "core-8fbc" and hasattr(self, '_router_braintrust_client') and self._router_braintrust_client:
    try:
        core_prompt = self._router_braintrust_client.get_core_prompt(version)
        core_version = version
        logger.debug(f"Fetched core prompt via router client: {len(core_prompt)} chars")
    except Exception as e:
        logger.warning(f"Failed to fetch core prompt via router client: {e}, falling back to legacy method")
        core_prompt, core_version = self._get_prompt(core_prompt_id, version, 'core')
else:
    # Fetch using legacy method
    logger.debug(f"Using legacy method for core prompt: {core_prompt_id}")
    core_prompt, core_version = self._get_prompt(core_prompt_id, version, 'core')
```

This ensures:
1. Only attempts router client method for `core-8fbc` slug
2. Checks that router client exists before calling it
3. Adds debug logging to track which method is used
4. Falls back gracefully if router method fails

### 3. Fixed Python 3.7 Compatibility

Fixed type annotation issues for Python 3.7 compatibility:

**Files Updated:**
- [router_service.py](server/chat/router/router_service.py): Changed `tuple[...]` → `Tuple[...]`
- [prompt_service.py](server/chat/prompts/prompt_service.py): Changed `tuple[...]` → `Tuple[...]`

Added `from typing import Tuple` imports and replaced all lowercase `tuple[...]` annotations with `Tuple[...]`.

## Testing

Created test scripts to verify the fix:

### Test 1: Core Prompt Content

**File:** [test_core_prompt.py](server/chat/router/test_core_prompt.py)

```bash
cd server && python test_core_prompt.py
```

**Expected Output:**
```
=== Testing Core Prompt ===

Prompt length: 2317 chars

Checks:
  ✓ TOOL USAGE (CRITICAL) section: True
  ✓ MUST use instruction: True
  ✓ NEVER answer from memory: True
  ✓ For specific text requests: True
  ✓ For finding sources: True

✓ All checks passed! Core prompt has proper tool usage instructions.
```

### Test 2: Prompt Service Integration

**File:** [test_prompt_service.py](server/chat/prompts/prompt_service.py)

```bash
cd server && python test_prompt_service.py
```

**Expected Output:**
```
=== Testing Prompt Service Integration ===

Core prompt ID: core-8fbc
Core prompt length: 1913 chars
Combined system prompt length: 3013 chars

Tool usage checks in system prompt:
  ✓ TOOL USAGE (CRITICAL): True
  ✓ MUST use tools: True
  ✓ NEVER answer from memory: True

✓ All checks passed! Prompt service correctly loads core prompt with tool instructions.
```

## Verification

Both tests pass successfully, confirming:
1. ✅ Core prompt contains explicit tool usage instructions
2. ✅ Prompt service correctly loads core-8fbc prompt
3. ✅ System prompt includes tool usage instructions
4. ✅ Conditional logic properly handles core-8fbc loading
5. ✅ Python 3.7 compatibility maintained

## Why This Works

The fix works because:

1. **Explicit Instructions Over Implicit**: Instead of "you have access to tools," we now say "you MUST use the provided Sefaria tools" and "NEVER answer from memory alone."

2. **Clear Section Hierarchy**: The "TOOL USAGE (CRITICAL)" header draws immediate attention to this requirement.

3. **Specific Tool Mapping**: Lists exactly which tool to use for which scenario, removing ambiguity.

4. **Negative Constraints**: Explicitly forbids answering from memory, forcing tool usage.

5. **Matches Working Prompt**: The updated fallback prompt now matches the structure and emphasis of the working `default_prompts.py` prompts that had no tool usage issues.

## Related Issues

This fix also resolved:
- JSON parsing issues (documented in [JSON_PARSING_FIX.md](JSON_PARSING_FIX.md))
- Core prompt loading (documented in [CORE_PROMPT_UPDATE.md](CORE_PROMPT_UPDATE.md))

## Braintrust Prompt Updates

When updating the `core-8fbc` prompt in Braintrust, ensure it includes:

```
TOOL USAGE (CRITICAL):
• You MUST use the provided Sefaria tools to search for and retrieve Jewish texts
• NEVER answer questions about Jewish texts, sources, or references from memory alone
• [List specific tool usage patterns]
```

The fallback prompt now serves as a reference for what the Braintrust prompt should contain.

## Monitoring

To verify tool usage in production:

```bash
# Check for tool usage in logs
grep "tool_use" logs/app.log

# Check for responses without tool calls (potential issues)
grep "respond.*without.*tool" logs/app.log

# Monitor core prompt loading
grep "core prompt" logs/app.log
```

## Future Improvements

1. **Structured Outputs**: Use Claude's native tool use features more explicitly
2. **Tool Use Validation**: Add validation that tools were actually called for text questions
3. **Metrics**: Track tool usage rate to detect regressions
4. **A/B Testing**: Test different prompt phrasings for tool usage compliance
