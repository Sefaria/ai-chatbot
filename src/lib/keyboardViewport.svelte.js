import { flushSync } from 'svelte';

/**
 * Phones: keeps the full-screen sheet above the on-screen keyboard.
 *
 * `input()` returns the text box, `active()` whether the sheet is open. Read `.box` for where the
 * sheet should sit ({ top, height } in px, or null for the full screen), and put `onTouchStart` and
 * `onTouchEnd` on the text box. Call it while the component initialises: it sets up an effect.
 */
export function keyboardViewport(input, active) {
  let box = $state(null);
  // The on-screen keyboard's height, once seen, per orientation: it's much taller in portrait
  const keyboardHeights = { portrait: 0, landscape: 0 };
  const orientation = () => (window.innerWidth > window.innerHeight ? 'landscape' : 'portrait');
  let inputTouchY = 0;

  // While the sheet is open, lock the page behind it and track the visual viewport, so
  // a focused field stays above the on-screen keyboard. Only a field in the sheet counts:
  // opening from a host search box leaves that keyboard closing, and sizing the sheet to
  // it would leave the sheet stuck part-way up the screen.
  $effect(() => {
    if (!active()) return;
    const root = document.documentElement;
    const overflow = root.style.overflow;
    root.style.overflow = 'hidden';
    const vv = window.visualViewport;
    const sync = () => {
      const typing = input()?.getRootNode().activeElement?.matches('input, textarea');
      if (typing) {
        box = { top: vv.offsetTop, height: vv.height };
        if (vv.height < window.innerHeight) keyboardHeights[orientation()] = window.innerHeight - vv.height - vv.offsetTop;
      }
      else box = vv.offsetTop ? { top: vv.offsetTop, height: window.innerHeight } : null;
    };
    // iOS reports the keyboard closing only ~0.5s after the text box loses focus, so the sheet
    // sat above the empty space. Give it the full height as soon as typing stops; when a tap
    // ended it (e.g. on Send), only once that tap's click has landed, since iOS fires the click
    // after the blur and moving Send first would lose it.
    const shadow = input()?.getRootNode();
    let lastTouchEnd = -Infinity;
    let stopTimer;
    const noteTouchEnd = (e) => { lastTouchEnd = e.timeStamp; };
    const grow = () => {
      clearTimeout(stopTimer);
      shadow?.removeEventListener('click', grow);
      if (input()?.getRootNode().activeElement?.matches('input, textarea')) return;
      box = vv.offsetTop ? { top: vv.offsetTop, height: window.innerHeight } : null;
    };
    const stopTyping = (e) => {
      if (e.relatedTarget?.matches?.('input, textarea')) return;
      clearTimeout(stopTimer);
      if (e.timeStamp - lastTouchEnd < 300) {
        shadow?.addEventListener('click', grow, { once: true });
        stopTimer = setTimeout(grow, 400); // the tap may have been a drag, with no click
      } else stopTimer = setTimeout(grow);
    };
    if (vv) {
      sync();
      vv.addEventListener('resize', sync);
      vv.addEventListener('scroll', sync);
      shadow?.addEventListener('focusout', stopTyping);
      shadow?.addEventListener('touchend', noteTouchEnd, { capture: true });
    }
    return () => {
      root.style.overflow = overflow;
      vv?.removeEventListener('resize', sync);
      vv?.removeEventListener('scroll', sync);
      shadow?.removeEventListener('focusout', stopTyping);
      shadow?.removeEventListener('touchend', noteTouchEnd, { capture: true });
      shadow?.removeEventListener('click', grow);
      clearTimeout(stopTimer);
      box = null;
    };
  });

  // iOS scrolls the page to keep a focused field above the keyboard, and animates that scroll
  // after it has already reported the final viewport, so the sheet dropped and climbed back.
  // Making room before the keyboard opens leaves iOS nothing to scroll.
  function makeRoomForKeyboard(e) {
    const vv = window.visualViewport;
    const inputEl = input();
    if (!active() || !vv || inputEl.disabled || inputEl.getRootNode().activeElement === inputEl) return;
    if (Math.abs(e.changedTouches[0].clientY - inputTouchY) > 10) return; // a drag, not a tap
    e.preventDefault(); // we focus it ourselves, once the sheet is out of the keyboard's way
    // Capped, so a height measured while pinch-zoomed can't squash the sheet
    const room = Math.min(keyboardHeights[orientation()] || window.innerHeight / 2, window.innerHeight * 0.6);
    box = { top: 0, height: Math.round(window.innerHeight - room) };
    flushSync();
    inputEl.focus({ preventScroll: true });
    // No on-screen keyboard came (e.g. a hardware keyboard): give the room back
    setTimeout(() => {
      if (active() && vv.height >= window.innerHeight) box = { top: vv.offsetTop, height: vv.height };
    }, 600);
  }

  return {
    get box() { return box; },
    onTouchStart(e) { inputTouchY = e.touches[0].clientY; },
    onTouchEnd: makeRoomForKeyboard
  };
}
