# Appetizer Feature Retest Plan

## Context
The import path bug in `views.py` was fixed (`.router` and `.appetizer` instead of `..router` and `..appetizer`). The appetizer thread was crashing immediately on every request due to `ModuleNotFoundError`. Now we need to verify the entire appetizer pipeline works.

## Architecture Recap
When a user sends a message:
1. **Main agent** (Sonnet) starts on one thread — full pipeline with tools, takes 30-60s
2. **Appetizer** (Haiku) starts on a SEPARATE parallel thread — must return in <5s:
   - Haiku extracts the key Jewish concept from the prompt (~1-2s)
   - `search_topics()` calls Sefaria API to find matching topic slug (~<1s)
   - Result pushed to SSE queue as `type: "appetizer"` event
   - Frontend renders yellow `TopicAppetizer` box with topic link

## Test Sequence

### Phase 1: Verify appetizer pipeline works
1. Restart conversation (clear old failed messages)
2. Send: "find me sources about Shabbat"
3. Poll shadow DOM every 2s for `.topic-appetizer` element
4. Record time-to-appetizer (target: <5s)
5. If appetizer appears:
   - Verify `.appetizer-link` has href containing `sefaria.org/topics/`
   - Verify `.appetizer-header-text` shows the "while waiting" copy
   - Screenshot the appetizer

### Phase 2: Test collapsibility (TA-3)
1. Click `.appetizer-header` to collapse
2. Verify `.appetizer-body` disappears
3. Click again to expand
4. Verify `.appetizer-body` reappears

### Phase 3: Test persistence after answer (TA-5)
1. Wait for full answer to arrive (textarea becomes enabled)
2. Verify `.topic-appetizer` still exists in the DOM
3. Screenshot final state

### Phase 4: Test translation suppression (TA-4)
1. Restart conversation
2. Send: "translate Genesis 1:1"
3. Wait 10s
4. Verify NO `.topic-appetizer` appears

### Phase 5: Check backend logs
1. grep for "appetizer" in backend.log
2. Confirm no more ModuleNotFoundError
3. Look for Haiku call timing

### Phase 6: Update feature-tests.json with results
