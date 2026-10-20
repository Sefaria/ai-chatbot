/**
 * One history entry for the full-screen sheet, so Back closes it instead of leaving the page.
 *
 * The listener is registered when the bundle loads, not when the widget mounts: popstate
 * listeners on window run in registration order (capture doesn't change that), and a host
 * that loads this bundle before its app (Sefaria does, in <head>) then never sees the
 * sheet's pop. A host that loads it later sees a pop back to its own current state.
 */

// The sheet's entry is the one the browser is on: the next pop leaves it
let isSheetEntryCurrent = false;
// Runs once the pop has left the sheet's entry
let onSheetEntryPopped = null;
// The page the sheet opened over, which a plain Back returns to
let sheetEntryUrl = null;

window.addEventListener('popstate', (e) => {
  if (!isSheetEntryCurrent) return;
  isSheetEntryCurrent = false;
  // Back to the page the sheet opened over only closes the sheet: the host needn't know. A jump
  // further (picked from Back's long-press menu, or history.go(-2)) lands on another page, which
  // the host must show, so it gets that pop; the sheet still closes.
  if (location.href === sheetEntryUrl) e.stopImmediatePropagation();
  onSheetEntryPopped?.();
});

export function pushSheetEntry(callback) {
  if (isSheetEntryCurrent) return;
  sheetEntryUrl = location.href;
  history.pushState(history.state, '');
  isSheetEntryCurrent = true;
  onSheetEntryPopped = callback;
}

/** Leave the entry from the UI; `callback` still runs once the pop lands. */
export function popSheetEntry() {
  if (isSheetEntryCurrent) history.back();
}

export function hasSheetEntry() {
  return isSheetEntryCurrent;
}
