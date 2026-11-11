/** Request-based ambient listening. Transcripts stay out of chat/localStorage. */
export function speechRecognitionSupported() {
  return typeof window !== 'undefined' && !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

export async function listeningRequest(baseUrl, userId, path = '', body, signal, method = 'POST') {
  const response = await fetch(`${baseUrl}/v2/listening/sessions${path}`, {
    method, headers: { 'Content-Type': 'application/json' }, signal,
    ...(method !== 'GET' ? { body: JSON.stringify({ userId, ...body }) } : {})
  });
  if (!response.ok) {
    const error = new Error(`Listening request failed: ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export function createListener({ baseUrl, userId, language, onState, onSuggestions,
  getPageUrl = () => window.location.href, intervalMs = 5000 }) {
  let recognition, sessionId, running = false, timer, sequence = 0;
  let segments = [], pendingRequest = null, inFlight = null, failures = 0;
  let needsAnalysis = false;
  const hidden = new Set();
  let suggestions = [];
  const emitSuggestions = () => onSuggestions(suggestions.filter(s => s.status === 'pending' && !hidden.has(s.id)));

  async function stop({ error = '' } = {}) {
    running = false;
    clearTimeout(timer);
    inFlight?.abort();
    recognition?.abort();
    recognition = null;
    segments = [];
    pendingRequest = null;
    suggestions = [];
    emitSuggestions();
    onState(error || 'idle');
    const id = sessionId;
    sessionId = null;
    if (id) {
      // Independent from the aborted analysis transport; server invalidates its lease.
      try { await listeningRequest(baseUrl, userId, `/${id}/stop`, {}, AbortSignal.timeout(5000)); }
      catch { /* Expiry/cleanup also invalidates and removes abandoned sessions. */ }
    }
  }

  async function flush() {
    if (!running || inFlight) return;
    if (!pendingRequest && !segments.length && !needsAnalysis) {
      timer = setTimeout(flush, intervalMs);
      return;
    }
    pendingRequest ||= { request_id: crypto.randomUUID(), segments: segments.slice(0, 30) };
    const controller = new AbortController();
    inFlight = controller;
    const deadline = setTimeout(() => controller.abort(), 35000);
    try {
      const result = await listeningRequest(baseUrl, userId, `/${sessionId}/analyze`, pendingRequest, controller.signal);
      if (!running) return;
      segments = segments.filter(s => s.seq > result.received_through_seq);
      needsAnalysis = result.received_through_seq > result.analyzed_through_seq;
      pendingRequest = null;
      failures = 0;
      suggestions = result.suggestions || [];
      emitSuggestions();
      onState('listening');
    } catch (error) {
      if (!running) return;
      if ([400, 401, 403, 404, 410, 413].includes(error.status) || ++failures >= 5) {
        await stop({ error: error.status === 410 ? 'expired' : 'error' });
        return;
      }
      onState('reconnecting');
    } finally {
      clearTimeout(deadline);
      if (inFlight === controller) inFlight = null;
    }
    if (running) timer = setTimeout(flush, intervalMs * Math.min(4, 2 ** failures));
  }

  async function start() {
    if (running || sessionId) return;
    running = true;
    onState('starting');
    // Start recognition directly in the click gesture, before awaiting the server.
    const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new Recognition();
    recognition.lang = language;
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.onresult = event => {
      if (!running) return;
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (!event.results[i].isFinal) continue;
        const text = event.results[i][0].transcript.trim();
        if (!text) continue;
        // Break large finalized browser results into API-sized segments.
        for (let offset = 0; offset < text.length; offset += 2000) {
          segments.push({ seq: ++sequence, text: text.slice(offset, offset + 2000), page_url: getPageUrl() });
        }
      }
      if (segments.reduce((n, s) => n + s.text.length, 0) > 18000) void stop({ error: 'bufferFull' });
    };
    recognition.onerror = event => {
      if (running) void stop({ error: event.error === 'not-allowed' ? 'permission' : 'error' });
    };
    // Don't display an active microphone after the browser has stopped capturing.
    recognition.onend = () => { if (running) void stop({ error: 'ended' }); };
    try {
      recognition.start();
      const result = await listeningRequest(baseUrl, userId, '', {}, AbortSignal.timeout(10000));
      if (!running) {
        await listeningRequest(baseUrl, userId, `/${result.session_id}/stop`, {}, AbortSignal.timeout(5000));
        return;
      }
      sessionId = result.session_id;
      onState('listening');
      timer = setTimeout(flush, intervalMs);
    } catch {
      if (running) await stop({ error: 'error' });
    }
  }

  function dismiss(suggestion, status = 'dismissed') {
    hidden.add(suggestion.id);
    emitSuggestions();
    if (sessionId) void listeningRequest(baseUrl, userId,
      `/${sessionId}/suggestions/${suggestion.id}`, { status }, AbortSignal.timeout(5000), 'PATCH'
    ).catch(() => {});
    // Recent questions remain in server context even if this status update races analysis.
  }

  return { start, stop, dismiss };
}
