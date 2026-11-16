// The answering model ends a personalized lab answer with `<profile_used>…</profile_used>`.
// While the answer streams, the widget hides what the server will take off the end: every closed
// tag at the end (in a loop, like the server's split), an opening tag still waiting for its close,
// and a trailing piece of an opening tag that may still become one. The grammar is the server's
// (`<\s*profile_used\s*>`, any case). A tag in the middle of the text is never hidden: the server
// only takes tags off the end, so the widget shows what the server keeps.

const OPEN_SRC = String.raw`<\s*profile_used\s*>`;
const CLOSE_SRC = String.raw`<\s*/\s*profile_used\s*>`;
const OPEN = new RegExp(OPEN_SRC, 'gi');
const CLOSE = new RegExp(CLOSE_SRC, 'i');
// A closed tag that ends the text; its inner text holds no other tag (as on the server).
const TRAILING = new RegExp(
  String.raw`${OPEN_SRC}((?:(?!${OPEN_SRC}|${CLOSE_SRC})[\s\S])*)${CLOSE_SRC}\s*$`,
  'i'
);
// The end of the text is a start of an opening tag that is not complete yet: `<`, `< prof`,
// `<profile_used `, … (each letter of the name optional from the right, spaces as the server allows).
const PARTIAL_OPEN = new RegExp(
  String.raw`<\s*` +
    [...'profile_used'].reduceRight((rest, letter) => `(?:${letter}${rest})?`, String.raw`\s*`) +
    '$',
  'i'
);
const NOTHING_LINE = 'Nothing from your profile changed this answer.';
const FROM_LINE = /^From your profile, I took [^\r\n<>]{1,250} into account\.$/;

/** The text before an opening tag that ends the text without a close, or null. */
function cutUnclosed(text) {
  let last = null;
  for (const match of text.matchAll(OPEN)) last = match;
  if (!last) return null;
  const rest = text.slice(last.index + last[0].length);
  return CLOSE.test(rest) ? null : text.slice(0, last.index);
}

/** The streamed text as it may be shown: without the tags at its end or a start of one. */
export function hideProfileUsed(text) {
  let shown = text;
  for (;;) {
    const partial = PARTIAL_OPEN.exec(shown);
    if (partial) {
      shown = shown.slice(0, partial.index);
      continue;
    }
    const unclosed = cutUnclosed(shown);
    if (unclosed !== null) {
      shown = unclosed;
      continue;
    }
    const closed = TRAILING.exec(shown);
    if (closed) {
      shown = shown.slice(0, closed.index);
      continue;
    }
    return shown;
  }
}

/** True when the line is one of the two shapes the server accepts. */
export function isProfileUsedLine(line) {
  return typeof line === 'string' && (line === NOTHING_LINE || FROM_LINE.test(line));
}
