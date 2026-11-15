#!/usr/bin/env node
// Fails unless @sefaria/design-system is pinned to a release tag (github:Sefaria/design-system#vX.Y.Z).
// A feature branch may point at a design-system branch while that design-system PR is in review,
// but it can't merge that way: main only ever uses released design-system versions.
// Draft preview PRs opened by the design-system bot (branches design-system/preview-*) are exempt;
// they are never merged.
import { readFileSync } from 'node:fs';

const branch = process.env.GITHUB_HEAD_REF || '';
if (branch.startsWith('design-system/preview-')) {
  console.log(`Skipped on ${branch}: design-system preview PRs aren't merged.`);
  process.exit(0);
}

const spec = JSON.parse(readFileSync('package.json', 'utf8')).dependencies?.['@sefaria/design-system'];
if (!/^github:Sefaria\/design-system#v\d+\.\d+\.\d+$/.test(spec || '')) {
  console.error(
    `@sefaria/design-system is "${spec}". Pin it to a release before merging:\n` +
      '  npm install github:Sefaria/design-system#vX.Y.Z',
  );
  process.exit(1);
}
console.log(`@sefaria/design-system is pinned to ${spec.split('#')[1]}.`);
