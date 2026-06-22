# Code Notes & Best Practices

Conventions distilled from code review. Apply to new frontend (Svelte) code; clean up existing code opportunistically rather than in large churn-only commits.

## CSS & design tokens

- **Design tokens live in the `:host` block** of `LCChatbot.svelte` and are always defined. Reference them **without redundant fallbacks** in new code — `color: var(--semantic-text-secondary)`, not `var(--semantic-text-secondary, #575757)`. The fallback only fires if the var is undefined, which can't happen for our own `:host` tokens.
- **Centralize new tokens in `:host`** instead of hardcoding values in components. This includes:
  - **Font families** — use `var(--lc-font)` rather than per-component `font-family: 'Roboto', …`.
  - **z-index** — define a named token (e.g. `--lc-z-tooltip`) in `:host` so the whole app's stacking order is visible in one place.
- **Use CSS logical properties** so RTL (Hebrew) works automatically: `text-align: start/end` (not `left/right`), `margin-inline-*`, `padding-inline-*`, `inset-inline-*`. Physical `left`/`right` do not flip for RTL.

## JavaScript / Svelte

- **No magic numbers or strings.** Extract named constants with a comment explaining the "why" (e.g. scroll offsets, pixel thresholds, route prefixes). A reader should not have to guess what `5` or `80` means.
- **`Array.at(-1)`** to read the last element, not `arr[arr.length - 1]`.
- **Default function props in the destructuring** — `let { onToggle = () => {} } = $props()` — rather than guarding `onToggle?.()` at each call site.
- **Extract shared logic into one helper.** If two functions differ only by a flag (e.g. linkified vs. plain ref rendering, or two scroll targets), unify them and parameterize the difference.

## Sefaria refs

- **Prefer the Sefaria ref API over hand-rolled ref parsing.** The ref API (on master) accepts a path like `Genesis.1.1` and returns canonical English + Hebrew refs and their URLs. Hand-rolled regex parsing of refs is fragile (manuscripts, ranges, dictionary refs, complex titles) and duplicates logic that belongs server-side. New ref handling should call the API; treat existing client-side regex parsers (`parseSefariaRef`, `refToUrl`, `refLabel`) as legacy to migrate, not to extend.

## i18n

- **Don't hardcode language-specific punctuation.** The serial/Oxford comma is required in American English ("A, B, or C") but redundant in Hebrew ("A, B או C"). Bake list separators into the localized string, or branch on locale — never assume English punctuation rules for all languages.
- Pass full sentences as a single key; never concatenate translated fragments (see `src/CLAUDE.md`).
