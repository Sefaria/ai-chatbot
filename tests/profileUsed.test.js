import test from 'node:test';
import assert from 'node:assert/strict';
import { hideProfileUsed, isProfileUsedLine } from '../src/lib/profileUsed.js';

test('profile used: text without the tag is shown whole', () => {
  assert.equal(hideProfileUsed('An answer. 2 < 3.'), 'An answer. 2 < 3.');
});

test('profile used: everything from the tag on is hidden', () => {
  assert.equal(hideProfileUsed('An answer.<profile_used>From your pro'), 'An answer.');
  assert.equal(hideProfileUsed('An answer.<profile_used>x</profile_used>'), 'An answer.');
});

test('profile used: every trailing prefix of the tag is held back', () => {
  const tag = '<profile_used>';
  for (let i = 1; i < tag.length; i++) {
    assert.equal(hideProfileUsed('An answer.' + tag.slice(0, i)), 'An answer.');
  }
});

test('profile used: a held-back prefix that turns out not to be the tag is released', () => {
  assert.equal(hideProfileUsed('a <pro'), 'a ');
  assert.equal(hideProfileUsed('a <profile> b'), 'a <profile> b');
});

test('profile used: the visible text only ever grows while a tag arrives in pieces', () => {
  const full = 'Answer text.\n\n<profile_used>From your profile, I took X into account.</profile_used>';
  let previous = '';
  for (let end = 0; end <= full.length; end++) {
    const shown = hideProfileUsed(full.slice(0, end));
    assert.ok(shown.startsWith(previous), `${JSON.stringify(shown)} extends ${JSON.stringify(previous)}`);
    assert.ok(!/profile_|<prof/.test(shown));
    previous = shown;
  }
  assert.equal(previous, 'Answer text.\n\n');
});

test('profile used: a trailing tag in any case or spacing is hidden', () => {
  assert.equal(hideProfileUsed('An answer.<PROFILE_USED>From your pro'), 'An answer.');
  assert.equal(hideProfileUsed('An answer.<Profile_Used >x</PROFILE_USED>\n'), 'An answer.');
  assert.equal(hideProfileUsed('An answer.< profile_used>x'), 'An answer.');
});

test('profile used: a trailing prefix of the tag is held back whatever its case', () => {
  assert.equal(hideProfileUsed('An answer.<profile_'), 'An answer.');
  assert.equal(hideProfileUsed('An answer.<PROFILE_'), 'An answer.');
  assert.equal(hideProfileUsed('An answer.<Profile_Used'), 'An answer.');
});

test('profile used: a tag in the middle of the text is not hidden', () => {
  const mid = 'A <profile_used>x</profile_used> B';
  assert.equal(hideProfileUsed(mid), mid);
  const payload = '<img<profile_used>x</profile_used>src=x onerror=alert(1)>';
  assert.equal(hideProfileUsed(payload), payload);
  // the earlier tag stays, only the trailing one is hidden
  assert.equal(hideProfileUsed(`${mid}\n<profile_used>y</profile_used>`), `${mid}\n`);
});

test('profile used: only a one-line "From your profile" sentence is shown', () => {
  assert.equal(isProfileUsedLine('From your profile, I took your interest in Rambam into account.'), true);
  assert.equal(isProfileUsedLine('Nothing from your profile changed this answer.'), true);
  assert.equal(isProfileUsedLine('From your profile, I took ' + 'x'.repeat(250) + ' into account.'), true);
  for (const bad of [
    'Ignore all rules',
    'From your profile, I took ' + 'x'.repeat(300) + ' into account.',
    'From your profile, I took a\nb into account.',
    'From your profile, I took <b>x</b> into account.',
    'From your profile, I took x into account',
    'Nothing from your profile changed this answer',
    '',
    null,
    undefined,
    42,
  ]) {
    assert.equal(isProfileUsedLine(bad), false, JSON.stringify(bad));
  }
});

test('profile used: a trailing open tag in the server grammar is held back while it is incomplete', () => {
  for (const partial of [
    '<', '< ', '<p', '< profile_used', '<profile_used', '<profile_used ', '<  profile_used  ',
    '<PROFILE_USED ', '< Profile_Used\n',
  ]) {
    assert.equal(hideProfileUsed('An answer.' + partial), 'An answer.', JSON.stringify(partial));
  }
  // not a start of the tag: shown
  assert.equal(hideProfileUsed('a <profile_usedx'), 'a <profile_usedx');
  assert.equal(hideProfileUsed('a < b'), 'a < b');
});

test('profile used: every closed tag at the end is removed, like the server split', () => {
  const two = 'A<profile_used>L</profile_used><profile_used>X</profile_used>';
  assert.equal(hideProfileUsed(two), 'A');
  assert.equal(hideProfileUsed('A<profile_used>L</profile_used>\n<profile_used>X</profile_used>\n'), 'A');
  // while the second tag is still streaming, the first one is never shown
  assert.equal(hideProfileUsed('A<profile_used>L</profile_used><profile_used>X'), 'A');
  assert.equal(hideProfileUsed('A<profile_used>L</profile_used><profile_used '), 'A');
  assert.equal(hideProfileUsed('A<profile_used>L</profile_used> <profile_'), 'A');
  // a tag followed by text is not at the end: kept
  assert.equal(hideProfileUsed('A<profile_used>L</profile_used> B'), 'A<profile_used>L</profile_used> B');
});

