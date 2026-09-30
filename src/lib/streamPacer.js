/**
 * Paces streamed markdown so it appears in whole units (word, sentence,
 * paragraph) at a steady rhythm instead of in uneven network bursts.
 *
 * It holds back a short lead before showing anything, then displays at the
 * rate text is arriving: slower when the reserve runs low (a stall is probably
 * underway), faster when it grows (a burst just landed). Short stalls become a
 * slowdown instead of a freeze followed by a rush.
 */

const BOUNDARIES = {
  // A word ends at the whitespace after it.
  word: /\S\s/g,
  // A sentence ends at . ! or ? (not a list number like "1.") plus any closing
  // quotes/markdown, followed by whitespace — or at a line break.
  sentence: /(?<!\d)[.!?]["'”’)*_\]]*\s|\n/g,
  // A paragraph (or any markdown block) ends at a blank line.
  paragraph: /\n\s*\n/g
};

const TICK_MS = 30;
// Display speed, as a multiple of the arrival rate.
const MIN_SPEED = 0.25;
const MAX_SPEED = 3;

// True while a link or bold span is still open, so we never show raw
// markdown like "[Deuteronomy" or "**bol" mid-stream.
function hasOpenMarkup(text) {
  if (text.lastIndexOf('[') > text.lastIndexOf(']')) return true;
  if (text.lastIndexOf('](') > text.lastIndexOf(')')) return true;
  return (text.split('**').length - 1) % 2 === 1;
}

/**
 * @param {Object} opts
 * @param {'word'|'sentence'|'paragraph'} opts.unit
 * @param {number} opts.leadMs - Reserve to build before showing anything; 0 shows units as soon as they complete
 * @param {function(string): void} opts.onReveal - Called with all text released so far
 */
export function createStreamPacer({ unit, leadMs, onReveal }) {
  let buffer = '';
  let shown = 0;
  let finished = false;
  let timer = null;
  let resolveDrained = null;
  let firstPushAt = 0;
  let lastTickAt = 0;
  let budget = 0;

  function pendingEnds() {
    const re = new RegExp(BOUNDARIES[unit]);
    re.lastIndex = shown;
    const ends = [];
    let match;
    while ((match = re.exec(buffer))) {
      const end = match.index + match[0].length;
      if (!hasOpenMarkup(buffer.slice(0, end))) ends.push(end);
    }
    if (finished && (ends.at(-1) ?? shown) < buffer.length) ends.push(buffer.length);
    return ends;
  }

  /** Chars per ms to display at, given how many chars are waiting to be shown. */
  function speed(backlogChars, lead = leadMs) {
    const rate = buffer.length / Math.max(1, performance.now() - firstPushAt);
    if (!lead || !rate) return Infinity;
    if (finished) return rate * MAX_SPEED;
    const reserveMs = backlogChars / rate;
    return rate * Math.min(MAX_SPEED, Math.max(MIN_SPEED, reserveMs / lead));
  }

  function stop() {
    clearInterval(timer);
    timer = null;
  }

  function step() {
    const now = performance.now();
    const elapsed = now - lastTickAt;
    lastTickAt = now;
    if (!finished && now - firstPushAt < leadMs) return;
    const ends = pendingEnds();
    if (!ends.length) {
      if (finished) {
        stop();
        resolveDrained?.();
      }
      return;
    }
    const backlog = buffer.length - shown;
    budget = Math.min(budget + speed(backlog) * elapsed, backlog);
    let next = shown;
    for (const end of ends) {
      if (end - shown > budget) break;
      next = end;
    }
    if (next > shown) {
      budget -= next - shown;
      shown = next;
      onReveal(buffer.slice(0, shown));
    }
  }

  function start() {
    if (timer) return;
    firstPushAt ||= performance.now();
    lastTickAt = performance.now();
    timer = setInterval(step, TICK_MS);
  }

  return {
    push(delta) {
      buffer += delta;
      start();
    },
    /** Marks the stream complete; resolves once everything has been revealed. */
    finish() {
      finished = true;
      start();
      return new Promise((resolve) => { resolveDrained = resolve; });
    },
    cancel() {
      stop();
      resolveDrained?.();
    },
    speed
  };
}
