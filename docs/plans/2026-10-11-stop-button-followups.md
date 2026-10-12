# Stop Button Follow-ups

Running list of changes Penina wants on the stop button. Implemented by Penina/Claude on `design-qa/stop-button` (branched from Yonadav's `feature/stop-chat-history-integration` at 2f60b73) for design QA.

Related branches: `feature/stop-chat-history-integration`, `poc/penina-stopbutton`

## Changes

- [x] **Loader / load time**
  - Reduce load time on the stop button. (Done by Yonadav, 2f60b73.)
  - ~~Replace the loader with our existing app loader.~~ Decision: remove the spinner entirely; the Stop icon stays (disabled) until the stop is confirmed.
- [x] **Input field when disabled — no color change**
  - Don't change the input field's color while generating.
  - Match current main/prod behavior: disabled state just looks like placeholder text and isn't clickable.
  - Done: removed the branch's `#e6e6e6` override; main's disabled style applies. The "Waiting for a response…" placeholder is kept.
- [ ] ~~**Keep "stopped generating" message on the canvas**~~ (reverted 2026-10-12)
  - Tried keeping the notice on every stopped turn, then reverted. Decision: keep the original agreed behavior, where the notice is removed when the next prompt is sent (topics stay).
- [x] **Multi-line prompt restored after stop**
  - Today, a prompt longer than one line comes back into the input as a tiny scrollable box after Stop, with the cursor at the start.
  - Per the original Figma: the input should grow with its content up to **3 lines**, then scroll internally.
  - At minimum, if the height stays the same, scroll the input to the bottom and put the cursor at the end of the text.
  - **Why it's tiny today** (same on main and both stop branches): the textarea is `rows="1"` with `max-height: 120px` but there is no auto-grow code, so it never gets taller than one line (~42px). The 120px cap is never reached.
  - **Math for 3 lines** (`box-sizing: border-box`, 14px font × 1.4 line-height = 19.6px/line, 10px top/bottom padding, 1px border):
    3 × 19.6 + 20 + 2 = **80.8px → `max-height: 81px`** (or `calc(3 * 1.4em + 22px)` so it tracks font size). 120px would show ~5 lines; 87px would show 3 lines plus a sliver of a 4th.
  - **Fix:** auto-grow the textarea (on input, and after restoring text on Stop: `height = auto; height = scrollHeight`), cap at the 3-line max, `overflow-y: auto` above that. CSS `field-sizing: content` is cleaner but not supported in Firefox, so it needs the JS fallback.
  - Done: `resizeInput()` runs on every input change (typing, restore, clear), `max-height: calc(3 * 1.4em + 22px)`. On restore the cursor goes to the end and the field scrolls to the bottom.
- [x] **Scroll to the stop notice**
  - On Stop, scroll the canvas to the bottom so the notice is visible (unlike a response, which keeps the user at its top). Scrolls even if the user scrolled up during generation, since Stop is deliberate.

## Mobile (once merged with `la-anon-mobile-entry`)

- [ ] **Don't focus the input after Stop on phones.** In the full-screen sheet (`isFullscreen`), restore the prompt text but skip `focus()`. Focusing opens the Android keyboard over the stop notice; iOS ignores focus that isn't from a tap, so behavior would differ by platform. Matches the anon-mobile branch, which already skips autofocus when the sheet opens. Desktop keeps focus and cursor-at-end. Can't be built on `design-qa/stop-button` because `isFullscreen` only exists on the mobile branch.
- [ ] **Stop button matches Send on phones.** Size it with the mobile branch's `--lc-send-size` (44px on phones) and grow the icon 18px → 22px in `.mode-fullscreen`, the way Send's icon goes 20px → 24px. Until then it stays a fixed 40px, because `--lc-send-size` isn't in the codebase yet.
- [ ] **No Stop tooltip on phones.** Pass an empty tooltip text when `isFullscreen`.

- [x] **Design variables (done on this branch).** Stop styles use existing codebase variables: `--lc-radius-sm`, `--lc-text-secondary`, `--lc-font-size-sm` (notice and error, so they step up to 14px on phones), `--global-dimension-200`. Hover is mouse-only (`@media (hover: hover)`); touch gets a pressed state. Still fixed for lack of a variable: 40px button size, 20px notice line height, 4px error spacing, 2px focus outline.

## Known issue (not addressed)

- For a moment after clicking Stop, a lone ✦ glyph shows where the thinking label was. Also present on Yonadav's branch.
