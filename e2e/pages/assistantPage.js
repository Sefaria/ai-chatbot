/**
 * Page object for the <lc-chatbot> widget in the behaviour tests.
 *
 * Test code only: it is not a page, the widget never imports it, and the
 * build doesn't include it. It holds how tests find and use things in the
 * widget, so the tests themselves read as behaviour. Raw selectors live here
 * and nowhere else. Copy is looked up by i18n key in en.json, so a copy change
 * doesn't break the tests.
 *
 * The widget renders into an open shadow root, which Playwright locators
 * pierce on their own. Tooltip bubbles are portalled to document.body.
 */
import { readFileSync } from 'node:fs';
import { expect } from '@playwright/test';

const en = JSON.parse(
  readFileSync(new URL('../../src/i18n/locales/en.json', import.meta.url), 'utf-8')
);

const ANSWER_TIMEOUT_MS = 10_000;
// The token the demo page gets after "logging in". The mock chat server
// accepts any token.
const LOGGED_IN_USER_ID = 'test-user-token';
const LOGIN_PROMPT = '[data-testid="la-login-prompt"]';

/** English copy for an i18n key. */
export function t(key) {
  if (!(key in en)) {
    throw new Error(`Missing i18n key in en.json: ${key}`);
  }
  return en[key];
}

export class AssistantPage {
  /** @param {import('@playwright/test').Page} page */
  constructor(page) {
    this.page = page;
    this.host = page.locator('lc-chatbot');
  }

  /**
   * Open the demo page (index.html, served by `npm run dev`) as a logged-out
   * visitor: with no ?user-id it mounts the widget without one, the way the
   * host site shows it to a logged-out visitor.
   */
  async goto() {
    await this.page.goto('/');
    await expect(this.host).toHaveCount(1);
  }

  /**
   * Log in (or sign up). On the real site this ends with the page reloading
   * and the widget mounted for the user; the widget can't tell login from
   * sign-up, so one method covers both. Here: reopen the demo page with
   * ?user-id=<token>.
   */
  async logIn() {
    await this.page.goto(`/?user-id=${encodeURIComponent(LOGGED_IN_USER_ID)}`);
    await expect(this.host).toHaveCount(1);
  }

  /** Log out: on the real site the page reloads without a user-id. */
  async logOut() {
    await this.goto();
  }

  // Panel

  get panel() {
    return this.host.getByRole('dialog', { name: t('assistant.header.chatWindow') });
  }

  get trigger() {
    return this.host.getByRole('button', { name: t('assistant.header.openAssistant') });
  }

  async open() {
    if (await this.panel.isVisible()) return;
    await this.trigger.click();
    await expect(this.panel).toBeVisible();
  }

  async close() {
    await this.host.getByRole('button', { name: t('assistant.header.close.tooltip'), exact: true }).click();
    await expect(this.panel).toBeHidden();
  }

  // Messages

  get messageList() {
    return this.host.getByRole('log', { name: t('assistant.messages.aria') });
  }

  get userMessages() {
    return this.messageList.locator('.message.user');
  }

  /** Answers only: not the welcome bubble, the loading bubble or limit notices. */
  get answers() {
    return this.messageList.locator(
      '.message.assistant:not(.limit-message):not(.empty-state *):has(.message-content)'
    );
  }

  get lastAnswer() {
    return this.answers.last();
  }

  async scrollMessagesToEnd() {
    await this.messageList.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });
  }

  async isScrolledToEnd() {
    return this.messageList.evaluate((el) => el.scrollTop + el.clientHeight >= el.scrollHeight - 2);
  }

  // Input

  get input() {
    return this.host.getByRole('textbox', { name: t('assistant.input.aria') });
  }

  get sendButton() {
    return this.host.getByRole('button', { name: t('assistant.input.send.tooltip'), exact: true });
  }

  /** Type a question and send it with Enter, without waiting for an answer. */
  async send(text) {
    await this.input.fill(text);
    await this.input.press('Enter');
  }

  /** Send a question and wait until its answer is shown. */
  async ask(text) {
    const before = await this.answers.count();
    await this.send(text);
    await expect(this.answers).toHaveCount(before + 1, { timeout: ANSWER_TIMEOUT_MS });
  }

  // Login prompt (shown in place of the text box once the free answers are used)

  get loginPrompt() {
    return this.host.locator(LOGIN_PROMPT);
  }

  get loginPromptText() {
    return this.loginPrompt.getByText(t('assistant.anon.limit_reached'), { exact: true });
  }

  /** "Log in or sign up"; it may be a link or a button. */
  get loginButton() {
    return this.loginPrompt.locator('a, button').filter({ hasText: t('assistant.anon.login_button') });
  }

  /** Whether keyboard focus (followed into shadow roots) is inside the login prompt. */
  async isFocusInsideLoginPrompt() {
    return this.page.evaluate((selector) => {
      let el = document.activeElement;
      while (el?.shadowRoot?.activeElement) {
        el = el.shadowRoot.activeElement;
      }
      return !!el?.closest(selector);
    }, LOGIN_PROMPT);
  }

  // Chat history

  get historyButton() {
    return this.host.locator('.history-btn');
  }

  get historyPanel() {
    return this.host.getByRole('complementary', { name: t('assistant.header.history.aria') });
  }

  get historyConversations() {
    return this.historyPanel.locator('.history-row');
  }

  async openHistory() {
    await this.historyButton.click();
    await expect(this.historyPanel).toBeVisible();
  }

  // More options menu

  get menu() {
    return this.host.getByRole('menu');
  }

  get menuItems() {
    return this.menu.getByRole('menuitem');
  }

  get newChatMenuItem() {
    // By role and name: its click label (data-feature-name) is absent while locked.
    return this.menu.getByRole('menuitem', { name: t('assistant.menu.restart.aria') });
  }

  async openMoreOptions() {
    await this.host.getByRole('button', { name: t('assistant.header.moreOptions'), exact: true }).click();
    await expect(this.menu).toBeVisible();
  }

  async startNewChat() {
    await this.openMoreOptions();
    await this.newChatMenuItem.click();
  }

  get tooltip() {
    return this.page.locator('[data-testid="la-tooltip"]');
  }

  // Errors

  get alerts() {
    return this.host.getByRole('alert');
  }

  get retryButtons() {
    return this.host.getByRole('button', { name: t('assistant.messages.retry') });
  }

  get failedMessages() {
    return this.messageList.locator('.message.failed');
  }
}
