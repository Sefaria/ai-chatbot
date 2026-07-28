# Progress Trail (Streaming Thinking) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single transient "thinking" bubble with a persistent, scrolling trail of progress messages so users can see each tool call as it happens — providing transparency and reducing perceived latency.

**Architecture:** Frontend-only change. The backend already streams all progress events (`tool_start`, `tool_end`, `status`) via SSE. Currently the frontend overwrites each event into a single `currentProgress` state variable. We change this to accumulate events into a `progressTrail` array and render them as a list. When the final answer arrives, the trail collapses into a togglable "Show thinking" section.

**Tech Stack:** Svelte 5 (runes), i18n (svelte-i18n)

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/components/ProgressTrail.svelte` | Renders the growing list of progress entries |
| Modify | `src/components/LCChatbot.svelte` | Replace single-bubble progress with ProgressTrail; manage `progressTrail` state |
| Modify | `src/i18n/locales/en.json` | Add i18n keys for trail UI |

## Context: Current Progress Display

The current implementation (lines 1026-1051 in `LCChatbot.svelte`) shows a single `.thinking-content` div that updates in-place. Each `onProgress` callback overwrites `currentProgress`. The user only sees the latest event. The `toolHistory` array tracks tool calls but is not rendered.

The existing `toolHistory` array already accumulates tool_start/tool_end events (lines 476-489). We reuse this as our data source and add status events to it.

---

### Task 1: Extract ProgressTrail Component

**Files:**
- Create: `src/components/ProgressTrail.svelte`

- [ ] **Step 1: Create the ProgressTrail component**

```svelte
<script>
  import { _ } from '../i18n/index.js';

  let { entries = [], collapsed = false } = $props();

  let expanded = $state(false);
  let displayEntries = $derived(collapsed && !expanded ? entries.slice(-1) : entries);
</script>

