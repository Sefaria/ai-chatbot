// Sefaria design system tokens (Sefaria/design-system), copied into the bundle at build time.
//
// LCChatbot renders this as a style element inside its shadow root ({@html designSystemStyle} at the
// top of its markup): an @import in the component's own styles wouldn't resolve there. Each file
// declares its tokens on `:root, :host`, and `:host` is what applies inside the shadow root.
//
// This lives in a .js file because Svelte's preprocessor treats any style tag text in a .svelte file,
// even inside a string or comment, as the component's style block.
import core from '@sefaria/design-system/core.css?inline';
import semantic from '@sefaria/design-system/semantic.css?inline';

export const designSystemStyle = `<style data-design-system>${core}\n${semantic}</style>`;
