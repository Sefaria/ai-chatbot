// Streamed answer text goes to playback in pieces: what was already played, then a queue of pieces
// waiting to be played. The text that may be shown (see profileUsed.js) usually only grows, but it
// can shrink or change at its end, for example when a trailing piece turns out to be a tag. Then
// playback must follow the new text, not keep what was handed over before.

/**
 * Bring playback in line with a new visible string.
 *
 * @param {string} shown everything handed to playback so far (played text, then the queue)
 * @param {{ text: string }[]} queue the pieces not yet played, in order; their texts end `shown`
 * @param {string} visible the text that may be shown now
 * @returns {{ queue: { text: string }[], played: string | null, fresh: string }} the queue to keep
 *   (cut where `visible` leaves `shown`), the played text to keep when some of it must be taken
 *   back (else null), and the new text to queue. played + queue + fresh always spell `visible`.
 */
export function resyncPlayback(shown, queue, visible) {
  if (visible.startsWith(shown)) {
    return { queue, played: null, fresh: visible.slice(shown.length) };
  }
  let common = 0;
  while (common < shown.length && common < visible.length && shown[common] === visible[common]) {
    common++;
  }
  const queuedLength = queue.reduce((sum, piece) => sum + piece.text.length, 0);
  const playedLength = shown.length - queuedLength;
  const kept = [];
  let end = playedLength;
  for (const piece of queue) {
    if (end >= common) break;
    const take = Math.min(piece.text.length, common - end);
    kept.push(take === piece.text.length ? piece : { ...piece, text: piece.text.slice(0, take) });
    end += take;
  }
  return {
    queue: kept,
    played: common < playedLength ? shown.slice(0, common) : null,
    fresh: visible.slice(common),
  };
}
