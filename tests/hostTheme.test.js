import test from 'node:test';
import assert from 'node:assert/strict';
import { followHostTheme, readHostTheme } from '../src/lib/hostTheme.js';

// Node has no DOM: a stand-in <html> element and MutationObserver that deliver attribute
// records the way a browser does (asynchronously, filtered by attributeFilter, until disconnect).
class FakeMutationObserver {
  static active = new Set();
  constructor(callback) { this.callback = callback; }
  observe(target, options) {
    this.target = target;
    this.options = options;
    FakeMutationObserver.active.add(this);
  }
  disconnect() { FakeMutationObserver.active.delete(this); }
}

function fakeRoot(theme) {
  const attributes = new Map(theme === undefined ? [] : [['data-theme', theme]]);
  const root = {
    getAttribute: (name) => (attributes.has(name) ? attributes.get(name) : null),
    setAttribute(name, value) { attributes.set(name, String(value)); notify(name); },
    removeAttribute(name) { attributes.delete(name); notify(name); },
  };
  function notify(name) {
    for (const observer of FakeMutationObserver.active) {
      const { attributes: watchesAttributes, attributeFilter } = observer.options;
      if (observer.target !== root || !watchesAttributes || (attributeFilter && !attributeFilter.includes(name))) continue;
      queueMicrotask(() => observer.callback([{ type: 'attributes', attributeName: name, target: root }], observer));
    }
  }
  return root;
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

test.beforeEach(() => {
  FakeMutationObserver.active.clear();
  globalThis.MutationObserver = FakeMutationObserver;
});
test.after(() => { delete globalThis.MutationObserver; });

test('dark only when <html data-theme> is exactly "dark"', () => {
  assert.equal(readHostTheme(fakeRoot('dark')), 'dark');
  assert.equal(readHostTheme(fakeRoot('light')), 'light');
  assert.equal(readHostTheme(fakeRoot(undefined)), 'light');
  assert.equal(readHostTheme(fakeRoot('white')), 'light'); // e.g. reCAPTCHA's value
  assert.equal(readHostTheme(fakeRoot('Dark')), 'light');
  assert.equal(readHostTheme(null), 'light');
});

test('starts dark on a dark page', () => {
  const seen = [];
  followHostTheme((theme) => seen.push(theme), fakeRoot('dark'));
  assert.deepEqual(seen, ['dark']);
});

test('starts light on a light page, and while data-theme is not set yet', () => {
  for (const initial of ['light', undefined]) {
    const seen = [];
    followHostTheme((theme) => seen.push(theme), fakeRoot(initial));
    assert.deepEqual(seen, ['light']);
  }
});

test('follows the page from light to dark and back without a reload', async () => {
  const root = fakeRoot(undefined);
  const seen = [];
  followHostTheme((theme) => seen.push(theme), root);
  root.setAttribute('data-theme', 'dark');
  await flush();
  root.setAttribute('data-theme', 'light');
  await flush();
  root.setAttribute('data-theme', 'dark');
  await flush();
  root.removeAttribute('data-theme');
  await flush();
  assert.deepEqual(seen, ['light', 'dark', 'light', 'dark', 'light']);
});

test('watches only data-theme on <html>, and reports only real changes', async () => {
  const root = fakeRoot('light');
  const seen = [];
  followHostTheme((theme) => seen.push(theme), root);
  const [observer] = FakeMutationObserver.active;
  assert.equal(observer.target, root);
  assert.deepEqual(observer.options, { attributes: true, attributeFilter: ['data-theme'] });
  root.setAttribute('lang', 'he');
  root.setAttribute('data-theme', 'light');
  root.setAttribute('data-theme', 'sepia');
  await flush();
  assert.deepEqual(seen, ['light']);
});

test('stops following once disconnected', async () => {
  const root = fakeRoot('light');
  const seen = [];
  const stop = followHostTheme((theme) => seen.push(theme), root);
  stop();
  assert.equal(FakeMutationObserver.active.size, 0);
  root.setAttribute('data-theme', 'dark');
  await flush();
  assert.deepEqual(seen, ['light']);
});

test('without a document it does nothing', () => {
  const seen = [];
  const stop = followHostTheme((theme) => seen.push(theme));
  assert.equal(typeof stop, 'function');
  stop();
  assert.deepEqual(seen, []);
});
