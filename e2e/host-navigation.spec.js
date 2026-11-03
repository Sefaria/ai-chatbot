/**
 * The phone sheet's history entry when the host page navigates from inside it.
 *
 * On phones the open assistant is a full-screen sheet with its own history entry,
 * so Back closes it. A host can also navigate from inside the sheet with its own
 * controls, e.g. Sefaria's Email sign-up option in the login prompt, which opens
 * its sign-up form and removes the assistant. Back from there should bring the
 * sheet back as it was, open; the next Back closes it, as always.
 * The chat server is mocked (support/mockChatServer.js).
 */
import { test, expect } from '@playwright/test';
import { mockChatServer } from './support/mockChatServer.js';
import { AssistantPage } from './pages/assistantPage.js';

const PHONE = { width: 375, height: 740 };

test.describe('The phone sheet and the host page’s navigation', () => {
  test.beforeEach(async ({ page, context }) => {
    await mockChatServer(context);
    await page.setViewportSize(PHONE);
  });

  test("the open sheet's history entry is marked, so the host can tell it's the current one", async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();

    await assistant.open();

    expect(await assistant.isSheetEntryCurrent()).toBe(true);
  });

  test('Back from a host page opened from inside the sheet brings the sheet back, open', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();
    const pageUrl = page.url();

    await assistant.actAsHostOpeningAPageFromTheSheet('/register');
    await assistant.pressBack();

    // The host hears that Back and shows its page again (with the assistant)…
    await expect.poll(() => assistant.hostBackNavigations()).toEqual([pageUrl]);
    // …and the assistant comes back as it was: the sheet, open.
    await expect(assistant.panel).toBeVisible();
    expect(await assistant.isSheetEntryCurrent()).toBe(true);
  });

  test('then the next Back closes the sheet, staying on the page', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();
    const pageUrl = page.url();
    await assistant.actAsHostOpeningAPageFromTheSheet('/register');
    await assistant.pressBack();
    await expect(assistant.panel).toBeVisible();

    await assistant.pressBack();

    await expect(assistant.panel).toBeHidden();
    await expect(assistant.trigger).toBeVisible();
    expect(page.url()).toBe(pageUrl);
  });
});
