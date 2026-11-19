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
