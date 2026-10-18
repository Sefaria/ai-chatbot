import { defineConfig } from '@playwright/test';

const PORT = 5179;

// Widget behaviour tests. The Vite dev server serves the widget source; each
// test mounts the widget on a page built by e2e/pages/assistantPage.js, and a
// mock chat server answers its API calls (e2e/support/mockChatServer.js).
export default defineConfig({
  testDir: 'e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chrome', use: { browserName: 'chromium' } },
    { name: 'safari', use: { browserName: 'webkit' } },
  ],
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    // The demo page counts as a Sefaria host (it gets the login button, for one),
    // whatever the developer's own .env.local says.
    env: { VITE_SEFARIA_HOSTNAMES: 'localhost,127.0.0.1,::1' },
    url: `http://localhost:${PORT}/src/main.js`,
    reuseExistingServer: !process.env.CI,
  },
});
