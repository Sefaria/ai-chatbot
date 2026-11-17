import assert from 'node:assert/strict';
import { test } from 'node:test';

import { personalizationContext } from './personalization.js';

function memoryStorage() {
  const data = new Map();
  return {
    getItem: key => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: key => data.delete(key),
    size: () => data.size
  };
}

test('nothing is sent when nothing is set', () => {
  const storage = memoryStorage();
  assert.deepEqual(personalizationContext('', storage), {});
  assert.deepEqual(personalizationContext('?other=1', storage), {});
  assert.equal(storage.size(), 0);
});

test('parameters are returned and stored', () => {
  const storage = memoryStorage();
  assert.deepEqual(personalizationContext('?la_personalization=hybrid&la_injection=tool', storage), {
    personalizationMode: 'hybrid',
    personalizationInjection: 'tool'
  });
  assert.equal(storage.getItem('la_personalization'), 'hybrid');
});

test('stored values persist on later page loads', () => {
  const storage = memoryStorage();
  personalizationContext('?la_personalization=realtime', storage);
  assert.deepEqual(personalizationContext('', storage), { personalizationMode: 'realtime' });
  personalizationContext('?la_injection=router_gate', storage);
  assert.deepEqual(personalizationContext('', storage), {
    personalizationMode: 'realtime',
    personalizationInjection: 'router_gate'
  });
});

test('clear removes both', () => {
  const storage = memoryStorage();
  personalizationContext('?la_personalization=realtime&la_injection=tool', storage);
  assert.deepEqual(personalizationContext('?la_personalization=clear', storage), {});
  assert.equal(storage.size(), 0);
  assert.deepEqual(personalizationContext('', storage), {});
});

test('works without storage', () => {
  assert.deepEqual(personalizationContext('?la_personalization=nightly', null), {
    personalizationMode: 'nightly'
  });
});
