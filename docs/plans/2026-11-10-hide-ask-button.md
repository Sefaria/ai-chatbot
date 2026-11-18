# Hide or show the Ask button

Since the Library Assistant opened to logged-out and mobile visitors, people have complained
that the floating "✦ Ask" button covers page content. Visitors can now turn it off from the
assistant's kebab menu.

Ticket: [sc-48380](https://app.shortcut.com/sefaria/story/48380). Branches: ai-chatbot
`feat/la-hide-ask-button` ([Sefaria/ai-chatbot#302](https://github.com/Sefaria/ai-chatbot/pull/302)),
Sefaria-Project `la-hide-ask-button` ([Sefaria/Sefaria-Project#4004](https://github.com/Sefaria/Sefaria-Project/pull/4004)),
both off their default branch. Preview on the `la-hide-ask-button` Cauldron with `?chatbot_version=302`.

## Decisions

- **Menu item**: "Hide Ask button" (eye-off icon) while the button shows, "Show Ask button"
  (eye icon) while it's hidden. Below Help: the last item logged out, right above "Disable in settings" logged in.
- **Modal**: reuses the feedback modal's card. Title "Hide or show Ask button"; a
  "Show the Ask button" switch that applies at once; Done closes it. A little larger and
  more spaced out than the feedback modal (title one step up, 14px body, 44×24 switch in a
  44px row).
- **Copy**: "You can continue to open the Library Assistant from the main menu." Then:
  - logged out: "To remove the Library Assistant from the main menu, **sign up** or
    **log in**." Each links straight to its flow (`/register`, `/login`) and back to the page.
  - logged in: "You can remove the Library Assistant from the main menu in **settings**."
    Links to `/settings/account#libraryAssistantSetting`, the on/off setting.
  - "main menu" everywhere (not "Sefaria header" / "Sefaria menu").
  - Each sentence is one key with link placeholders, so translators can reorder it. All copy
    goes through Weblate; the keys land in `en.json` with draft text.
- **Hidden** means hidden on every page, phones and desktop, until turned back on. The
  header and mobile-menu entry points still open the assistant.
- **Saved**: logged out, in this browser. Logged in, also in the account
  (`settings.library_assistant_floating_button_hidden`), so it follows the user across devices;
  the account's value wins over the browser's. The widget tells the host with
  `chatbot:launcher_visibility`; Sefaria saves it via `/api/profile` and passes it back as
  `hide-launcher` (and keeps the new value in `ReaderApp` state, so a remounted widget doesn't
  get the page-load value).
- **Logging out** clears the browser's copy (with the logged-in chat, in
  `clearLoggedInChatWhenLoggedOut`), so the button shows again; logging back in restores the
  account's choice.
- **Names**: keys, code and the account setting say `floating_button`, not `ask_button`, since the
  button's word ("Ask") may change. The English copy still says "Ask button".
- **Analytics**: clicks are named by ARIA label for now (our analytics reads them), at an
  accessibility cost: the menu item is "Hide or show Ask button"; the switch is "Hide Ask button"
  while on and "Show Ask button" while off, so a click's name says what it did. No other events.
  Clicks inside the dialog now bubble (the backdrop checks its own target) so the tracker sees them.
- **Accepted**: with the pill hidden there's no reply badge, and the relabelled no-results
  pill doesn't show (the link in Sefaria's no-results text still asks the search; opening from
  the header just opens the assistant).

## To do before merge

- [x] **Weblate strings**: the 11 `assistant.menu.*_floating_button` /
  `assistant.floating_button_modal.*` keys, English and Hebrew, came in through Weblate
  ([Sefaria/ai-chatbot#313](https://github.com/Sefaria/ai-chatbot/pull/313)). This branch's locale
  files now match `main` exactly, so the PR carries keys only.
- [ ] **Behavioral tests**: add more e2e coverage beyond `e2e/floating-button-setting.spec.js`
  (which covers hide persists, host reopen, log in / sign up / account settings links, the
  `chatbot:launcher_visibility` event, account wins over browser).
- [ ] **Real device**: check the menu, modal and hidden button on a real phone (so far only the
  iOS Simulator and phone-width browser).
- [x] **ARIA label strings**: `assistant.menu.floating_button_aria`,
  `assistant.floating_button_modal.toggle_hide_aria` / `toggle_show_aria` came in through Weblate
  ([Sefaria/ai-chatbot#328](https://github.com/Sefaria/ai-chatbot/pull/328)).

## Open question

- **Hide, then sign up**: a logged-out visitor who hides the button and then signs up from the
  modal gets an account with no saved value, so Sefaria passes `hide-launcher="false"` and the
  button comes back. The ticket doesn't cover it; one option is to adopt the browser's choice
  when the account has none.

## Follow-ups

- **Analytics labels**: move these clicks to `data-feature-name` (and drop the ARIA labels)
  once analytics no longer relies on ARIA labels.
- **"Disable in settings"** (logged-in kebab item) still links to the top of
  `/settings/account`; point it at `#libraryAssistantSetting` too.
- **Done button border**: the thin gray border (shared with the feedback modal) is cleaned up
  in the LA variables project.

## General LA follow-ups (accessibility)

- **Modal focus**: opening the feedback, delete and Ask button modals doesn't move focus into
  them, so Escape only works after clicking inside, and focus isn't returned on close.

## Not verified

- The Sefaria side has unit tests but hasn't been run end to end on a Sefaria page.
