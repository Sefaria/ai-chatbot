// Mock chatbot backend for streaming-UX demos. No API keys needed.
// Serves the endpoints the widget calls and streams a canned answer as
// `partial` SSE events at a model-like pace.
//
//   node dev/mock-stream-server.mjs            # port 8001 (vite proxies /api here)
//   MOCK_TOKENS_PER_SEC=30 node dev/mock-stream-server.mjs
//   MOCK_STALLS=0 node dev/mock-stream-server.mjs   # no stalls

import http from 'node:http';

const PORT = Number(process.env.PORT || 8001);
const TOKENS_PER_SEC = Number(process.env.MOCK_TOKENS_PER_SEC || 60);
const STALLS = process.env.MOCK_STALLS !== '0';

// Stalls like our backend's, randomized in both spacing and length: one every
// 0.3–1.5s of streaming, lasting 0.5–5s (mostly short, occasionally long).
const nextStallGapMs = () => 300 + Math.random() * 1200;
const stallLengthMs = () => 500 + Math.random() ** 2 * 4500;

const ANSWER = `## The Shema

The Shema is the central declaration of Jewish faith, recited morning and evening. It opens with [Deuteronomy 6:4](https://www.sefaria.org/Deuteronomy.6.4): **"Hear, O Israel: the LORD is our God, the LORD is one."**

The full recitation joins three biblical passages:

1. **[Deuteronomy 6:4–9](https://www.sefaria.org/Deuteronomy.6.4-9)** — loving God with all your heart, and teaching these words to your children.
2. **[Deuteronomy 11:13–21](https://www.sefaria.org/Deuteronomy.11.13-21)** — the link between keeping the commandments and the land's blessing.
3. **[Numbers 15:37–41](https://www.sefaria.org/Numbers.15.37-41)** — the commandment of tzitzit, and remembering the Exodus.

The Mishnah opens with a question about exactly this practice. [Mishnah Berakhot 1:1](https://www.sefaria.org/Mishnah_Berakhot.1.1) asks: from what time may one recite the evening Shema? The answer sets the rhythm of daily prayer that follows.

Rashi, commenting on [Deuteronomy 6:4](https://www.sefaria.org/Rashi_on_Deuteronomy.6.4), reads the verse as a statement about the future. The LORD who is now "our God" will one day be recognized as the one God by all the nations.

Would you like to explore the commentaries on the first verse, or look at how the Shema is placed in the prayer service?`;

// Split into model-sized tokens (2–5 chars) that break mid-word, like real output.
function tokenize(text) {
  const tokens = [];
  for (let i = 0; i < text.length; ) {
    const n = 2 + Math.floor(Math.random() * 4);
    tokens.push(text.slice(i, i + n));
    i += n;
  }
  return tokens;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function sendJson(res, body) {
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (c) => (data += c));
    req.on('end', () => {
      try { resolve(JSON.parse(data || '{}')); } catch { resolve({}); }
    });
  });
}

async function streamChat(req, res) {
  const body = await readBody(req);
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache', Connection: 'keep-alive' });
  const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

  send('progress', { type: 'status', text: 'Thinking...' });
  await sleep(1200);
  send('progress', { type: 'tool_start', text: null, toolName: 'search', description: 'Searching the library' });
  await sleep(1500);
  send('progress', { type: 'tool_end', text: null, toolName: 'search', isError: false });
  await sleep(600);
  send('progress', { type: 'status', text: 'Synthesizing response...' });
  await sleep(800);

  // Tokens arrive in uneven bursts, like a real network stream.
  const tokens = tokenize(ANSWER);
  let nextStallAt = Date.now() + nextStallGapMs();
  for (let i = 0; i < tokens.length && !res.destroyed; ) {
    let burst = 1 + Math.floor(Math.random() * 4);
    let waitMs = (burst * 1000) / TOKENS_PER_SEC;
    if (STALLS && Date.now() >= nextStallAt) {
      // Our server stalls while the model keeps generating, so the backlog
      // lands all at once when it recovers.
      waitMs = stallLengthMs();
      burst = Math.round((waitMs / 1000) * TOKENS_PER_SEC);
      nextStallAt = Date.now() + waitMs + nextStallGapMs();
    }
    await sleep(waitMs);
    send('partial', { type: 'message_delta', text: tokens.slice(i, i + burst).join('') });
    i += burst;
  }

  send('message', {
    messageId: `mock-${Date.now()}`,
    sessionId: body.sessionId || 'mock-session',
    timestamp: new Date().toISOString(),
    markdown: ANSWER,
    traceId: null,
    toolCalls: [],
    stats: {},
    session: { turnCount: 1 }
  });
  res.end();
}

http.createServer((req, res) => {
  const path = req.url.split('?')[0].replace(/^\/api/, '');
  if (req.method === 'POST' && path === '/chat/stream') return streamChat(req, res);
  if (path === '/history') return sendJson(res, { messages: [], hasMore: false, session: null });
  if (path === '/v2/prompts/defaults') return sendJson(res, { corePromptSlug: 'mock', labs: false });
  if (path === '/v2/chat/recover') return sendJson(res, { status: 'missing' });
  return sendJson(res, { success: true });
}).listen(PORT, () => console.log(`Mock stream server on http://localhost:${PORT}`));
