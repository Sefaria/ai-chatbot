import test from 'node:test';
import assert from 'node:assert/strict';
import {isHanukkah} from '../src/lib/hanukkah.js';

function withPage(search, stored, fn) {
  const store = new Map(stored ? [['lc-hanukkah', stored]] : []);
  globalThis.window = {location: {search}, localStorage: {getItem: key => store.get(key) ?? null}};
  try {
    fn();
  } finally {
    delete globalThis.window;
  }
}

test('window opens 17:00 local on 4 Dec 2026 and closes at the end of 12 Dec', () => {
  withPage('', null, () => {
    assert.equal(isHanukkah(new Date(2026, 11, 4, 16, 59)), false);
    assert.equal(isHanukkah(new Date(2026, 11, 4, 17, 0)), true);
    assert.equal(isHanukkah(new Date(2026, 11, 12, 23, 59)), true);
    assert.equal(isHanukkah(new Date(2026, 11, 13, 0, 0)), false);
  });
});

test('query param and localStorage force the window on or off', () => {
  const outside = new Date(2026, 10, 16);
  const inside = new Date(2026, 11, 6);
  withPage('?hanukkah=1', null, () => assert.equal(isHanukkah(outside), true));
  withPage('', '1', () => assert.equal(isHanukkah(outside), true));
  withPage('?hanukkah=0', '1', () => assert.equal(isHanukkah(inside), false));
});

test('works with no window at all', () => {
  assert.equal(isHanukkah(new Date(2026, 11, 6)), true);
});

test('the Hanukkah string keys the code uses exist in both locales (from Weblate)', async () => {
  const {readFile} = await import('node:fs/promises');
  const load = async (lang) => JSON.parse(await readFile(new URL(`../src/i18n/locales/${lang}.json`, import.meta.url)));
  const expected = [
    'assistant.loading.hanukkah.initial',
    ...[1, 2, 3, 4, 5].map((n) => `assistant.thinking.array.hanukkah.0${n}`),
    'assistant.thinking.hanukkah.final',
  ];
  for (const lang of ['en', 'he']) {
    const messages = await load(lang);
    for (const key of expected) assert.ok(messages[key], `${lang}.json is missing ${key}`);
  }
});