{#if entries.length > 0}
  <div class="progress-trail" class:collapsed>
    {#if collapsed}
      <button class="trail-toggle" onclick={() => expanded = !expanded}>
        <svg class="trail-chevron" class:rotated={expanded} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
        <span>{$_('progress.showThinking', { values: { count: entries.length } })}</span>
      </button>
    {/if}

    {#if !collapsed || expanded}
      <div class="trail-entries">
        {#each displayEntries as entry (entry.id)}
          <div class="trail-entry" class:error={entry.status === 'error'}>
            {#if entry.type === 'tool'}
              <span class="trail-icon">
                {#if entry.status === 'running'}
                  <span class="trail-spinner"></span>
                {:else if entry.status === 'error'}
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>
                {:else}
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
                {/if}
              </span>
              <span class="trail-text">{entry.description}</span>
            {:else}
              <span class="trail-icon"><span class="trail-spinner"></span></span>
              <span class="trail-text">{entry.text}</span>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </div>
{/if}
```

- [ ] **Step 2: Verify component renders in isolation**

Run: `npm run dev`
Open browser, confirm no build errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/ProgressTrail.svelte
git commit -m "feat(ProgressTrail): add component for streaming thinking trail"
```

---

### Task 2: Wire ProgressTrail into LCChatbot

**Files:**
- Modify: `src/components/LCChatbot.svelte` (lines ~44-50, ~459-489, ~1026-1051)

- [ ] **Step 1: Add import and state**

At line 9 (imports section), add:
```javascript
import ProgressTrail from './ProgressTrail.svelte';
```

At line ~44 (state section), add a counter for unique entry IDs:
```javascript
let trailEntryId = $state(0);
```

- [ ] **Step 2: Modify onProgress to build trail entries**

Replace the current `onProgress` handler (lines ~465-489) to also push status events into `toolHistory`:

```javascript
onProgress: (progress) => {
  let displayText;
  if (progress?.type === 'status') {
    displayText = progress.text;
    toolHistory = [...toolHistory, {
      id: trailEntryId++,
      type: 'status',
      text: displayText?.replace(/…|\.\.\./, '') || '',
      status: 'running',
      startTime: Date.now()
    }];
  } else if (progress?.type === 'tool_start') {
    displayText = progress.description || `Running ${progress.toolName}`;
    toolHistory = [...toolHistory, {
      id: trailEntryId++,
      type: 'tool',
      toolName: progress.toolName,
      description: displayText?.replace(/…|\.\.\./, '') || '',
      status: 'running',
      startTime: Date.now()
    }];
  } else if (progress.type === 'tool_end') {
    toolHistory = toolHistory.map((t, i) =>
      i === toolHistory.length - 1 && t.type === 'tool'
        ? { ...t, status: progress.isError ? 'error' : 'complete', duration: Date.now() - t.startTime }
        : t
    );
  }
  displayText = displayText?.replace(/…|\.\.\./, '');
  currentProgress = {...progress, displayText};
},
```

Note: the existing `firstSourcePreview` logic from the POC branch (`waiting-source`) is not on main yet. When merging, preserve both: the SourceSuggestion capture in `tool_end` AND the status push here.

- [ ] **Step 3: Replace the thinking bubble with ProgressTrail**

Replace lines 1026-1051 (the `{#if isSending}` block):

```svelte
{#if isSending}
  <div class="message assistant">
    <ProgressTrail entries={toolHistory} collapsed={false} />
  </div>
{/if}
```

- [ ] **Step 4: Show collapsed trail above the final answer**

After the final answer is rendered, show a collapsed version. Find the `assistantBubble` render area (around line 1001) and add above it:

```svelte
{:else if item.role === 'assistant'}
  {#if item.toolHistory?.length > 0}
    <ProgressTrail entries={item.toolHistory} collapsed={true} />
  {/if}
  {@render assistantBubble(item.content, item.status === 'sent' && !!item.traceId, item)}
```

- [ ] **Step 5: Persist toolHistory on the assistant message**

In the success handler (~line 505-535), when building `assistantMessage`, include the trail:

```javascript
const assistantMessage = {
  messageId: response.messageId,
  // ... existing fields ...
  toolHistory: [...toolHistory],
};
```

- [ ] **Step 6: Reset trail counter on new message**

At the start of sendMessage (~line 459), reset:
```javascript
trailEntryId = 0;
```

- [ ] **Step 7: Verify manually**

Run: `npm run dev`
Open browser, send a prompt (e.g., "find me sources about Shabbat"). Verify:
1. Tool calls appear one by one as they happen
2. Status messages ("Thinking...") appear in the trail
3. When answer arrives, trail collapses to "Show thinking (N steps)"
4. Clicking the toggle expands the full trail

- [ ] **Step 8: Commit**

```bash
git add src/components/LCChatbot.svelte
git commit -m "feat: wire ProgressTrail into chat UI for streaming thinking display"
```

---

### Task 3: Add CSS for ProgressTrail

**Files:**
- Modify: `src/components/LCChatbot.svelte` (style section, end of file)

- [ ] **Step 1: Add :global() styles for the trail**

Add before the closing `</style>` tag:

```css
:global(.progress-trail) {
  font-size: 12px;
  color: #666;
  padding: 4px 12px;
}
:global(.progress-trail.collapsed) {
  padding: 2px 12px;
}
:global(.trail-toggle) {
  display: flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  cursor: pointer;
  color: #888;
  font-size: 11px;
  padding: 2px 0;
  font-family: inherit;
}
:global(.trail-toggle:hover) {
  color: #555;
}
:global(.trail-chevron) {
  transition: transform 0.2s ease;
}
:global(.trail-chevron.rotated) {
  transform: rotate(180deg);
}
:global(.trail-entries) {
  display: flex;
  flex-direction: column;
  gap: 3px;
}
:global(.trail-entry) {
  display: flex;
  align-items: center;
  gap: 6px;
  line-height: 1.4;
  color: #777;
}
:global(.trail-entry.error) {
  color: #c62828;
}
:global(.trail-icon) {
  flex-shrink: 0;
  width: 12px;
  height: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
:global(.trail-spinner) {
  width: 10px;
  height: 10px;
  border: 1.5px solid #ccc;
  border-top-color: #888;
  border-radius: 50%;
  animation: trail-spin 0.8s linear infinite;
}
@keyframes trail-spin {
  to { transform: rotate(360deg); }
}
:global(.trail-text) {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
```

- [ ] **Step 2: Verify styling**

Run: `npm run dev`
Check that trail entries are compact, readable, and don't overwhelm the chat area.

- [ ] **Step 3: Commit**

```bash
git add src/components/LCChatbot.svelte
git commit -m "style: add CSS for progress trail entries"
```

---

### Task 4: Add i18n Strings

**Files:**
- Modify: `src/i18n/locales/en.json`

- [ ] **Step 1: Add trail i18n keys**

Add after the `"status.toolError"` line:

```json
"progress.showThinking": "Show thinking ({count} steps)",
```

- [ ] **Step 2: Commit**

```bash
git add src/i18n/locales/en.json
git commit -m "feat(i18n): add progress trail strings"
```

---

### Task 5: Build Verification

- [ ] **Step 1: Run the build**

```bash
npm run build
```

Expected: Build succeeds with no errors.

- [ ] **Step 2: Final manual test**

Run: `npm run dev`
Test the following scenarios:
1. **Discovery prompt** ("find me sources about time") — trail shows multiple tool calls
2. **Translation prompt** ("translate this") — trail shows fewer steps
3. **Simple prompt** ("hello") — trail may show only "Thinking..." and guardrail
4. **Verify collapse** — after answer arrives, trail collapses; toggle works

- [ ] **Step 3: Commit any fixes**
