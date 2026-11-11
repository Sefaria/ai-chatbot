/**
 * The host page's light/dark theme.
 *
 * Sefaria says which theme is on only with `data-theme` on <html>: "dark" or "light". It can be
 * missing for a moment before Sefaria's head script sets it, and it changes without a reload
 * (the visitor picks a theme). Anything but "dark" is light.
 */

/** 'dark' when the page's root element says so, otherwise 'light'. */
export function readHostTheme(root) {
  return root?.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
}

/**
 * Calls onChange(theme) with the page's theme now, then again each time it changes.
 * Returns a function that stops watching, so it can be returned from an $effect.
 */
export function followHostTheme(onChange, root = typeof document === 'undefined' ? null : document.documentElement) {
  if (!root) return () => {};
  let theme = readHostTheme(root);
  onChange(theme);
  const observer = new MutationObserver(() => {
    const next = readHostTheme(root);
    if (next === theme) return;
    theme = next;
    onChange(theme);
  });
  observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] });
  return () => observer.disconnect();
}
