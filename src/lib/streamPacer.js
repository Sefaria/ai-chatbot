/**
 * Paces streamed markdown so it appears in whole units (word, sentence,
 * paragraph) at a steady rhythm instead of in uneven network bursts.
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

// When units pile up, release several per tick so we never fall far behind.
const CATCH_UP_DIVISOR = 4;

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
 * @param {number} opts.intervalMs - Time between releases
 * @param {function(string): void} opts.onReveal - Called with all text released so far
 */
export function createStreamPacer({ unit, intervalMs, onReveal }) {
  let buffer = '';
  let shown = 0;
  let finished = false;
  let timer = null;
  let resolveDrained = null;

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

  function stop() {
    clearInterval(timer);
    timer = null;
  }

  function step() {
    const ends = pendingEnds();
    if (ends.length) {
      shown = ends[Math.ceil(ends.length / CATCH_UP_DIVISOR) - 1];
      onReveal(buffer.slice(0, shown));
    } else if (finished) {
      stop();
      resolveDrained?.();
    }
  }

  function start() {
    timer ??= setInterval(step, intervalMs);
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
    }
  };
}
