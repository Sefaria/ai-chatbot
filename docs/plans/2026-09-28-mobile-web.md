# Library Assistant on mobile web

Status: built on branch `lam-opus`; Sefaria-Project counterpart on its `lam-opus` branch.

## Goal

Logged-in phone users get the assistant. Before this, Sefaria hid it below its MOBILE breakpoint, and the widget had no small-screen layout.

## Decisions

- **Full-screen sheet under `(max-width: 600px), (max-height: 500px)`.** 600px matches Sefaria's older mobile threshold. The height clause catches landscape phones, where the 456px panel can't fit. Wider touch screens (tablets) keep the floating panel.
- **Never auto-open on phones.** A sheet that opens on load hides the page the user came for.
- **Back closes the sheet.** It pushes a duplicate of `history.state`. The popstate listener is registered at bundle load, because `window` runs popstate listeners in registration order even with `capture`, and it stops propagation, so ReaderApp never sees the pop.
- **Links close the sheet, then navigate after the pop.** Navigating first would push the new URL above the sheet's entry, and a later Back would land on a dead duplicate.
- **Closed button recedes on forward scroll** (any scroller, via a capture listener), so it doesn't cover the text being read.
- **Keyboard:** the sheet follows `visualViewport`, because Chrome Android's default `resizes-visual` and iOS both leave `position: fixed` under the keyboard.
- **Touch:** tooltips only for mouse pointers (a tap's emulated `mouseenter` left them stuck), and history-row menus are always visible under `hover: none`.
- **Host entry point:** a `chatbot:open` document event, used by Sefaria's mobile menu.

## Follow-ups

- If the NG mobile reader (Sefaria-Project `ng-mobile`, separate React tree) ships, it must render `<lc-chatbot>` too.
- The logged-out promo banner stays desktop-only (product call).
