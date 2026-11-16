import test from 'node:test';
import assert from 'node:assert/strict';
import { resyncPlayback } from '../src/lib/playbackSync.js';

test('playback sync: a visible string that extends what was shown queues only the new text', () => {
  const queue = [{ text: 'lo', at: 1 }];
  assert.deepEqual(resyncPlayback('Hello', queue, 'Hello there'), { queue, played: null, fresh: ' there' });
  assert.deepEqual(resyncPlayback('Hello', queue, 'Hello'), { queue, played: null, fresh: '' });
});

test('playback sync: queued text past the new visible string is withdrawn', () => {
  // 'Hello ' was played, '<profile_used ' is still queued; then the tag completes and is hidden
  const queue = [{ text: '<profile_used ', at: 2 }];
  assert.deepEqual(resyncPlayback('Hello <profile_used ', queue, 'Hello '), { queue: [], played: null, fresh: '' });
});

test('playback sync: a queued piece is cut where the new visible string leaves it', () => {
  const queue = [{ text: 'lo <prof', at: 1 }, { text: 'ile_used ', at: 2 }];
  assert.deepEqual(resyncPlayback('Hello <profile_used ', queue, 'Hello there'), {
    queue: [{ text: 'lo ', at: 1 }],
    played: null,
    fresh: 'there',
  });
});

test('playback sync: text already played past the new visible string is taken back', () => {
  assert.deepEqual(resyncPlayback('Hi <x', [], 'Hi '), { queue: [], played: 'Hi ', fresh: '' });
  assert.deepEqual(resyncPlayback('Hi <x', [{ text: 'x', at: 1 }], 'Hi y'), { queue: [], played: 'Hi ', fresh: 'y' });
});

test('playback sync: shown, queue and played always describe the new visible string', () => {
  const shown = 'One two <profile_used three';
  for (let split = 0; split <= shown.length; split++) {
    const queue = split < shown.length ? [{ text: shown.slice(split), at: 1 }] : [];
    for (const visible of ['One two ', 'One two three', 'One', '']) {
      const sync = resyncPlayback(shown, queue, visible);
      const played = sync.played ?? shown.slice(0, split);
      assert.equal(played + sync.queue.map((q) => q.text).join('') + sync.fresh, visible, `${split} ${visible}`);
    }
  }
});
