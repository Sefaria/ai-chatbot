/**
 * Design-system tokens in the widget.
 *
 * The widget bundles Sefaria/design-system's CSS. A host outside production may pass a
 * design-system branch or commit in `design-system-preview`; the widget then loads that version's
 * CSS from GitHub on top. GitHub is mocked here.
 */
import { test, expect } from '@playwright/test';
import { AssistantPage } from './pages/assistantPage.js';

const PREVIEW_FILES = 'https://raw.githubusercontent.com/Sefaria/design-system/**';

/** A token's value on the widget's host element, where `:host` declares it. */
function token(page, name) {
  return page.evaluate((n) => getComputedStyle(document.querySelector('lc-chatbot')).getPropertyValue(n).trim(), name);
}

/** Answer requests for design-system files with `cssFor(path)`; returns the requested paths. */
async function mockPreviewFiles(page, cssFor) {
  const requested = [];
  await page.route(PREVIEW_FILES, (route) => {
    const path = new URL(route.request().url()).pathname;
    requested.push(path);
    route.fulfill({ status: 200, contentType: 'text/plain', body: cssFor(path) });
  });
  return requested;
}

test('the bundled design-system tokens apply', async ({ page }) => {
  await new AssistantPage(page).goto();
  expect(await token(page, '--brand-sefaria-blue')).toBe('#18345D');
});

test('a preview loads that version of the design system on top of the bundled one', async ({ page }) => {
  const requested = await mockPreviewFiles(page, (path) =>
    path.endsWith('/core.css') ? ':root, :host { --brand-sefaria-blue: #FF0000; }' : ''
  );
  await page.goto('/?design-system-preview=penina/new-colors');

  await expect.poll(() => token(page, '--brand-sefaria-blue')).toBe('#FF0000');
  expect(requested.sort()).toEqual([
    '/Sefaria/design-system/penina/new-colors/core.css',
    '/Sefaria/design-system/penina/new-colors/semantic.css',
  ]);
});

test('preview CSS is text: markup in it never becomes elements', async ({ page }) => {
  await mockPreviewFiles(
    page,
    () => ':root, :host { --brand-sefaria-blue: #00FF00; } </style><img src="x" onerror="window.injected = true">'
  );
  await page.goto('/?design-system-preview=main');

  await expect.poll(() => token(page, '--brand-sefaria-blue')).toBe('#00FF00');
  expect(await page.evaluate(() => document.querySelector('lc-chatbot').shadowRoot.querySelector('img[src="x"]'))).toBeNull();
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});

test('a preview that is not a branch name or commit is ignored', async ({ page }) => {
  const requested = await mockPreviewFiles(page, () => ':root, :host { --brand-sefaria-blue: #FF0000; }');
  await page.goto(`/?design-system-preview=${encodeURIComponent('../../evil/repo/main')}`);
  await expect(page.locator('lc-chatbot')).toHaveCount(1);

  expect(await token(page, '--brand-sefaria-blue')).toBe('#18345D');
  expect(requested).toEqual([]);
});
