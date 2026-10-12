import test from 'node:test';
import assert from 'node:assert/strict';
import { withStoppedTurns } from '../src/lib/stoppedTurns.js';

const topics = {topics: [{topicTitle: 'Shabbat', topicSlug: 'shabbat'}]};
const stopped = {messageId:'one', sessionId:'chat-a', role:'user', content:'Question', processingState:'cancelled', appetizerData: topics};
test('reopening a stopped conversation restores its notice and topics', () => {
  const result = withStoppedTurns([stopped]);
  assert.equal(result[1].showNotice, true);
  assert.deepEqual(result[1].appetizerData, topics);
  assert.equal(result[1].sessionId, 'chat-a');
});
test('the notice stays on the canvas after a new prompt', () => {
  const result = withStoppedTurns([...withStoppedTurns([stopped]), {messageId:'two', role:'user', content:'Next'}]);
  assert.equal(result[0].content, 'Question');
  assert.equal(result[1].showNotice, true);
  assert.deepEqual(result[1].appetizerData, topics);
  assert.deepEqual(withStoppedTurns(result), result, 'cached notices are not resurrected');
});
test('completion has no stopped notice', () => {
  assert.equal(withStoppedTurns([{...stopped, processingState:'completed'}]).length, 1);
});
