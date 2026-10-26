import { tick } from 'svelte';

// pinChatToBottom is running: the resize observer below stands aside meanwhile.
// One flag for the page, like sheetHistory.js: a page has one assistant.
let scrollingToEnd = false;

/**
 * Svelte action for the message list, on phones: `use:keepAnchorOnResize={isFullscreen}`.
 * When the chat area changes height (the keyboard opens or closes, the text box grows), keep
 * what was just above the text box in place instead of letting it slide under it.
 */
export function keepAnchorOnResize(el, enabled) {
  let observer = null;

  function observe(on) {
    observer?.disconnect();
    observer = null;
    if (!on) return;
    let previous = el.clientHeight;
    observer = new ResizeObserver(() => {
      const delta = previous - el.clientHeight;
      // A hidden list (sheet closed) measures 0: just remember the new height
      if (delta && previous && el.clientHeight && !scrollingToEnd) {
        el.scrollTo({ top: el.scrollTop + delta, behavior: 'instant' });
      }
      previous = el.clientHeight;
    });
    observer.observe(el);
  }

  observe(enabled);
  return {
    update(on) {
      if (on === enabled) return;
      enabled = on;
      observe(on);
    },
    destroy() { observer?.disconnect(); }
  };
}

// Counts pins, so an earlier one finishing (Send, then Stop within a second) leaves the flag alone
let pinRun = 0;

/**
 * Phones: smoothly scroll to the end once the chat area has resized (its resize observer runs a
 * frame later and would cut a smooth scroll short). Any momentum scroll is stopped first, since
 * iOS ignores scroll changes made while one is still gliding, and the observer stands aside
 * while the scroll runs (the keyboard closing after Send resizes the chat again).
 * `getList()` returns the message list, read once the chat has resized.
 */
export async function pinChatToBottom(getList) {
  const run = ++pinRun;
  scrollingToEnd = true; // from the start: the keyboard closing after Send would pull the chat up
  await tick();
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  const el = getList();
  if (!el) { if (run === pinRun) scrollingToEnd = false; return; }
  el.style.overflowY = 'hidden';
  void el.offsetHeight; // apply it, which stops the glide
  el.style.overflowY = '';
  el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  // The reader touching the chat stops the glide to read: don't pull them back down after
  let userScrolled = false;
  const noteUserScroll = () => { userScrolled = true; };
  el.addEventListener('touchstart', noteUserScroll, { passive: true });
  el.addEventListener('wheel', noteUserScroll, { passive: true });
  await new Promise(resolve => {
    const done = () => { clearTimeout(timer); el.removeEventListener('scrollend', done); resolve(); };
    const timer = setTimeout(done, 1000); // no scrollend if it was already there
    el.addEventListener('scrollend', done);
  });
  el.removeEventListener('touchstart', noteUserScroll);
  el.removeEventListener('wheel', noteUserScroll);
  if (run !== pinRun) return; // a newer pin owns the flag now
  scrollingToEnd = false;
  // The chat grew or shrank under the scroll (e.g. the keyboard closing): finish the last bit
  if (!userScrolled && el.scrollHeight - el.clientHeight - el.scrollTop > 2) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
}
