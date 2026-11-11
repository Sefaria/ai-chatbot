/**
 * The visitor can turn off the floating "✦ Ask" button from the kebab menu.
 * The host's own entry points still open the assistant, and the choice is
 * remembered in this browser.
 */
import { test, expect } from '@playwright/test';
import { mockChatServer } from './support/mockChatServer.js';
import { AssistantPage } from './pages/assistantPage.js';

async function setUp(page, context, { loggedIn = false, savedHideLauncher } = {}) {
  await mockChatServer(context);
  const assistant = new AssistantPage(page);
  await (loggedIn ? assistant.logIn({ savedHideLauncher }) : assistant.goto());
  await assistant.open();
  return assistant;
}

test('turning off the Ask button hides it, also after a reload', async ({ page, context }) => {
  const assistant = await setUp(page, context);
  await assistant.openFloatingButtonSetting();
  await expect(assistant.floatingButtonSwitch).toBeChecked();

  await assistant.floatingButtonSwitch.click();
  await assistant.closeFloatingButtonSetting();
  await assistant.close();
  await expect(assistant.trigger).toHaveCount(0);

  await page.reload();
  await expect(assistant.host).toHaveCount(1);
  if (await assistant.panel.isVisible()) await assistant.close();
  await expect(assistant.trigger).toHaveCount(0);
});

test('with the Ask button off, the host can still open the assistant and turn it back on', async ({ page, context }) => {
  const assistant = await setUp(page, context);
  await assistant.openFloatingButtonSetting();
  await assistant.floatingButtonSwitch.click();
  await assistant.closeFloatingButtonSetting();
  await assistant.close();

  await assistant.openFromHost();
  await assistant.openFloatingButtonSetting('show');
  await expect(assistant.floatingButtonSwitch).not.toBeChecked();
  await assistant.floatingButtonSwitch.click();
  await assistant.closeFloatingButtonSetting();
  await assistant.close();
  await expect(assistant.trigger).toBeVisible();
});

test('a logged-out visitor is offered log-in and sign-up to remove the assistant from the header', async ({ page, context }) => {
  const assistant = await setUp(page, context);
  await assistant.openFloatingButtonSetting();
  await expect(assistant.floatingButtonLogInLink).toHaveAttribute('href', /\/login\?next=%2F$/);
  await expect(assistant.floatingButtonSignUpLink).toHaveAttribute('href', /\/register\?next=%2F$/);
});

test('a logged-in user is sent to the Library Assistant setting in account settings', async ({ page, context }) => {
  const assistant = await setUp(page, context, { loggedIn: true });
  await assistant.openFloatingButtonSetting();
  await expect(assistant.floatingButtonAccountSettingsLink).toHaveAttribute('href', /\/settings\/account#libraryAssistantSetting$/);
  await expect(assistant.floatingButtonSignUpLink).toHaveCount(0);
  await expect(assistant.floatingButtonLogInLink).toHaveCount(0);
});

test('the host is told each change, so it can save it to the account', async ({ page, context }) => {
  const assistant = await setUp(page, context, { loggedIn: true });
  await assistant.listenForLauncherVisibility();
  await assistant.openFloatingButtonSetting();
  await assistant.floatingButtonSwitch.click();
  await assistant.floatingButtonSwitch.click();
  expect(await assistant.launcherVisibilityEvents()).toEqual([true, false]);
});

test("a logged-in user's saved choice wins over this browser's", async ({ page, context }) => {
  // Hidden on another device: hidden here, though this browser never hid it
  let assistant = await setUp(page, context, { loggedIn: true, savedHideLauncher: true });
  await assistant.close();
  await expect(assistant.trigger).toHaveCount(0);

  // Shown again on another device: shown here, though this browser hid it
  await assistant.openFromHost();
  await assistant.openFloatingButtonSetting('show');
  await assistant.floatingButtonSwitch.click();
  await assistant.floatingButtonSwitch.click(); // hidden in this browser again
  await assistant.closeFloatingButtonSetting();
  await assistant.logIn({ savedHideLauncher: false });
  if (await assistant.panel.isVisible()) await assistant.close();
  await expect(assistant.trigger).toBeVisible();
});