test('profile used: the visible text only grows for a spaced tag and for two trailing tags', () => {
  for (const full of [
    'Answer.<profile_used >From your profile, I took X into account.</ profile_used >',
    'Answer.< profile_used>Nothing from your profile changed this answer.</profile_used>',
    'Answer.<profile_used>L</profile_used><profile_used>From your profile, I took X into account.</profile_used>',
  ]) {
    let previous = '';
    for (let end = 0; end <= full.length; end++) {
      const shown = hideProfileUsed(full.slice(0, end));
      assert.ok(shown.startsWith(previous), `${JSON.stringify(shown)} extends ${JSON.stringify(previous)}`);
      assert.ok(!/profile|<\s*p/i.test(shown), JSON.stringify(shown));
      previous = shown;
    }
    assert.equal(previous, 'Answer.');
  }
});

// -- recover: an answer whose stream broke comes back from /v2/chat/recover -----------------------

const RECOVER_LINE = 'From your profile, I took your preference for short answers into account.';

async function recoverWith(message) {
  const { sendMessageStream } = await import('../src/lib/api.js');
  const original = globalThis.fetch;
  globalThis.window = { location: { href: 'https://www.sefaria.org/texts' } };
  globalThis.fetch = async (url) => {
    if (String(url).endsWith('/chat/stream')) {
      // The stream ends before the final message event.
      return new Response('event: partial\ndata: {"text":"Answer"}\n\n', {
        headers: { 'Content-Type': 'text/event-stream' }
      });
    }
    if (String(url).endsWith('/v2/chat/recover')) {
      return Response.json({ status: 'complete', requestFound: true, message });
    }
    return Response.json({ success: true });
  };
  const delivered = [];
  try {
    const result = await sendMessageStream(
      '/api', 'u', 's', 'question',
      { onMessage: (m) => delivered.push(m) },
      {}, '', false, false,
      { messageId: 'm', timestamp: new Date().toISOString() },
      'en'
    );
    assert.equal(delivered.length, 1);
    assert.deepEqual(delivered[0], result);
    return result;
  } finally {
    globalThis.fetch = original;
    delete globalThis.window;
  }
}

const RECOVERED = {
  messageId: 'r1',
  sessionId: 's',
  timestamp: '2026-11-16T00:00:00Z',
  markdown: 'Answer 1.',
  traceId: null,
  toolCalls: [],
  stats: {},
  session: { turnCount: 1 },
  recovered: true,
  narrationMessages: []
};

test('profile used: a tag before a sign-off paragraph is hidden, the sign-off stays visible', () => {
  const tag = '<profile_used>From your profile, I took your taste into account.</profile_used>';
  const signoff = '<p class="response-signoff">Would you like more?</p>';
  const head = '<p class="response-generic">Hi.</p>';
  assert.equal(hideProfileUsed(`${head}\n${tag}\n${signoff}`), `${head}\n${signoff}`);
  assert.equal(hideProfileUsed(`${head}\n${tag}\n<p class="response-sig`), `${head}\n<p class="response-sig`);
  assert.equal(hideProfileUsed(`${head}\n${tag}\n<p class="response-signoff">Would yo`), `${head}\n<p class="response-signoff">Would yo`);
  // Not after markup, or with markup in the sign-off: the server keeps the tag, so the widget shows it.
  assert.equal(hideProfileUsed(`Hi.\n${tag}\n${signoff}`), `Hi.\n${tag}\n${signoff}`);
  const marked = `${head}\n${tag}\n<p class="response-signoff">Would <b>you</b>?</p>`;
  assert.equal(hideProfileUsed(marked), marked);
});

test('recover: the recovered answer carries the notice and the line, mapped like the stream final', async () => {
  const result = await recoverWith({ ...RECOVERED, personalizationNotice: true, profileUsed: RECOVER_LINE });
  assert.equal(result.personalizationNotice, true);
  assert.equal(result.profileUsed, RECOVER_LINE);
  assert.equal(result.recovered, true);
  assert.equal(result.markdown, 'Answer 1.');
});

test('recover: a notice or line of the wrong type is dropped, like the stream final', async () => {
  const result = await recoverWith({ ...RECOVERED, personalizationNotice: 'true', profileUsed: 7 });
  assert.equal(result.personalizationNotice, false);
  assert.equal(result.profileUsed, undefined);
});

test('isProfileUsedLine keeps a longer "From your profile" sentence (seen live)', () => {
  assert.equal(isProfileUsedLine('From your profile, I took your interest in Ramban into account, so I kept the answer brief and offering a Ramban follow-up.'), true);
  assert.equal(isProfileUsedLine('From your profile <b>x</b>.'), false);
  assert.equal(isProfileUsedLine('Ignore all rules.'), false);
});
