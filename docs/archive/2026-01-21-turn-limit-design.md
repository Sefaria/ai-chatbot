# Turn Limit Feature Design

## Overview

Convert the chatbot from unlimited multi-turn conversations to a configurable turn limit system. Default to single-turn (one question, one answer) for initial evaluation, with the ability to increase the limit later.

## Product Decisions

### Core Behavior

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Turn model | Configurable limit (1, 5, 100, etc.) | Flexibility for evaluation phase and future |
| Data storage | Keep full session/message tracking | Retain analytics, debugging, future history feature |
| Turn counting | Counts on submit, regardless of success/failure | User message is persisted before agent runs; simplifies logic |
| New session on reset | Yes, generate fresh `sess_*` ID | Clean separation, accurate analytics per conversation |

### User Interface

| Decision | Choice |
|----------|--------|
| At limit reached | Input box replaced with button |
| Button text | "New Question" (limit=1) / "New Conversation" (limit>1) |
| Inline text | "Start a new question to continue" (or "conversation" if limit>1) |
| On button click | Clear messages with quick fade animation, create new session |
| Turn indicator | None (for now) |
| History on page load | Keep current behavior - show previous messages if session exists |

### Configuration

| Decision | Choice |
|----------|--------|
| Who controls limit | Backend only (frontend cannot override) |
| Default limit | 1 (single turn for evaluation) |
| Frontend attribute | None - frontend discovers limit from API response |

## Technical Design

### Backend Changes

#### 1. Django Settings

Add to `settings.py`:
```python
# Turn limit for chat sessions (1 = single Q&A, higher = multi-turn)
MAX_TURNS = int(os.environ.get("MAX_TURNS", 1))
```

#### 2. Chat API Response (`POST /api/chat` and `/api/chat/stream`)

Add `session` object to response:
```json
{
  "messageId": "msg_...",
  "sessionId": "sess_...",
  "timestamp": "2026-01-21T...",
  "markdown": "Response text...",
  "routing": {
    "flow": "halachic",
    "decisionId": "dec_...",
    "confidence": 0.95,
    "wasRefused": false
  },
  "session": {
    "turnCount": 1,
    "maxTurns": 1,
    "limitReached": true
  }
}
```

#### 3. History API Response (`GET /api/history`)

Extend existing `session` object:
```json
{
  "messages": [...],
  "hasMore": false,
  "session": {
    "currentFlow": "halachic",
    "turnCount": 1,
    "totalTokens": 500,
    "maxTurns": 1,
    "limitReached": true
  }
}
```

#### 4. Backend Validation

Before processing a chat request, check if turn limit exceeded:
```python
if session.turn_count >= settings.MAX_TURNS:
    return Response(
        {
            "error": "turn_limit_reached",
            "message": "Turn limit reached. Start a new conversation.",
            "maxTurns": settings.MAX_TURNS
        },
        status=status.HTTP_400_BAD_REQUEST
    )
```

This is defensive - frontend should prevent this, but protects against stale state.

#### 5. Model Changes

None required. `ChatSession.turn_count` already exists and is incremented after each turn.

### Frontend Changes

#### 1. State Management

Add to component state:
```javascript
let maxTurns = null;      // From API response
let limitReached = false; // From API response
```

Update after each API response:
```javascript
if (response.session) {
  maxTurns = response.session.maxTurns;
  limitReached = response.session.limitReached;
}
```

#### 2. UI States

**Normal state** (limit not reached):
- Show input box and send button as usual

**Limit reached state**:
- Hide/remove input box
- Show "New Question" or "New Conversation" button
- Show inline text: "Start a new question to continue"

#### 3. Dynamic Text

```javascript
const buttonText = maxTurns === 1 ? "New Question" : "New Conversation";
const inlineText = maxTurns === 1
  ? "Start a new question to continue"
  : "Start a new conversation to continue";
```

#### 4. New Session Flow

