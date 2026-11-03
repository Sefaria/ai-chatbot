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

// Marks the sheet's entry in history.state, so a host that navigates from inside the open
// sheet (its own control in it, e.g. a sign-up option) can tell, and replace the entry with its
// page instead of pushing on top of it; then Back from that page goes straight back.
export const SHEET_ENTRY_STATE_KEY = 'lcSheet';

export function pushSheetEntry(callback) {
  if (isSheetEntryCurrent) return;
  sheetEntryUrl = location.href;
  history.pushState({ ...history.state, [SHEET_ENTRY_STATE_KEY]: true }, '');
  isSheetEntryCurrent = true;
  onSheetEntryPopped = callback;
}

/** Whether the browser is on a sheet's entry (a widget that was removed while open left it). */
export function isOnSheetEntry() {
  return history.state?.[SHEET_ENTRY_STATE_KEY] === true;
}

/**
 * Take over the sheet's entry the browser is on, as a widget just put back by the host does
 * (Back from a host page opened inside the sheet): the next Back closes the sheet again.
 */
export function adoptSheetEntry(callback) {
  sheetEntryUrl = location.href;
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

/**
 * The widget is going away (the host removed it, e.g. to show its own page). Its entry is no
 * longer the sheet's to handle: the next Back belongs to the host, who may have replaced it.
 */
export function forgetSheetEntry() {
  isSheetEntryCurrent = false;
  onSheetEntryPopped = null;
}
