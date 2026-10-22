// Phones: drag the sheet down by its header to close it. A long drag or a quick flick closes.
const SHEET_CLOSE_DISTANCE = 120;
const SHEET_CLOSE_VELOCITY = 0.5; // px/ms

/**
 * Svelte action for the sheet's header: `use:sheetDrag={{ enabled, onMove, onSettle, onClose }}`.
 * onMove(offset) reports how far down the sheet is (0 = in place), onSettle(true/false) when it
 * starts and stops sliding home or away, and onClose() when a drag closed it.
 */
export function sheetDrag(node, params) {
  let drag = null;
  let offset = 0;
  // The slide home or away after a release, until it finishes: { closing, timer }
  let settling = null;

  function setOffset(value) {
    offset = value;
    params.onMove(value);
  }

  function finishSettling() {
    if (!settling) return;
    clearTimeout(settling.timer);
    const { closing } = settling;
    settling = null;
    if (closing) params.onClose();
    setOffset(0);
    params.onSettle(false);
  }

  function start(e) {
    if (!params.enabled || e.button > 0 || e.target.closest('button, a, input')) return;
    // Grabbed again while sliding: finish the slide now, or its timer would snap the sheet mid-drag
    const closing = settling?.closing;
    finishSettling();
    if (closing) return;
    node.setPointerCapture(e.pointerId);
    drag = { startY: e.clientY, lastY: e.clientY, lastT: e.timeStamp, velocity: 0 };
    params.onSettle(false);
  }

  function move(e) {
    if (!drag) return;
    const dt = e.timeStamp - drag.lastT;
    if (dt > 0) drag.velocity = (e.clientY - drag.lastY) / dt;
    drag.lastY = e.clientY;
    drag.lastT = e.timeStamp;
    setOffset(Math.max(0, e.clientY - drag.startY));
  }

  function end(e) {
    if (!drag) return;
    // A flick counts only if the finger was still moving when it lifted
    const moving = e.timeStamp - drag.lastT < 100;
    const flicked = moving && offset > 20 && drag.velocity > SHEET_CLOSE_VELOCITY;
    const shouldClose = offset > SHEET_CLOSE_DISTANCE || flicked;
    drag = null;
    params.onSettle(true);
    setOffset(shouldClose ? window.innerHeight : 0);
    // Not cancelled on destroy: closing removes this header, and the sheet must still settle
    settling = { closing: shouldClose, timer: setTimeout(finishSettling, 200) };
  }

  node.addEventListener('pointerdown', start);
  node.addEventListener('pointermove', move);
  node.addEventListener('pointerup', end);
  node.addEventListener('pointercancel', end);

  return {
    update(newParams) { params = newParams; },
    destroy() {
      node.removeEventListener('pointerdown', start);
      node.removeEventListener('pointermove', move);
      node.removeEventListener('pointerup', end);
      node.removeEventListener('pointercancel', end);
    }
  };
}
