import test from 'node:test';
import assert from 'node:assert/strict';
import { hideProfileUsed } from '../src/lib/profileUsed.js';

test('profile used: text without the tag is shown whole', () => {
  assert.equal(hideProfileUsed('An answer. 2 < 3.'), 'An answer. 2 < 3.');
});

test('profile used: everything from the tag on is hidden', () => {
  assert.equal(hideProfileUsed('An answer.<profile_used>From your pro'), 'An answer.');
  assert.equal(hideProfileUsed('An answer.<profile_used>x</profile_used>'), 'An answer.');
});

test('profile used: every trailing prefix of the tag is held back', () => {
  const tag = '<profile_used>';
  for (let i = 1; i < tag.length; i++) {
    assert.equal(hideProfileUsed('An answer.' + tag.slice(0, i)), 'An answer.');
  }
});

test('profile used: a held-back prefix that turns out not to be the tag is released', () => {
  assert.equal(hideProfileUsed('a <pro'), 'a ');
  assert.equal(hideProfileUsed('a <profile> b'), 'a <profile> b');
});

test('profile used: the visible text only ever grows while a tag arrives in pieces', () => {
  const full = 'Answer text.\n\n<profile_used>From your profile, I took X into account.</profile_used>';
  let previous = '';
  for (let end = 0; end <= full.length; end++) {
    const shown = hideProfileUsed(full.slice(0, end));
    assert.ok(shown.startsWith(previous), `${JSON.stringify(shown)} extends ${JSON.stringify(previous)}`);
    assert.ok(!/profile_|<prof/.test(shown));
    previous = shown;
  }
  assert.equal(previous, 'Answer text.\n\n');
});
