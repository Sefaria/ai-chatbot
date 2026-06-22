/**
 * Test 3 — Topic appetizer navigation
 *
 * On sefaria.org origin, clicking an appetizer topic button should dispatch
 * a "sefaria:bootstrap-url" CustomEvent on document with detail.url = '/topics/shabbat'.
 *
 * handleAppetizerClick in LCChatbot.svelte:
 *   const onSefaria = window.location.hostname.includes('sefaria.org');
 *   if (onSefaria) {
 *     document.dispatchEvent(new CustomEvent('sefaria:bootstrap-url', { detail: { url: `/topics/${topicSlug}` } }));
 *   }
 *
 * The appetizer is rendered inside an Accordion (kind="topics") that is collapsed
 * by default. The accordion header button says "Show related topics". We expand it
 * first, then find and click the "Shabbat" button (class "lc-topic-link").
 */

import { test, expect } from '@playwright/test';
import { setupMocks, setupSefariaOriginFixture, waitForPanelReady, sendMessage } from './helpers.js';

test('topic appetizer click dispatches sefaria:bootstrap-url with /topics/shabbat', async ({ page }) => {
  const consoleErrors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });

  await setupSefariaOriginFixture(page);
  await setupMocks(page, { mock404Ref: false });

  await page.goto('https://www.sefaria.org/Genesis.1.1');
  await waitForPanelReady(page);

  // Install event listener BEFORE sending so we don't miss the event
  await page.evaluate(() => {
    window.__bootstrap = [];
    document.addEventListener('sefaria:bootstrap-url', (e) => window.__bootstrap.push(e.detail));
  });

  await sendMessage(page, 'Tell me about Shabbat');

  // Wait for the assistant response to arrive (response package rendered)
  await page.waitForSelector('.lc-response-package', { timeout: 15_000 });

  // The appetizer is inside the "topics" accordion which is collapsed by default.
  // The accordion header button has text "Show related topics".
  // We click it to expand, then the lc-topic-link buttons become visible.
  const topicsAccordionBtn = page.locator('button.lc-accordion-header', { hasText: 'Show related topics' });
  await expect(topicsAccordionBtn).toBeVisible({ timeout: 8_000 });
  await topicsAccordionBtn.click();

  // Now the topic button with text "Shabbat" and class "lc-topic-link" should be visible
  const topicBtn = page.locator('button.lc-topic-link', { hasText: 'Shabbat' });
  await expect(topicBtn.first()).toBeVisible({ timeout: 8_000 });

  // Click the topic button
  await topicBtn.first().click();

  // Assert the bootstrap event was dispatched with the correct url
  const bootstrap = await page.evaluate(() => window.__bootstrap);

  expect(bootstrap.length).toBeGreaterThanOrEqual(1);
  expect(bootstrap[0].url).toBe('/topics/shabbat');

  if (consoleErrors.length > 0) {
    console.log('[test3 console errors]', consoleErrors);
  }
});
