# LA sandbox (PR #236): user flows for the engineering handoff

A running list of the flows to attach to PR #236 when it goes to engineering. Each flow
says where it starts, what the person does, and what should happen, on desktop and phone,
in English and Hebrew.

Status: in progress. Parts of the PR still to cover (see the end).

- Live preview: https://la-sandbox.cauldron.sefaria.org/texts?chatbot_version=236
- Figma: [Library Assistant Wireframes](https://www.figma.com/design/Y31hDgxSjr0l1fcm0nNJSD/Library-Assistant-Wireframes)

## 1. Search no-results button

### 1.1 Signed-in user clicks Try Library Assistant

- **Rule:** the question the button sends always opens in a **new chat**; it is never
  added to the conversation they already have open.
- **Steps:** signed in, any search tab with no results, click **Try Library Assistant**.
- **Expected:** the assistant opens (or stays open) on a new, empty chat and the rewritten
  question is sent there right away. The previous conversation is untouched and stays in
  History. If the open chat is already empty, the question is sent in it.

### 1.2 Closing and reopening from the same no-results page

- **Steps:** click **✦ Search with Library Assistant**, close the assistant, click it again.
- **Expected:** the assistant reopens on the chat it started; the question is not asked
  again. The same holds when moving to another empty tab of the same search. A new search
  with no results asks its own question once.

## 2. Chat history

### 2.1 Opening a saved chat that takes a moment to load

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

## 3. Cookie banner on phones

### 3.1 The Ask button sits above the cookie banner

- **Start:** a phone (or any window up to 842px wide) where the visitor hasn't accepted cookies, so Sefaria's cookie banner spans the bottom of the screen.
- **Expected:** the closed Ask button sits 16px above the banner, not behind it. When the visitor taps OK, the button slides back to its usual spot (0.2s, at every width up to 842px, reduced motion included). If the banner's text wraps to more lines, the button follows its height.
- **How:** Sefaria publishes the banner's height as `--lc-launcher-bottom-offset` on `:root` (`static/js/sefaria/launcherOffset.js`); the widget adds it to the closed button's distance from the bottom.
- **Desktop:** unchanged. At 843px and wider the banner is a 360px card in the corner, so the offset is 0.
- **Open on a phone:** the full-screen assistant is drawn above the banner, so the banner can't cover the text box; the banner shows again when the assistant closes (decided 2026-10-12).
- **Known limit (decided 2026-10-11):** a short-term fix. With One Tap, fundraising modals and other banners also on a phone screen, it gets crowded; a longer-term design for stacked overlays is still needed.

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
