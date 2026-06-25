# TopicAppetizer Same-Page Navigation Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the TopicAppetizer link navigate in the current page instead of opening a new tab, matching how users expect in-site navigation to work on Sefaria.

**Architecture:** Remove `target="_blank"` and `window.open()` from TopicAppetizer. Use `window.location.href` for same-page navigation after firing the analytics event. The `<a>` tag keeps its `href` for accessibility/right-click, but programmatic navigation ensures the analytics event fires first.

**Tech Stack:** Svelte 5, Web Components (shadow DOM), Playwright MCP for testing

---

## Context

The chatbot is embedded inside Sefaria's React SPA (`ReaderApp.jsx:2469`). When a user clicks the topic appetizer link (e.g. "Shabbat →"), they expect to navigate to `sefaria.org/topics/shabbat` in the same tab — not open a duplicate tab. Production testing confirmed all links currently open new tabs, which is wrong for in-site navigation.

The SourceSuggestion component (`src/components/SourceSuggestion.svelte:42`) also uses `target="_blank"`, but the user has not flagged it — leave it unchanged.

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/components/TopicAppetizer.svelte` | Modify | Remove `target="_blank"`, change `handleClick` to use `window.location.href` |
| `src/components/LCChatbot.svelte:827-836` | No change | `handleAppetizerClick` dispatches analytics CustomEvent — keep as-is |

---

### Task 1: Fix TopicAppetizer navigation to same-page

**Files:**
- Modify: `src/components/TopicAppetizer.svelte:12-16` (handleClick function)
- Modify: `src/components/TopicAppetizer.svelte:40-48` (a tag attributes)

- [ ] **Step 1: Change `handleClick` to navigate same-page**

In `src/components/TopicAppetizer.svelte`, replace the `handleClick` function (lines 12-16):

```javascript
// BEFORE (current):
function handleClick(e) {
    e.preventDefault();
    if (onClickTopic) onClickTopic(data.topicSlug);
    window.open(data.topicUrl, '_blank', 'noopener,noreferrer');
}

// AFTER:
function handleClick(e) {
    e.preventDefault();
    if (onClickTopic) onClickTopic(data.topicSlug);
    window.location.href = data.topicUrl;
}
```

- [ ] **Step 2: Remove `target="_blank"` from the `<a>` tag**

In the same file, change the `<a>` tag (lines 40-46):

```svelte
<!-- BEFORE: -->
<a
  class="appetizer-link"
  href={data.topicUrl}
  target="_blank"
  rel="noopener noreferrer"
  onclick={handleClick}
>

<!-- AFTER: -->
<a
  class="appetizer-link"
  href={data.topicUrl}
  onclick={handleClick}
>
```

- [ ] **Step 3: Verify frontend builds**

Run: `cd /Users/yotamfromm/dev/sefaria/ai-chatbot && npm run build 2>&1 | tail -5`

Expected: `✓ built in` with no errors

- [ ] **Step 4: Commit**

```bash
cd /Users/yotamfromm/dev/sefaria/ai-chatbot
git add src/components/TopicAppetizer.svelte
git commit -m "fix(appetizer): navigate topic link in same page instead of new tab"
```

---

### Task 2: Playwright verification — link navigates in same page

**Files:**
- No files created/modified — this is a manual Playwright test via MCP tools

**Pre-requisite:** Local dev servers running — Vite on port 5173 (proxies `/api` to port 8001), Django backend on port 8001.

- [ ] **Step 1: Navigate to the chatbot dev page**

Using Playwright MCP, navigate to `http://localhost:5173`.

- [ ] **Step 2: Open the chatbot and send a message**

```javascript
// Open chatbot
document.querySelector('lc-chatbot').shadowRoot.querySelector('.chat-toggle').click();
```

Wait 1 second, then type "tell me about Shabbat" into the textarea and press Enter.

- [ ] **Step 3: Wait for the appetizer and verify it appears**

Poll shadow DOM every 500ms for `.topic-appetizer` (max 10 seconds). Record the time from send to appearance.

Expected: Appetizer appears within 6 seconds with topic title and a link to `sefaria.org/topics/`.

- [ ] **Step 4: Verify the link does NOT have `target="_blank"`**

```javascript
const shadow = document.querySelector('lc-chatbot').shadowRoot;
const link = shadow.querySelector('.appetizer-link');
// Verify:
// link.target should be "" (empty, not "_blank")
// link.href should contain "sefaria.org/topics/"
```

Expected: `target` is empty string, `href` contains `sefaria.org/topics/`.

- [ ] **Step 5: Click the link and verify same-page navigation**

Click the `.appetizer-link`. After 2 seconds, check:
1. Browser tabs list — should still have the same number of tabs (NO new tab opened)
2. Current page URL — should now be the topic URL (e.g. `https://www.sefaria.org/topics/shabbat`)

Expected: Same tab count, URL changed to the topic page.

- [ ] **Step 6: Take a screenshot of the navigated topic page**

Screenshot to confirm the user lands on the Sefaria topic page in the same tab.

---

### Task 3: Playwright verification — appetizer timing still under 6 seconds

**Files:**
- No files created/modified — Playwright test via MCP tools

- [ ] **Step 1: Navigate back to the chatbot**

Navigate to `http://localhost:5173` again.

- [ ] **Step 2: Inject timing observer and send a message**

```javascript
const shadow = document.querySelector('lc-chatbot').shadowRoot;
window.__testStart = null;
window.__appetizerTime = null;

const observer = new MutationObserver(() => {
  const appetizer = shadow.querySelector('.topic-appetizer');
  if (appetizer && !window.__appetizerTime) {
    window.__appetizerTime = Date.now();
    window.__appetizerElapsed = window.__appetizerTime - window.__testStart;
  }
});
observer.observe(shadow, { childList: true, subtree: true });
```

Type "what does the Torah say about prayer?" and record `Date.now()` as `window.__testStart` right before pressing Enter.

- [ ] **Step 3: Poll for appetizer timing**

Every 500ms for up to 10 seconds, check `window.__appetizerElapsed`.

Expected: `__appetizerElapsed` is a number less than 6000 (under 6 seconds).

- [ ] **Step 4: Report timing**

Log the exact elapsed time. Verify it meets the 6-second SLA.

---

## Self-Review Checklist

1. **Spec coverage:** Same-page navigation (Task 1), link verification (Task 2), timing SLA (Task 3) — all covered.
2. **Placeholder scan:** No TBDs, TODOs, or vague steps. All code shown.
3. **Type consistency:** `data.topicUrl` and `data.topicSlug` used consistently across all tasks, matching the `appetizerData` shape from the backend (`{topicSlug, topicTitle, topicUrl}`).
