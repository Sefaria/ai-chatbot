/**
 * The assistant follows the host page's light/dark theme.
 *
 * Sefaria says which theme is on only with <html data-theme="dark|light">, and can
 * switch it at any time without a reload. The assistant shows the same theme, and
 * says which one on its own element (<lc-chatbot data-theme>), which is how the page
 * tells it apart from an older assistant with no dark theme.
 * The chat server is mocked (support/mockChatServer.js).
 */
import { test, expect } from '@playwright/test';
import { mockChatServer } from './support/mockChatServer.js';
import { AssistantPage } from './pages/assistantPage.js';

// The open panel's background (--lc-body-bg) in each theme
const LIGHT_PANEL = 'rgb(249, 250, 251)';
const DARK_PANEL = 'rgb(28, 28, 28)';

test.describe('The assistant follows the page’s light/dark theme', () => {
  test.beforeEach(async ({ context }) => {
    await mockChatServer(context);
  });

  test('on a dark page it opens dark', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto({ pageTheme: 'dark' });

    await assistant.open();

    await expect(assistant.host).toHaveAttribute('data-theme', 'dark');
    await expect(assistant.panel).toHaveCSS('background-color', DARK_PANEL);
    await expect(assistant.panel).toHaveCSS('color-scheme', 'dark');
  });

  test('on a light page it opens light', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto({ pageTheme: 'light' });

    await assistant.open();

    await expect(assistant.host).toHaveAttribute('data-theme', 'light');
    await expect(assistant.panel).toHaveCSS('background-color', LIGHT_PANEL);
  });

  test('a page that has not set its theme yet is light', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();

    await assistant.open();

    await expect(assistant.host).toHaveAttribute('data-theme', 'light');
    await expect(assistant.panel).toHaveCSS('background-color', LIGHT_PANEL);
  });

  test('when the page switches theme, the open assistant switches with it, both ways', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto({ pageTheme: 'light' });
    await assistant.open();

    await assistant.setPageTheme('dark');
    await expect(assistant.host).toHaveAttribute('data-theme', 'dark');
    await expect(assistant.panel).toHaveCSS('background-color', DARK_PANEL);

    await assistant.setPageTheme('light');
    await expect(assistant.host).toHaveAttribute('data-theme', 'light');
    await expect(assistant.panel).toHaveCSS('background-color', LIGHT_PANEL);

    await assistant.setPageTheme(null);
    await assistant.setPageTheme('dark');
    await expect(assistant.panel).toHaveCSS('background-color', DARK_PANEL);
  });

  test('the page’s light-island rule for older assistants does not reach this one', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto({ pageTheme: 'dark' });
    // Sefaria keeps an assistant with no data-theme (an older one) light with this rule;
    // even the rule it used before that, for every assistant, leaves this one dark.
    await page.addStyleTag({ content: ':root[data-theme="dark"] lc-chatbot { color-scheme: light; color: #333333; }' });

    await assistant.open();

    await expect(assistant.panel).toHaveCSS('color-scheme', 'dark');
    await expect(assistant.panel).toHaveCSS('color', 'rgb(232, 230, 227)');
  });
});
