// Sefaria design system tokens (Sefaria/design-system), copied into the bundle at build time.
//
// LCChatbot puts this CSS in a style element inside its shadow root, as a text node, never as HTML:
// an @import in the component's own styles wouldn't resolve there. Each file declares its tokens on
// `:root, :host`, and `:host` is what applies inside the shadow root.
//
// This lives in a .js file because Svelte's preprocessor treats any style tag text in a .svelte file,
// even inside a string or comment, as the component's style block.
import core from '@sefaria/design-system/core.css?inline';
import semantic from '@sefaria/design-system/semantic.css?inline';

export const designSystemCss = `${core}\n${semantic}`;

// Previews: the host page (Sefaria, outside production) may pass a branch name or commit of
// Sefaria/design-system in the `design-system-preview` attribute. The widget then loads that
// version's CSS on top of the bundled one, so a design-system PR can be seen before it's released.
const PREVIEW_SOURCE = 'https://raw.githubusercontent.com/Sefaria/design-system';
const REF = /^[A-Za-z0-9._-]+(\/[A-Za-z0-9._-]+)*$/;

/** Whether `ref` looks like a branch name or commit (no "..", no leading or trailing slash). */
export function isPreviewRef(ref) {
  return typeof ref === 'string' && ref.length <= 100 && REF.test(ref) && !ref.includes('..');
}

// A preview is unreviewed CSS (any branch, or a commit), so it's used only if it is nothing but
// token declarations: `:root, :host` blocks (or their dark-mode form) of `--name: value;` lines, with
// no URL functions, at-rules, escapes or markup. Custom properties alone can't load anything or draw
// over the page. Rejecting is safer than stripping, which CSS escapes can get around.
const BLOCK_SELECTOR = String.raw`(?::root\s*,\s*:host|:root\[data-theme="dark"\]\s*,\s*:host\(\[data-theme="dark"\]\))`;
const DECLARATION = String.raw`\s*--[A-Za-z0-9-]+\s*:[^;{}@\\<]*;`;
const TOKEN_CSS = new RegExp(String.raw`^\s*(?:${BLOCK_SELECTOR}\s*\{(?:${DECLARATION})*\s*\}\s*)*$`);
const URL_FUNCTION = /(?:url|image-set|image|cross-fade|element|expression)\s*\(/i;
const MAX_PREVIEW_LENGTH = 200_000;

/** Whether `css` holds only design-system token declarations (see above). */
export function isTokenCss(css) {
  if (typeof css !== 'string' || css.length > MAX_PREVIEW_LENGTH) return false;
  const text = css.replace(/\/\*[\s\S]*?\*\//g, ' ');
  return TOKEN_CSS.test(text) && !URL_FUNCTION.test(text);
}

/**
 * The CSS of that design-system version, or '' if `ref` isn't valid, the files can't be loaded, or
 * they hold anything but tokens. For hosts outside production only: Sefaria never passes the
 * attribute in production.
 */
export async function fetchPreviewCss(ref, fetchImpl = fetch) {
  if (!isPreviewRef(ref)) return '';
  try {
    const files = await Promise.all(
      ['core.css', 'semantic.css'].map(async (file) => {
        const response = await fetchImpl(`${PREVIEW_SOURCE}/${ref}/${file}`);
        if (!response.ok) throw new Error(`${file}: HTTP ${response.status}`);
        return response.text();
      })
    );
    const css = files.join('\n');
    if (!isTokenCss(css)) throw new Error('it holds something other than token declarations');
    return css;
  } catch (error) {
    console.warn(`Design system preview "${ref}" couldn't be loaded:`, error);
    return '';
  }
}