When user clicks the button:
1. Generate new session ID: `generateSessionId()`
2. Clear messages array with quick fade animation
3. Reset state: `limitReached = false`, `maxTurns = null`
4. Save new session to localStorage
5. Show empty state with input box

#### 5. History Loading

On component mount / panel open:
1. Call `/api/history` as usual
2. Read `session.limitReached` and `session.maxTurns` from response
3. If `limitReached === true`, show the "New Question" button state

### Files to Modify

**Backend:**
- `server/chatbot_server/settings.py` - Add `MAX_TURNS`
- `server/chat/views.py` - Add session info to responses, add validation

**Frontend:**
- `src/components/LCChatbot.svelte` - State, UI changes, new session flow
- `src/lib/api.js` - Handle new response fields (if needed)
- `src/lib/session.js` - No changes needed (generateSessionId already exists)

## Testing Plan

1. **Backend unit tests:**
   - Turn limit validation returns 400 when exceeded
   - Response includes correct session info
   - History endpoint includes turn limit info

2. **Frontend manual testing:**
   - Single turn: ask question, see response, input replaced with button
   - Click "New Question", messages clear, can ask again
   - Refresh page with limit-reached session, shows button state
   - Multi-turn (change MAX_TURNS=5): can ask multiple questions, button appears after 5th

3. **Edge cases:**
   - Error responses still count as turns
   - Streaming errors still count as turns
   - Page refresh mid-stream shows correct state

## Future Considerations

- Per-embedding configuration (different sites get different limits)
- "Your recent questions" history view
- Turn limit indicator ("2 of 5 questions remaining")

---

# Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add configurable turn limits to the chatbot, defaulting to single-turn (Q&A) mode.

**Architecture:** Backend controls turn limit via Django settings, returns session info in every response. Frontend reads this info and shows "New Question" button when limit reached.

**Tech Stack:** Django 4.2, Svelte 5, REST API

---

## Task 1: Add MAX_TURNS Setting

**Files:**
- Modify: `server/chatbot_server/settings.py`

**Step 1: Add the setting**

Add after line 150 (after `ENVIRONMENT = ...`):

```python
# Chat turn limit (1 = single Q&A, higher = multi-turn conversation)
MAX_TURNS = int(os.environ.get("MAX_TURNS", 1))
```

**Step 2: Verify setting loads**

Run:
```bash
cd server && python -c "from django.conf import settings; print(f'MAX_TURNS={settings.MAX_TURNS}')"
```
Expected: `MAX_TURNS=1`

**Step 3: Commit**

```bash
git add server/chatbot_server/settings.py
git commit -m "$(cat <<'EOF'
feat: add MAX_TURNS setting for turn limit configuration

Default to 1 (single Q&A mode) for initial evaluation.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Add Session Info to Chat Response

**Files:**
- Modify: `server/chat/views.py`

**Step 1: Import settings at top of file**

After line 27 (`from rest_framework.response import Response`), add:

```python
from django.conf import settings
```

**Step 2: Add helper function to build session info**

Add after `extract_page_context` function (after line 77):

```python
def build_session_info(session) -> dict:
    """Build session info dict for API response."""
    turn_count = session.turn_count or 0
    max_turns = settings.MAX_TURNS
    return {
        "turnCount": turn_count,
        "maxTurns": max_turns,
        "limitReached": turn_count >= max_turns,
    }
