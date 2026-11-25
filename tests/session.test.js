import test from 'node:test';
import assert from 'node:assert/strict';

const data = new Map();
globalThis.localStorage = {
  getItem: (k) => (data.has(k) ? data.get(k) : null),
  setItem: (k, v) => data.set(k, String(v)),
  removeItem: (k) => data.delete(k),
  key: (i) => [...data.keys()][i] ?? null,
  get length() { return data.size; }
};
const { clearLoggedInChatWhenLoggedOut } = await import('../src/lib/session.js');
const { setStorage, getStorage, STORAGE_KEYS } = await import('../src/lib/storage.js');

test('logging out clears the dismissed personalization toast with the logged-in chat', () => {
  data.clear();
  setStorage(STORAGE_KEYS.SESSION, { sessionId: 's', loggedIn: true });
  setStorage(STORAGE_KEYS.HIDE_LAUNCHER, true);
  setStorage(STORAGE_KEYS.PERSONALIZATION_TOAST_DISMISSED, true);
  clearLoggedInChatWhenLoggedOut(false);
  assert.equal(getStorage(STORAGE_KEYS.PERSONALIZATION_TOAST_DISMISSED), null);
  assert.equal(getStorage(STORAGE_KEYS.HIDE_LAUNCHER), null);
});

test('a logged-in visitor keeps the dismissed toast', () => {
  data.clear();
  setStorage(STORAGE_KEYS.SESSION, { sessionId: 's', loggedIn: true });
  setStorage(STORAGE_KEYS.PERSONALIZATION_TOAST_DISMISSED, true);
  clearLoggedInChatWhenLoggedOut(true);
  assert.equal(getStorage(STORAGE_KEYS.PERSONALIZATION_TOAST_DISMISSED), true);
});
