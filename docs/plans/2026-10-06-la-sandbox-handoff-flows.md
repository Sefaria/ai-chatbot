# LA sandbox (PR #236): user flows for the engineering handoff

A running list of the flows to attach to PR #236 when it goes to engineering. Each flow
says where it starts, what the person does, and what should happen, on desktop and phone,
in English and Hebrew.

Status: **anonymous usage drafted**. Other parts of the PR still to cover (see the end).

- Live preview: https://la-sandbox.cauldron.sefaria.org/texts?chatbot_version=236
  (private window = logged out; Sandbox controls > Entry points: Circle only for a
  production-like view)
- Figma: [Library Assistant Wireframes, ↳Anonymous Usage](https://www.figma.com/design/Y31hDgxSjr0l1fcm0nNJSD/Library-Assistant-Wireframes?node-id=7552-291)
  (earlier banner explorations; the shipped design replaces the input instead)

## 1. Anonymous usage

Logged-out visitors can use the assistant without an account for a limited number of
answers (`CHATBOT_ANON_FREE_RESPONSES`, default **2**), then are asked to log in.

### 1.1 Logged-out visitor asks their free questions

- **Start:** logged out, on a library page (desktop or phone). Sefaria renders the widget
  with no `user-id`.
- **Steps:** open the assistant, ask a question, get an answer; ask a second one.
- **Expected:**
  - Both questions are answered normally.
  - The history icon in the header is visible but locked, with the tooltip "Log in to see
    chat history".
  - No warning before the last free answer (a one-left warning was tried and removed).

### 1.2 Free answers run out

- **Start:** the visitor has just received their last free answer.
- **Expected:**
  - The text box and send button are replaced by a centered line, "To keep asking, log in
    or register for free.", and a full-width **Log in or register** button below it.
  - Nothing covers the conversation, so the last answer can be read to the end.
  - Screen readers hear the prompt (`role="status"`). Focus is not moved; the button is
    reachable with Tab.
  - The prompt slides and fades in over 200ms; no motion with reduced motion.
  - Hebrew: "כדי להמשיך לשאול, התחברו או הירשמו לחשבון ספריא חינמי." and the button
    "להתחברות או הרשמה".
- **Visual spec:** the input area keeps its padding and top line. Line: Roboto (Heebo in
  Hebrew), the body size (14px desktop, 16px phone), 20px line height, Text/Secondary.
  Button: full width, the send button's height (40px desktop, 44px phone), Sefaria blue
  (`--lc-entry-bg`, darker on hover), white semibold text, 8px corners, a focus ring for
  keyboard users.

### 1.3 Visitor tries to ask past the limit (server says no)

- **Start:** the browser doesn't know the limit is reached (for example, a second tab), so
  the text box is still open.
- **Steps:** send a question.
- **Expected:** the server answers `403 login_required`. The question is removed from the
  chat, put back in the text box as a draft, and the login prompt replaces the input as in 1.2. Nothing is
  shown as an error.

### 1.4 Visitor closes and comes back later (still logged out)

- **Start:** limit reached, then the page is reloaded or revisited in the same browser.
- **Expected:** the login prompt is still there in place of the input (the "login required" state
  is remembered in the browser). The earlier conversation is still shown.

### 1.5 Visitor logs in from the prompt and comes back

- **Steps:** click **Log in or register**.
- **Expected:**
  - The browser goes to Sefaria's login page with `?next=<the page they were on>`. The
    assistant does not appear on the login page.
  - After logging in (or registering), they land back on the same page. The assistant
    opens on its own, showing the earlier conversation, with the text box enabled.
  - This reopening only happens within 30 minutes of clicking the link.
- **Known limit:** the earlier conversation is shown, but the assistant can't use it as
  context. A signed-in user can't continue an anonymous conversation on the server, so the
  next question starts a new session. Engineering to confirm this is acceptable.

### 1.6 Signed-in user logs out

- **Expected:** a new, empty session starts as a logged-out visitor. Personal memory is
  cleared from the browser so the next person on it doesn't inherit it.

### 1.7 Phone

- Same flows 1.1–1.5 inside the full-screen sheet.
- The prompt replaces the text box. Check the button stays clear of the iOS
  home bar and doesn't scroll sideways at 320px.
- After logging in, the sheet reopens on the conversation (the only time a sheet opens by
  itself).

### 1.8 Hebrew

- All of the above with `interface-lang="he"`: the prompt in Hebrew, right-aligned.

### 1.9 Asking from the search no-results button

- **Start:** logged out, on Sefaria's search page with no results. Clicking **Try Library
  Assistant** opens the assistant and asks the rewritten search in a new chat
  (`chatbot:open` with `source: 'search_no_results'`; the widget sends
  `entrySource: "search_no_results"` with that one question).
- **Rule (any limit N):** a question from this button counts like any other, except it
  never uses up the last free answer:
  - If it isn't the last free answer, it counts as usual.
  - If it would be the last free answer, it is answered but not counted, and no login
    prompt appears. The visitor's next question then counts as usual.
  - If the visitor is already at the limit, nothing is sent: the assistant opens on the
    login prompt (1.2). The server would refuse it anyway (`login_required`). The button's
    question is not kept: after they log in and come back (1.5), the text box is empty and
    they can click the button again or type their own question (decided 2026-10-07; not
    auto-sent, not prefilled).
- **Example (N = 2):** the button as their first question leaves 1 free answer. One
  question of their own, then the button: answered, still 1 left. Their next question uses
  it and the login prompt appears.
- **Once per visitor:** the source comes from the browser, so each anonymous visitor gets
  this exemption once, across all their chats. A second button question in the same spot
  counts as usual and uses the last answer. A failed answer doesn't use up the exemption.
- Signed-in users are unaffected.

### Edge cases and open questions

- **Clearing site data gives a fresh quota.** Free answers are counted per anonymous id,
  which lives in the browser's storage, so clearing it or using another browser resets the
  count. Fine for a soft limit; engineering and product to confirm.
- **Failed answers don't count.** Only successful answers use up the quota.
- **Copy and translation keys.** The prompt uses new keys, `assistant.anon.limit_reached`
  and `assistant.anon.login_button`; the banner's `assistant.anon.loginRequired` and
  `assistant.anon.login` were removed.
- **"Register" vs. "sign up".** UX-copy guidance prefers "sign up"; keep "register" only
  if it matches Sefaria's own login page.

## 2. Search no-results button

### 2.1 Signed-in user clicks Try Library Assistant

- **Rule:** the question the button sends always opens in a **new chat**. It is never added
  to the conversation the user already has open. This holds for signed-in users and for
  logged-out visitors with free answers left.
- **Exception, logged-out visitor already at the limit:** no new chat and nothing sent. The
  assistant opens on their **last conversation**, with the login prompt in place of the text
  box (1.2). After they log in, the text box is empty (1.9).
- **Steps:** signed in, any search tab with no results, click **Try Library Assistant**.
- **Expected:** the assistant opens (or stays open) on a new, empty chat and the rewritten
  question is sent there right away. The previous conversation is untouched and stays in
  History. If the open chat is already empty, the question is sent in it.
- Same for logged-out visitors, until they hit the limit (1.9).

### 2.2 Closing and reopening from the same no-results page

- **Steps:** click **✦ Search with Library Assistant**, close the assistant, click it again.
- **Expected:** the assistant reopens on the chat it started; the question is not asked
  again. The same holds when moving to another empty tab of the same search. A new search
  with no results asks its own question once.

## 3. Chat history

### 3.1 Opening a saved chat that takes a moment to load

- **Steps:** signed in, open History, pick a chat not yet opened in this visit.
- **Expected:** the open chat clears right away and the chat area shows the animated
  loader alone (Lucide `loader-circle`, 24px, Semantic/Icon/Muted #6F6F6F, one turn every
  0.8s as on lucide-animated.com), centered on the chat area
  ([Figma](https://www.figma.com/design/Y31hDgxSjr0l1fcm0nNJSD/Library-Assistant-Wireframes?node-id=7430-9494)).
  The picked chat then appears at its last message. Screen readers hear "Loading
  messages...". If loading fails, the previous chat comes back. A chat already opened in
  this visit appears at once, with no loader.
- Phones and desktop alike. The same loader shows at the top while older messages load
  above an open chat.

## Still to cover from this PR

- [ ] Entry point: the Ask pill, on phones and desktop (decided; circle, input bar and wide
      pill removed)
- [ ] LA open on mobile: full screen only (decided; split screen removed)
- [ ] Header item, banner above Browse, search "Ask" row, resources-panel item, no-results
      button, mobile menu item (Sefaria side, PR Sefaria/Sefaria-Project#3782)
- [ ] Reply-ready notice (decided: always on)
- [ ] Button color and icon (decided: purple Ask pill with the star; the open assistant
      stays Sefaria blue)
- [x] Sandbox controls removed
