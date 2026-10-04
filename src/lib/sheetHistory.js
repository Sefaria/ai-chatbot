/**
 * One history entry for the full-screen sheet, so Back closes it instead of leaving the page.
 *
 * The listener is registered when the bundle loads, not when the widget mounts: popstate
 * listeners on window run in registration order (capture doesn't change that), and a host
 * that loads this bundle before its app (Sefaria does, in <head>) then never sees the
 * sheet's pop. A host that loads it later sees a pop back to its own current state.
 */

let hasEntry = false;
let onPop = null;

window.addEventListener('popstate', (e) => {
  if (!hasEntry) return;
  hasEntry = false;
  e.stopImmediatePropagation();
  onPop?.();
});

export function pushSheetEntry(callback) {
  if (hasEntry) return;
  history.pushState(history.state, '');
  hasEntry = true;
  onPop = callback;
}

/** Leave the entry from the UI; `callback` still runs once the pop lands. */
export function popSheetEntry() {
  if (hasEntry) history.back();
}

export function hasSheetEntry() {
  return hasEntry;
}
