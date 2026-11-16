// The answering model ends a personalized lab answer with `<profile_used>…</profile_used>`.
// While the answer streams, the widget hides what the server will take off the end: every closed
// tag at the end (in a loop, like the server's split), an opening tag still waiting for its close,
// and a trailing piece of an opening tag that may still become one. A closed tag right before the
// closing sign-off paragraph (after HTML) is hidden too, and the sign-off text stays visible. The grammar is the server's
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
// The core format's closing sign-off paragraph, as the server accepts it after a tag
// (`<p class="response-signoff">` with plain or curly quotes, inner text without markup).
const SIGNOFF_OPENER = '<p class="response-signoff">';
const SIGNOFF_REST = /^[^<]*(?:<|<\/|<\/p|<\/p>\s*)?$/i;
const TAG_WITH_REST = new RegExp(
  String.raw`${OPEN_SRC}((?:(?!${OPEN_SRC}|${CLOSE_SRC})[\s\S])*)${CLOSE_SRC}(\s*)`,
  'gi'
);

/** True when the text is a start (or the whole) of one sign-off paragraph, nothing after it but whitespace. */
function isSignoffPrefix(rest) {
  if (!rest) return false;
  for (let i = 0; i < Math.min(rest.length, SIGNOFF_OPENER.length); i += 1) {
    const expected = SIGNOFF_OPENER[i];
    const ok = expected === '"' ? /["\u201c\u201d]/.test(rest[i]) : rest[i].toLowerCase() === expected;
    if (!ok) return false;
  }
  return rest.length <= SIGNOFF_OPENER.length || SIGNOFF_REST.test(rest.slice(SIGNOFF_OPENER.length));
}

/** The text with a closed tag taken out of between HTML and a (possibly incomplete) sign-off paragraph, or null. */
function cutBeforeSignoff(text) {
  let found = null;
  for (const match of text.matchAll(TAG_WITH_REST)) {
    const rest = text.slice(match.index + match[0].length);
    const before = text.slice(0, match.index).trimEnd();
    if (before.endsWith('>') && isSignoffPrefix(rest)) found = { match, before, rest };
  }
  return found && `${found.before}\n${found.rest}`;
}
// Same rule as the server: one sentence starting "From your profile" or "Nothing from your profile", at most 300 characters.
const LINE = /^(?:From|Nothing from) your profile[^\r\n<>]{1,280}\.$/;

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
    const beforeSignoff = cutBeforeSignoff(shown);
    if (beforeSignoff !== null) return beforeSignoff;
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
  return typeof line === 'string' && line.length <= 300 && LINE.test(line);
}
