/**
 * Tooltip bubbles rendered into `document.body`.
 *
 * The widget's panel sets `overflow: hidden` (rounded corners + scroll
 * containment), so a bubble drawn inside the shadow tree is clipped at the panel
 * edge (sc-45831). Portalling to <body> with `position: fixed` and a max z-index
 * escapes every clipping/stacking ancestor; the bubble is then clamped to the
 * viewport and flipped above the trigger when there is no room below, so it is
 * always fully readable.
 */

const BG = '#3a3a3a';
const MAX_WIDTH = 252;
const GAP = 8; // trigger → bubble
const MARGIN = 8; // bubble → viewport edge
const CARET_SIZE = 6; // border width; the caret box is twice this
const CARET_INSET = 16; // keeps the caret clear of the rounded corners

/** Position an already-mounted bubble (and its caret) relative to the trigger. */
function place(bubble, caret, anchor) {
  const r = anchor.getBoundingClientRect();
  const w = bubble.offsetWidth;
  const h = bubble.offsetHeight;
  const rtl = getComputedStyle(anchor).direction === 'rtl';

  const below = r.bottom + GAP + h + MARGIN <= window.innerHeight;
  const top = below ? r.bottom + GAP : r.top - GAP - h;

  const rawLeft = rtl ? r.right - w : r.left;
  const maxLeft = Math.max(MARGIN, window.innerWidth - w - MARGIN);
  const left = Math.min(Math.max(MARGIN, rawLeft), maxLeft);

  bubble.style.top = `${Math.round(Math.max(MARGIN, top))}px`;
  bubble.style.left = `${Math.round(left)}px`;

  const caretX = Math.min(Math.max(r.left + r.width / 2 - left, CARET_INSET), w - CARET_INSET);
  Object.assign(caret.style, {
    left: `${Math.round(caretX - CARET_SIZE)}px`,
    top: below ? `${-2 * CARET_SIZE}px` : `${h}px`,
    borderBottomColor: below ? BG : 'transparent',
    borderTopColor: below ? 'transparent' : BG,
  });
}

/**
 * Mount a tooltip bubble for `anchor` and return it (or null when `text` is empty).
 * Pass the result to `hideTooltip` to remove it.
 */
export function showTooltip(anchor, text) {
  const label = (text ?? '').trim();
  if (!label) {
    return null;
  }

  const bubble = document.createElement('div');
  bubble.className = 'lc-tooltip-bubble';
  bubble.dataset.testid = 'la-tooltip';
  bubble.textContent = label;
  Object.assign(bubble.style, {
    position: 'fixed',
    top: '0',
    left: '0',
    maxWidth: `${MAX_WIDTH}px`,
    width: 'max-content',
    background: BG,
    color: '#fff',
    font: '12px/1.4 Roboto, sans-serif',
    textAlign: 'start',
    padding: '8px 12px',
    borderRadius: '12px',
    whiteSpace: 'normal',
    wordBreak: 'break-word',
    pointerEvents: 'none',
    zIndex: '2147483647',
    boxShadow: '0 2px 8px rgba(0,0,0,0.25)',
  });

  const caret = document.createElement('div');
  Object.assign(caret.style, {
    position: 'absolute',
    width: '0',
    height: '0',
    border: `${CARET_SIZE}px solid transparent`,
  });
  bubble.appendChild(caret);

  document.body.appendChild(bubble);
  place(bubble, caret, anchor);
  return bubble;
}

export function hideTooltip(bubble) {
  bubble?.remove();
}

/**
 * Svelte action: show `text` on hover. The bubble is torn down on scroll/resize
 * because `position: fixed` does not follow the trigger.
 */
export function tooltip(node, text = '') {
  let bubble = null;
  let label = text;

  function hide() {
    hideTooltip(bubble);
    bubble = null;
    window.removeEventListener('scroll', hide, true);
    window.removeEventListener('resize', hide);
  }

  function show() {
    hide();
    bubble = showTooltip(node, label);
    if (bubble) {
      window.addEventListener('scroll', hide, true);
      window.addEventListener('resize', hide);
    }
  }

  node.addEventListener('mouseenter', show);
  node.addEventListener('mouseleave', hide);

  return {
    update(next) {
      label = next ?? '';
      if (bubble) {
        show();
      }
    },
    destroy() {
      hide();
      node.removeEventListener('mouseenter', show);
      node.removeEventListener('mouseleave', hide);
    },
  };
}
