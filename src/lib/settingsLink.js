// Link to the Library Assistant settings page, carrying where to come back to as `from`
// (the current path and query). Without a window there is no return address, so no `from`.
export function settingsHref(hash = '') {
  const base = '/settings/library-assistant';
  const query =
    typeof window !== 'undefined' && window.location
      ? `?from=${encodeURIComponent(window.location.pathname + window.location.search)}`
      : '';
  return `${base}${query}${hash ? `#${hash}` : ''}`;
}
