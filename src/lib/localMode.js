/**
 * Local mode: running the agent against a runner on the user's own machine.
 *
 * See docs/plans/2026-09-08-local-agent-runner.md. The runner serves the same
 * API surface as our server, so switching to it is a base-URL change plus the
 * bearer token pairing produced.
 *
 * Local mode is an optimization, never a dependency: every failure here falls
 * back to the server silently.
 */

const DEFAULT_PORT = 8899;
const TOKEN_KEY = 'sefaria-runner-token';
const PROBE_TIMEOUT_MS = 1500;

export function runnerBaseUrl(port = DEFAULT_PORT) {
  return `http://127.0.0.1:${port}`;
}

/** Read the stored runner token. Storage can throw in private windows. */
export function storedToken() {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function storeToken(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // A viewer with site data blocked simply pairs again next time.
  }
}

export function forgetToken() {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // Nothing to do — the token was never persisted.
  }
}

/**
 * Ask whether a runner is listening, and whether it is already paired.
 *
 * Short timeout on purpose: this runs on load, and a machine with no runner
 * must not delay the chat opening.
 */
export async function probe(port = DEFAULT_PORT) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const response = await fetch(`${runnerBaseUrl(port)}/health`, {
      signal: controller.signal
    });
    if (!response.ok) return { available: false, paired: false };
    const body = await response.json();
    return { available: body.status === 'ok', paired: Boolean(body.paired) };
  } catch {
    // No runner, blocked by the browser, or too slow — all mean "use the server".
    return { available: false, paired: false };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Exchange the code shown in the runner's terminal for a bearer token.
 *
 * Returns { ok: true } or { ok: false, error } — the caller shows the reason,
 * since "wrong code" and "cannot reach Sefaria" need different fixes.
 */
export async function pair(code, userId, port = DEFAULT_PORT) {
  let response;
  try {
    response = await fetch(`${runnerBaseUrl(port)}/pair`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, userId })
    });
  } catch {
    return { ok: false, error: 'Could not reach the runner. Is it still running?' };
  }

  let body = {};
  try {
    body = await response.json();
  } catch {
    return { ok: false, error: 'The runner sent an unexpected response.' };
  }

  if (response.ok && body.runnerToken) {
    storeToken(body.runnerToken);
    return { ok: true };
  }

  if (body.error === 'invalid_code') {
    return { ok: false, error: 'That code is not right. Check the runner’s terminal.' };
  }
  if (body.error === 'pairing_closed') {
    return { ok: false, error: 'That code has expired or been used. Restart the runner for a new one.' };
  }
  if (body.error === 'identity_unresolved') {
    return { ok: false, error: 'The runner could not reach Sefaria to confirm your account.' };
  }
  return { ok: false, error: 'Pairing failed. Restart the runner and try again.' };
}

/**
 * Decide which API the widget should talk to.
 *
 * Returns the server base URL unless a runner is present, paired, and we hold
 * its token.
 */
export async function resolveTarget(serverBaseUrl, port = DEFAULT_PORT) {
  const token = storedToken();
  if (!token) return { baseUrl: serverBaseUrl, local: false };

  const { available, paired } = await probe(port);
  if (!available || !paired) return { baseUrl: serverBaseUrl, local: false };

  return {
    baseUrl: runnerBaseUrl(port),
    local: true,
    headers: { Authorization: `Bearer ${token}` }
  };
}
