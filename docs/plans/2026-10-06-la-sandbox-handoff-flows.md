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
  - (Hebrew strings still to be added; it shows the English for now.)
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

- Same flows 1.1–1.5 inside the full-screen sheet (and the split-screen sheet, if that's
  chosen in Sandbox controls).
- The prompt replaces the text box. Check the button stays clear of the iOS
  home bar and doesn't scroll sideways at 320px.
- After logging in, the sheet reopens on the conversation (the only time a sheet opens by
  itself).

### 1.8 Hebrew

- All of the above with `interface-lang="he"`: the prompt in Hebrew, right-aligned.

### Edge cases and open questions

- **Clearing site data gives a fresh quota.** Free answers are counted per anonymous id,
  which lives in the browser's storage, so clearing it or using another browser resets the
  count. Fine for a soft limit; engineering and product to confirm.
- **Failed answers don't count.** Only successful answers use up the quota.
- **Copy and translation keys.** The prompt uses new keys, `assistant.anon.limitReached`
  and `assistant.anon.loginButton`; the banner's `assistant.anon.loginRequired` and
  `assistant.anon.login` were removed.
- **"Register" vs. "sign up".** UX-copy guidance prefers "sign up"; keep "register" only
  if it matches Sefaria's own login page.

## Still to cover from this PR

- [ ] Entry points: circle, input bar, wide pill, Ask pill (phone); circle or Ask pill
      (desktop); "Circle only"
- [ ] LA open on mobile: full screen vs. split screen, and dragging the split sheet
- [ ] Header item, banner above Browse, search "Ask" row, resources-panel item, no-results
      button, mobile menu item (Sefaria side, PR Sefaria/Sefaria-Project#3782)
- [ ] Reply-ready notice
- [ ] Button color (blue or purple) and icon (Samekh or star)
- [ ] Sandbox controls themselves (POC only; to be removed before shipping)
