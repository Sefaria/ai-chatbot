import test from 'node:test';
import assert from 'node:assert/strict';
import {nudgeKeyframes} from '../src/lib/nudge.js';

const peak = (frames) => frames[1].transform;

test('floating panel hops up and settles', () => {
  const frames = nudgeKeyframes('floating');
  assert.equal(peak(frames), 'translateY(-16px)');
  assert.equal(frames.at(-1).transform, 'translateY(0px)');
});

test('docked panel hops toward the page in either direction', () => {
  assert.equal(peak(nudgeKeyframes('docked')), 'translateX(-16px)');
  assert.equal(peak(nudgeKeyframes('docked', true)), 'translateX(16px)');
});
