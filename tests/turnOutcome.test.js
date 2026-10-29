import test from 'node:test';
import assert from 'node:assert/strict';
import {waitForTurn} from '../src/lib/turnOutcome.js';

test('confirmed stop releases a silent stream; its late result cannot settle a new turn', async () => {
  const controller = new AbortController();
  let finishOld;
  const pending = waitForTurn(new Promise(resolve => {finishOld = resolve;}), controller.signal);
  const reason = Object.assign(new Error('Stopped'), {code: 'stream_cancelled'});
  controller.abort(reason);
  await assert.rejects(pending, error => error === reason);
  const next = waitForTurn(Promise.resolve('new answer'), new AbortController().signal);
  finishOld('old answer');
  assert.equal(await next, 'new answer');
});

test('without confirmed stop, a completed answer is preserved', async () => {
  assert.equal(await waitForTurn(Promise.resolve('answer'), new AbortController().signal), 'answer');
});
