/**
 * The host's entry point while the assistant is already open.
 *
 * Sefaria's header pill ("✦ Library Assistant") opens the assistant by sending
 * chatbot:open. Clicked again while the assistant is open, it used to do nothing;
 * now the panel hops twice to say "I'm here": floating it hops up, docked it hops
 * toward the page. It settles back where it was and nothing else changes. No hop
 * on phones (the sheet fills the screen) or with reduced motion.
 * The chat server is mocked (support/mockChatServer.js).
 */
import { test, expect } from '@playwright/test';
import { mockChatServer } from './support/mockChatServer.js';
import { AssistantPage } from './pages/assistantPage.js';

const DESKTOP = { width: 1280, height: 800 };
const PHONE = { width: 375, height: 740 };
// The hop is 16px; anything under 1px is standing still.
const A_HOP = 10;
const STILL = 1;

function expectStill(movement) {
  for (const way of ['up', 'down', 'left', 'right']) expect(movement[way]).toBeLessThan(STILL);
}

test.describe("The host's entry point while the assistant is open", () => {
  test.beforeEach(async ({ page, context }) => {
    await mockChatServer(context);
    await page.setViewportSize(DESKTOP);
  });

  test('the floating assistant hops up and settles back where it was', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();

    const movement = await assistant.panelMovementWhenHostOpensTheAssistant();

    expect(movement.up).toBeGreaterThan(A_HOP);
    expect(movement.left).toBeLessThan(STILL);
    expect(movement.right).toBeLessThan(STILL);
    expect(Math.abs(movement.end.y)).toBeLessThan(STILL);
    await expect(assistant.panel).toBeVisible();
  });

  test('it hops every time the entry point is clicked', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();

    const first = await assistant.panelMovementWhenHostOpensTheAssistant();
    const second = await assistant.panelMovementWhenHostOpensTheAssistant();

    expect(first.up).toBeGreaterThan(A_HOP);
    expect(second.up).toBeGreaterThan(A_HOP);
  });

  test('docked, it hops toward the page and settles back', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();
    await assistant.dock();

    const movement = await assistant.panelMovementWhenHostOpensTheAssistant();

    expect(movement.left).toBeGreaterThan(A_HOP);
    expect(movement.right).toBeLessThan(STILL);
    expect(movement.up).toBeLessThan(STILL);
    expect(Math.abs(movement.end.x)).toBeLessThan(STILL);
  });

  test('in Hebrew, docked, it hops toward the page on the other side', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();
    await assistant.dock();
    await assistant.useHebrew();

    const movement = await assistant.panelMovementWhenHostOpensTheAssistant();

    expect(movement.right).toBeGreaterThan(A_HOP);
    expect(movement.left).toBeLessThan(STILL);
  });

  test('what the reader was typing and the open chat are left as they were', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();
    await assistant.input.fill('What does Rashi say about');

    await assistant.panelMovementWhenHostOpensTheAssistant();

    await expect(assistant.panel).toBeVisible();
    await expect(assistant.input).toHaveValue('What does Rashi say about');
  });

  test('with reduced motion, it stays still', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();

    expectStill(await assistant.panelMovementWhenHostOpensTheAssistant());
    await expect(assistant.panel).toBeVisible();
  });

  test('on a phone, the sheet stays still', async ({ page }) => {
    await page.setViewportSize(PHONE);
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();

    expectStill(await assistant.panelMovementWhenHostOpensTheAssistant());
    await expect(assistant.panel).toBeVisible();
  });

  test('when the assistant is closed, the entry point opens it, as before', async ({ page }) => {
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();
    await assistant.close();

    await assistant.actAsHostOpeningTheAssistant();

    await expect(assistant.panel).toBeVisible();
  });
});
