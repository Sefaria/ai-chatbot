import { defineConfig } from '@playwright/test';

// LIVE run: real Sefaria site, real chatbot server, real model. Not part of `npx playwright test`
// (that config's testDir is e2e/ but joe-story.live.spec.js skips itself unless LIVE=1).
// Run: LIVE=1 npx playwright test -c e2e/live/playwright.live.config.js
export default defineConfig({
  testDir: '.',
  testMatch: /.*\.live\.spec\.js/,
  timeout: 70 * 60 * 1000,
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: 'list',
  use: {
    browserName: 'chromium',
    viewport: { width: 1280, height: 900 },
    trace: 'off',
    actionTimeout: 60_000,
    navigationTimeout: 90_000,
  },
});
