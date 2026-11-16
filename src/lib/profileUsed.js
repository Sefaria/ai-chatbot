// The answering model ends a personalized lab answer with `<profile_used>…</profile_used>`.
// While the answer streams, a tag at the end of the text is hidden, and a trailing piece of the
// opening tag that may still turn into it is held back. A tag in the middle of the text is never
// hidden: the server only takes a tag off the end, so the widget shows what the server keeps.

const TAG = '<profile_used>';
const OPEN = /<\s*profile_used\s*>/gi;
const CLOSE = /<\s*\/\s*profile_used\s*>/i;
const NOTHING_LINE = 'Nothing from your profile changed this answer.';
const FROM_LINE = /^From your profile, I took [^\r\n<>]{1,250} into account\.$/;

/** The streamed text as it may be shown: without a trailing tag or a start of one. */
export function hideProfileUsed(text) {
  let last = null;
  for (const match of text.matchAll(OPEN)) last = match;
  if (last) {
    const rest = text.slice(last.index + last[0].length);
    const close = CLOSE.exec(rest);
    // Unclosed so far, or closed with nothing but whitespace after it: the tag ends the text.
    if (!close || !rest.slice(close.index + close[0].length).trim()) {
      return text.slice(0, last.index);
    }
  }
  const lower = text.toLowerCase();
  for (let keep = Math.min(TAG.length - 1, text.length); keep > 0; keep--) {
    if (lower.endsWith(TAG.slice(0, keep))) return text.slice(0, text.length - keep);
  }
  return text;
}

/** True when the line is one of the two shapes the server accepts. */
export function isProfileUsedLine(line) {
  return typeof line === 'string' && (line === NOTHING_LINE || FROM_LINE.test(line));
}