```

**Step 3: Add turn limit validation in chat() view**

In the `chat` function, after session is loaded (after line 200), add validation:

```python
    # Check turn limit
    if (session.turn_count or 0) >= settings.MAX_TURNS:
        return Response(
            {
                "error": "turn_limit_reached",
                "message": "Turn limit reached. Start a new conversation.",
                "maxTurns": settings.MAX_TURNS,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
```

**Step 4: Add session info to chat() response**

In the `chat` function, modify the response_data dict (around line 351) to include session info:

Change:
```python
        response_data = {
            "messageId": response_message.message_id,
            "sessionId": data["sessionId"],
            "timestamp": response_message.server_timestamp.isoformat(),
            "markdown": agent_response.content,
            "routing": {
                "flow": route_result.flow.value,
                "decisionId": route_result.decision_id,
                "confidence": route_result.confidence,
                "wasRefused": agent_response.was_refused,
            },
        }
```

To:
```python
        # Reload session to get updated turn_count
        session.refresh_from_db()

        response_data = {
            "messageId": response_message.message_id,
            "sessionId": data["sessionId"],
            "timestamp": response_message.server_timestamp.isoformat(),
            "markdown": agent_response.content,
            "routing": {
                "flow": route_result.flow.value,
                "decisionId": route_result.decision_id,
                "confidence": route_result.confidence,
                "wasRefused": agent_response.was_refused,
            },
            "session": build_session_info(session),
        }
```

**Step 5: Commit**

```bash
git add server/chat/views.py
git commit -m "$(cat <<'EOF'
feat: add turn limit validation and session info to chat endpoint

- Check turn limit before processing, return 400 if exceeded
- Include turnCount, maxTurns, limitReached in response

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add Session Info to Streaming Chat Response

**Files:**
- Modify: `server/chat/views.py`

**Step 1: Add turn limit validation in chat_stream()**

In `chat_stream` function, after session is loaded (after line 434), add:

```python
    # Check turn limit
    if (session.turn_count or 0) >= settings.MAX_TURNS:
        return Response(
            {
                "error": "turn_limit_reached",
                "message": "Turn limit reached. Start a new conversation.",
                "maxTurns": settings.MAX_TURNS,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
```

**Step 2: Add session info to streaming final message**

In the `generate_sse()` function inside `chat_stream`, modify the final_data dict (around line 634) to include session info:

Change:
```python
        final_data = {
            "messageId": response_message.message_id,
            "sessionId": data["sessionId"],
            "timestamp": response_message.server_timestamp.isoformat(),
            "markdown": agent_response.content,
            "routing": {
                "flow": route_result.flow.value,
                "decisionId": route_result.decision_id,
                "wasRefused": agent_response.was_refused,
            },
            "stats": {
                "llmCalls": agent_response.llm_calls,
                "toolCalls": len(agent_response.tool_calls),
                "inputTokens": agent_response.input_tokens,
                "outputTokens": agent_response.output_tokens,
                "latencyMs": latency_ms,
            },
        }
```

To:
```python
        # Reload session to get updated turn_count
        session.refresh_from_db()

        final_data = {
            "messageId": response_message.message_id,
            "sessionId": data["sessionId"],
            "timestamp": response_message.server_timestamp.isoformat(),
            "markdown": agent_response.content,
            "routing": {
                "flow": route_result.flow.value,
                "decisionId": route_result.decision_id,
                "wasRefused": agent_response.was_refused,
            },
            "stats": {
                "llmCalls": agent_response.llm_calls,
                "toolCalls": len(agent_response.tool_calls),
                "inputTokens": agent_response.input_tokens,
                "outputTokens": agent_response.output_tokens,
                "latencyMs": latency_ms,
            },
            "session": build_session_info(session),
        }
```

**Step 3: Commit**

```bash
git add server/chat/views.py
git commit -m "$(cat <<'EOF'
feat: add turn limit validation and session info to streaming endpoint

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Add Session Info to History Response

**Files:**
- Modify: `server/chat/views.py`

**Step 1: Update history() to include turn limit info**

In the `history` function, modify the session_info dict (around line 706):

Change:
```python
        session_info = {
            "currentFlow": session.current_flow,
            "turnCount": session.turn_count,
            "totalTokens": session.total_input_tokens + session.total_output_tokens,
        }
```

To:
```python
        session_info = {
            "currentFlow": session.current_flow,
            "turnCount": session.turn_count or 0,
            "totalTokens": (session.total_input_tokens or 0) + (session.total_output_tokens or 0),
            "maxTurns": settings.MAX_TURNS,
            "limitReached": (session.turn_count or 0) >= settings.MAX_TURNS,
        }
```

**Step 2: Commit**

```bash
git add server/chat/views.py
git commit -m "$(cat <<'EOF'
feat: add turn limit info to history endpoint

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Update Frontend API to Handle Session Info

**Files:**
- Modify: `src/lib/api.js`

**Step 1: Update ChatResponse typedef**

Update the ChatResponse typedef (around line 26) to include session:

```javascript
/**
 * @typedef {Object} SessionInfo
 * @property {number} turnCount
 * @property {number} maxTurns
 * @property {boolean} limitReached
 */

/**
 * @typedef {Object} ChatResponse
 * @property {string} messageId
 * @property {string} sessionId
 * @property {string} timestamp
 * @property {string} markdown
 * @property {SessionInfo} [session]
 */
```

**Step 2: Update sendMessageStream to include session in response**

In `sendMessageStream`, update the finalMessage construction (around line 178):

Change:
```javascript
              finalMessage = {
                messageId: data.messageId,
                sessionId: data.sessionId,
                timestamp: data.timestamp,
                markdown: data.markdown,
                toolCalls: data.toolCalls,
                stats: data.stats
              };
```

To:
```javascript
              finalMessage = {
                messageId: data.messageId,
                sessionId: data.sessionId,
                timestamp: data.timestamp,
                markdown: data.markdown,
                toolCalls: data.toolCalls,
                stats: data.stats,
                session: data.session
              };
```

**Step 3: Update loadHistory to return session info**

In `loadHistory`, update the return statement (around line 251):

Change:
```javascript
  return {
    messages: data.messages || [],
    hasMore: data.hasMore ?? false
  };
```

To:
```javascript
  return {
    messages: data.messages || [],
    hasMore: data.hasMore ?? false,
    session: data.session || null
  };
```

**Step 4: Commit**

```bash
git add src/lib/api.js
git commit -m "$(cat <<'EOF'
feat: update API client to handle session info in responses

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Add Turn Limit State to Frontend Component

**Files:**
- Modify: `src/components/LCChatbot.svelte`

**Step 1: Add new state variables**

After line 33 (`let toolHistory = $state([]);`), add:

```javascript
  // Turn limit state
  let maxTurns = $state(null);
  let limitReached = $state(false);
  let isClearing = $state(false);
```

**Step 2: Add computed property for dynamic text**

After the new state variables, add:

```javascript
  // Dynamic text based on turn limit
  let newSessionButtonText = $derived(maxTurns === 1 ? 'New Question' : 'New Conversation');
  let newSessionHintText = $derived(
    maxTurns === 1
      ? 'Start a new question to continue'
      : 'Start a new conversation to continue'
  );
```

**Step 3: Commit**

```bash
git add src/components/LCChatbot.svelte
git commit -m "$(cat <<'EOF'
feat: add turn limit state variables to chatbot component

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Update Frontend to Read Session Info from Responses

**Files:**
- Modify: `src/components/LCChatbot.svelte`

**Step 1: Update handleSend to read session info**

In `handleSend`, after the assistant message is added (around line 240), add session info handling:

After:
```javascript
      messages = [...messages, assistantMessage];
      saveMessagesToStorage();
      scrollToBottom();
```

Add:
```javascript
      // Update turn limit state from response
      if (response.session) {
        maxTurns = response.session.maxTurns;
        limitReached = response.session.limitReached;
      }
```

**Step 2: Update loadInitialHistory to read session info**

In `loadInitialHistory`, update the try block (around line 121):

Change:
```javascript
      const result = await loadHistory(apiBaseUrl, userId, sessionId, null, 20);
      messages = result.messages;
      hasMoreHistory = result.hasMore;
```

To:
```javascript
      const result = await loadHistory(apiBaseUrl, userId, sessionId, null, 20);
      messages = result.messages;
      hasMoreHistory = result.hasMore;

      // Update turn limit state from history response
      if (result.session) {
        maxTurns = result.session.maxTurns;
        limitReached = result.session.limitReached;
      }
```

**Step 3: Commit**

```bash
git add src/components/LCChatbot.svelte
git commit -m "$(cat <<'EOF'
feat: read session info from API responses

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Add Start New Session Function

**Files:**
- Modify: `src/components/LCChatbot.svelte`
- Modify: `src/lib/session.js`

**Step 1: Export generateSessionId from session.js**

In `src/lib/session.js`, change line 14 from:

```javascript
function generateSessionId() {
```

To:
```javascript
export function generateSessionId() {
```

**Step 2: Import generateSessionId in component**

In `src/components/LCChatbot.svelte`, update line 5:

Change:
```javascript
  import { getOrCreateSession, updateSessionActivity, generateMessageId } from '../lib/session.js';
```

To:
```javascript
  import { getOrCreateSession, updateSessionActivity, generateMessageId, generateSessionId } from '../lib/session.js';
```

**Step 3: Add startNewSession function**

After `retryMessage` function (around line 297), add:

```javascript
  function startNewSession() {
    // Trigger fade animation
    isClearing = true;

    setTimeout(() => {
      // Generate new session
      const newSessionId = generateSessionId();
      sessionId = newSessionId;

      // Clear messages
      messages = [];

      // Reset state
      limitReached = false;
      maxTurns = null;
      hasMoreHistory = false;

      // Save new session
      setStorage(STORAGE_KEYS.SESSION, {
        sessionId: newSessionId,
        lastActivity: new Date().toISOString()
      });

      // Clear old messages from storage
      setStorage(STORAGE_KEYS.MESSAGES + ':' + newSessionId, []);

      isClearing = false;

      // Focus input
      setTimeout(() => inputRef?.focus(), 100);

      dispatchEvent('session_started', { sessionId: newSessionId });
    }, 150); // Quick fade duration
  }
```

**Step 4: Commit**

```bash
git add src/components/LCChatbot.svelte src/lib/session.js
git commit -m "$(cat <<'EOF'
feat: add startNewSession function for turn limit reset

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Add Limit Reached UI

**Files:**
- Modify: `src/components/LCChatbot.svelte`

**Step 1: Add fade animation class to message list**

Update the message list div (around line 436):

Change:
```javascript
      <div
      class="lc-chatbot-messages"
```

To:
```javascript
      <div
      class="lc-chatbot-messages"
      class:clearing={isClearing}
```

**Step 2: Replace footer with conditional UI**

Replace the entire footer section (lines 551-571):

Change:
```javascript
      <!-- Input Footer -->
      <footer class="lc-chatbot-input">
        <textarea
          bind:this={inputRef}
          bind:value={inputText}
          onkeydown={handleKeydown}
          placeholder="Type a message..."
          rows="1"
          disabled={isSending}
        ></textarea>
        <button
          class="send-btn"
          onclick={handleSend}
          disabled={!inputText.trim() || isSending}
          aria-label="Send message"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="22" y1="2" x2="11" y2="13"></line>
            <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
          </svg>
        </button>
      </footer>
```

To:
```javascript
      <!-- Input Footer -->
      <footer class="lc-chatbot-input">
        {#if limitReached}
          <div class="limit-reached">
            <p class="limit-hint">{newSessionHintText}</p>
            <button class="new-session-btn" onclick={startNewSession}>
              {newSessionButtonText}
            </button>
          </div>
        {:else}
          <textarea
            bind:this={inputRef}
            bind:value={inputText}
            onkeydown={handleKeydown}
            placeholder="Type a message..."
            rows="1"
            disabled={isSending}
          ></textarea>
          <button
            class="send-btn"
            onclick={handleSend}
            disabled={!inputText.trim() || isSending}
            aria-label="Send message"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        {/if}
      </footer>
```

**Step 3: Commit**

```bash
git add src/components/LCChatbot.svelte
git commit -m "$(cat <<'EOF'
feat: add limit reached UI with new session button

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Add CSS for Limit Reached State

**Files:**
- Modify: `src/components/LCChatbot.svelte`

**Step 1: Add CSS for clearing animation and limit reached UI**

Add before the closing `</style>` tag:

```css
  /* Clearing Animation */
  .lc-chatbot-messages.clearing {
    opacity: 0;
    transition: opacity 0.15s ease;
  }

  /* Limit Reached State */
  .limit-reached {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    width: 100%;
    padding: 8px 0;
  }

  .limit-hint {
    font-size: 13px;
    color: var(--lc-text-muted);
    margin: 0;
  }

  .new-session-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 10px 20px;
    background: var(--lc-primary);
    color: white;
    border: none;
    border-radius: var(--lc-radius-sm);
    font-family: var(--lc-font);
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .new-session-btn:hover {
    background: var(--lc-primary-hover);
  }

  .new-session-btn:active {
    transform: scale(0.98);
  }
```

**Step 2: Commit**

```bash
git add src/components/LCChatbot.svelte
git commit -m "$(cat <<'EOF'
feat: add CSS for limit reached state and clearing animation

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Test Full Flow

**Step 1: Start backend**

```bash
cd server && python manage.py runserver 0.0.0.0:8001
```

**Step 2: Start frontend**

```bash
npm run dev
```

**Step 3: Manual test checklist**

- [ ] Ask a question, verify response includes session info
- [ ] After response, input should be replaced with "New Question" button
- [ ] Text above button says "Start a new question to continue"
- [ ] Click "New Question", messages fade and clear
- [ ] Can ask another question (new session)
- [ ] Refresh page, should see previous Q&A with button shown
- [ ] Check console for errors

**Step 4: Test with MAX_TURNS=5**

```bash
MAX_TURNS=5 python manage.py runserver 0.0.0.0:8001
```

- [ ] Can ask 5 questions
- [ ] After 5th, button shows "New Conversation"
- [ ] Text says "Start a new conversation to continue"

---

## Task 12: Create PR

**Step 1: Verify all changes**

```bash
git status
git log --oneline -10
```

**Step 2: Push branch**

```bash
git push -u origin dev
```

**Step 3: Create PR**

```bash
gh pr create --base main --head dev --title "feat: add configurable turn limits with single-turn default" --body "$(cat <<'EOF'
## Summary

Adds configurable turn limits to the chatbot, defaulting to single-turn (one question, one answer) mode for initial evaluation.

## Product Decisions

| Decision | Choice |
|----------|--------|
| Turn model | Configurable limit via `MAX_TURNS` setting (default: 1) |
| At limit | Input replaced with "New Question" / "New Conversation" button |
| On reset | Creates new session, clears messages with quick fade |
| Turn counting | Counts on submit (even if response fails) |
| Backend validation | Returns 400 if turn limit exceeded |

## Technical Changes

### Backend
- Added `MAX_TURNS` setting to `settings.py` (default: 1)
- Chat endpoints return `session: { turnCount, maxTurns, limitReached }` in every response
- History endpoint includes turn limit info
- Validation rejects requests if turn limit exceeded

### Frontend
- Reads session info from API responses
- Shows "New Question" button when `limitReached: true`
- `startNewSession()` creates new session and clears UI
- Quick fade animation on clear

## Testing

- Manual testing with MAX_TURNS=1 and MAX_TURNS=5
- Verified page refresh preserves limit state
- Verified error responses count as turns

## Files Changed

- `server/chatbot_server/settings.py` - Added MAX_TURNS setting
- `server/chat/views.py` - Turn limit validation and session info in responses
- `src/lib/api.js` - Handle session info in responses
- `src/lib/session.js` - Export generateSessionId
- `src/components/LCChatbot.svelte` - Turn limit state and UI

---

See `docs/plans/2026-01-21-turn-limit-design.md` for full design document.
EOF
)"
```
