import test from 'node:test';
import assert from 'node:assert/strict';
import { createListener } from '../src/lib/listening.js';

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check) {
  for (let i = 0; i < 100; i++) { if (check()) return; await pause(5); }
  assert.fail('Timed out waiting for listener');
}

function setup(fetcher) {
  const original = { window: globalThis.window, fetch: globalThis.fetch };
  let recognition;
  class Recognition {
    constructor() { recognition = this; }
    start() {}
    abort() { this.onend?.(); }
  }
  globalThis.window = { SpeechRecognition: Recognition, location: { href: 'https://www.sefaria.org/Berakhot.2a' } };
  const calls = [];
  globalThis.fetch = async (url, options) => {
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ url, body });
    if (url.endsWith('/sessions')) return Response.json({ session_id: 's' });
    if (url.endsWith('/stop')) return Response.json({ status: 'closed' });
    if (url.includes('/suggestions/')) return Response.json({ status: body.status });
    return fetcher(body, options);
  };
  const states = [], cards = [];
  const listener = createListener({ baseUrl: '/api', userId: 'token', language: 'en-US', intervalMs: 5,
    onState: value => states.push(value), onSuggestions: value => cards.push(value) });
  return { listener, calls, states, cards,
    speak(text) { recognition.onresult({ resultIndex: 0, results: [Object.assign([{ transcript: text }], { isFinal: true })] }); },
    end() { recognition.onend(); },
    async restore() { await listener.stop(); globalThis.window = original.window; globalThis.fetch = original.fetch; }
  };
}

test('silence sends no analysis; speech retries the identical request; stop clears capture', async () => {
  let attempts = 0;
  const env = setup(body => {
    attempts++;
    if (attempts === 1) return new Response('{}', { status: 503 });
    return Response.json({ received_through_seq: 1, analyzed_through_seq: 1, suggestions: [] });
  });
  try {
    await env.listener.start();
    await pause(15);
    assert.equal(attempts, 0);
    env.speak('Why is this different?');
    await until(() => attempts === 2);
    const analyses = env.calls.filter(c => c.url.endsWith('/analyze'));
    assert.deepEqual(analyses[0].body, analyses[1].body);
    assert.equal(analyses[0].body.segments[0].page_url, 'https://www.sefaria.org/Berakhot.2a');
    await env.listener.stop();
    await pause(20);
    assert.equal(attempts, 2);
    assert.equal(env.states.at(-1), 'idle');
  } finally { await env.restore(); }
});

test('a model response arriving after stop never publishes a card', async () => {
  let finish, started = false;
  const env = setup(() => new Promise(resolve => { started = true; finish = resolve; }));
  try {
    await env.listener.start(); env.speak('Why?');
    await until(() => started);
    await env.listener.stop();
    finish(Response.json({ received_through_seq: 1, analyzed_through_seq: 1, suggestions: [{ id: 'q', question: 'Why?', status: 'pending' }] }));
    await pause(15);
    assert.equal(env.cards.some(cards => cards.length), false);
  } finally { await env.restore(); }
});

test('browser ending capture leaves the active state and closes the session', async () => {
  const env = setup(() => { throw new Error('Unexpected analysis'); });
  try {
    await env.listener.start(); env.end();
    await until(() => env.calls.some(c => c.url.endsWith('/stop')));
    assert.equal(env.states.at(-1), 'ended');
  } finally { await env.restore(); }
});

test('acknowledged backlog continues to be analyzed during silence', async () => {
  let analyses = 0;
  const env = setup(() => {
    analyses++;
    return Response.json({ received_through_seq: 1, analyzed_through_seq: analyses === 1 ? 0 : 1, suggestions: [] });
  });
  try {
    await env.listener.start(); env.speak('Why?');
    await until(() => analyses === 2);
    assert.deepEqual(env.calls.filter(c => c.url.endsWith('/analyze'))[1].body.segments, []);
  } finally { await env.restore(); }
});
