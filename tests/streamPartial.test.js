import test from 'node:test';
import assert from 'node:assert/strict';
import { sendMessageStream } from '../src/lib/api.js';

function sseResponse(body) {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(body));
        controller.close();
      }
    }),
    { headers: { 'Content-Type': 'text/event-stream' } }
  );
}

test('onPartial receives streamed content_delta text via the partial SSE event', async () => {
  const original = globalThis.fetch;
  globalThis.window = { location: { href: 'https://www.sefaria.org/texts' } };
  const sse =
    'event: partial\ndata: {"text":"Hello "}\n\n' +
    'event: partial\ndata: {"text":"world"}\n\n' +
    'event: progress\ndata: {"type":"content_done","text":null}\n\n' +
    'event: message\ndata: {"messageId":"m1","sessionId":"s","timestamp":"2024-01-01T00:00:00Z","markdown":"Hello world"}\n\n';
  globalThis.fetch = async () => sseResponse(sse);

  const partials = [];
  const progressTypes = [];
  try {
    const result = await sendMessageStream(
      '/api', 'u', 's', 'question',
      {
        onPartial: (text) => partials.push(text),
        onProgress: (progress) => progressTypes.push(progress.type)
      },
      {}, '', false, false,
      { messageId: 'm', timestamp: new Date().toISOString() },
      'en'
    );
    assert.deepEqual(partials, ['Hello ', 'world']);
    assert.deepEqual(progressTypes, ['content_done']);
    assert.equal(result.markdown, 'Hello world');
  } finally {
    globalThis.fetch = original;
    delete globalThis.window;
  }
});
