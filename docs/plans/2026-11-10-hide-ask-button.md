# Hide or show the Ask button

Since the Library Assistant opened to logged-out and mobile visitors, people have complained
that the floating "✦ Ask" button covers page content. Visitors can now turn it off from the
assistant's kebab menu.

Branches: ai-chatbot `feat/la-hide-ask-button` (draft PR), Sefaria-Project `la-hide-ask-button`
(`~/code/Sefaria-Project-la-hide-ask-button`, no PR yet), both off their default branch.
Preview on the `la-hide-ask-button` Cauldron with `?chatbot_version=<ai-chatbot PR number>`.

## Decisions

- **Menu item**: "Hide Ask button" (eye-off icon) while the button shows, "Show Ask button"
  (eye icon) while it's hidden. Below Help: the last item logged out, right above "Disable in settings" logged in.
- **Modal**: reuses the feedback modal's card. Title "Hide or show Ask button"; a
  "Show the Ask button" switch that applies at once; Done closes it. A little larger and
  more spaced out than the feedback modal (title one step up, 14px body, 44×24 switch in a
  44px row).
- **Copy**: "You can continue to open the Library Assistant from the main menu." Then:
  - logged out: "To remove the Library Assistant from the main menu, **log in** or
    **sign up**." Each links straight to its flow (`/login`, `/register`) and back to the page.
  - logged in: "To remove the Library Assistant from the main menu, go to **account settings**."
    Links to `/settings/account#libraryAssistantSetting`, the on/off setting.
  - "main menu" everywhere (not "Sefaria header" / "Sefaria menu").
  - Each sentence is one key with link placeholders, so translators can reorder it. All copy
    goes through Weblate; the keys land in `en.json` with draft text.
- **Hidden** means hidden on every page, phones and desktop, until turned back on. The
  header and mobile-menu entry points still open the assistant.
- **Saved**: logged out, in this browser. Logged in, also in the account
  (`settings.library_assistant_ask_button_hidden`), so it follows the user across devices;
  the account's value wins over the browser's. The widget tells the host with
  `chatbot:launcher_visibility`; Sefaria saves it via `/api/profile` and passes it back as
  `hide-launcher`.
- **No analytics yet.**
- **Accepted**: with the pill hidden there's no reply badge, and the relabelled no-results
  pill doesn't show (the link in Sefaria's no-results text still asks the search; opening from
  the header just opens the assistant).

## Follow-ups

- **"Disable in settings"** (logged-in kebab item) still links to the top of
  `/settings/account`; point it at `#libraryAssistantSetting` too.
- **Done button border**: the thin gray border (shared with the feedback modal) is cleaned up
  in the LA variables project.

## General LA follow-ups (accessibility)

- **Modal focus**: opening the feedback, delete and Ask button modals doesn't move focus into
  them, so Escape only works after clicking inside, and focus isn't returned on close.

## Not verified

- The Sefaria side has unit tests but hasn't been run end to end on a Sefaria page.
