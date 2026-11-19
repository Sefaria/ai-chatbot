// "I'm here": the open panel hops twice when the host's entry point (e.g. Sefaria's header
// pill) is clicked while it's already open. Floating hops up; docked hops toward the page.
export function nudgeKeyframes(mode, rtl = false) {
  const toward = rtl ? 1 : -1; // docked sits at the inline end, so the page is the other way
  const move = (px) => mode === 'docked' ? `translateX(${toward * px}px)` : `translateY(${-px}px)`;
  const offsets = [0, .18, .38, .56, .76, 1];
  // Easing per hop, as in a CSS @keyframes (an `easing` option would stretch over all of it)
  return [0, 16, 0, 7, 0, 0].map((px, i) => ({ transform: move(px), offset: offsets[i], easing: 'cubic-bezier(.3,.7,.4,1)' }));
}

export function nudge(el, mode, rtl) {
  if (!el?.animate || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  el.animate(nudgeKeyframes(mode, rtl), { duration: 700 });
}
