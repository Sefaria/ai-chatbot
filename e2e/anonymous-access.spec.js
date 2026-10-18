/**
 * Logged-out use of the assistant.
 *
 * Every test starts as a logged-out visitor. The number of free answers comes
 * from getFreeAnswerLimit(), never from a number in a test. The chat server is
 * mocked (support/mockChatServer.js). Tests check only what the visitor sees.
 */
import { test, expect } from '@playwright/test';
import { mockChatServer } from './support/mockChatServer.js';
import { getFreeAnswerLimit } from './support/freeAnswerLimit.js';
import { AssistantPage, t } from './pages/assistantPage.js';

const TWO_DAYS_MS = 2 * 24 * 60 * 60 * 1000;
const limit = getFreeAnswerLimit();

/** Open the assistant as a logged-out visitor, with the mock chat server. */
async function setUp(page, context, serverOptions = {}) {
  await mockChatServer(context, serverOptions);
  const assistant = new AssistantPage(page);
  await assistant.goto();
  await assistant.open();
  return assistant;
}

/** Ask `count` questions, waiting for each answer. */
async function askQuestions(assistant, count) {
  for (let n = 1; n <= count; n += 1) {
    await assistant.ask(`Question ${n}`);
  }
}

/** Use up all the free answers. */
async function reachLimit(assistant) {
  await askQuestions(assistant, limit);
  await expect(assistant.loginPrompt).toBeVisible();
}

/**
 * Slow every animation on the page 100× (Chrome DevTools protocol, so Chrome
 * only), so a 200ms fade-in is still running when the test reads it.
 */
async function slowDownAnimations(page) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Animation.enable');
  await cdp.send('Animation.setPlaybackRate', { playbackRate: 0.01 });
}

/** Animations running on an element or inside it, with how they start. */
async function runningAnimations(locator) {
  return locator.evaluate((el) =>
    el.getAnimations({ subtree: true }).map((animation) => {
      const first = animation.effect.getKeyframes()[0] ?? {};
      const translateY = first.transform && first.transform !== 'none'
        ? new DOMMatrixReadOnly(first.transform).m42
        : 0;
      return {
        duration: Number(animation.effect.getTiming().duration),
        startOpacity: first.opacity === undefined ? null : Number(first.opacity),
        startTranslateY: translateY,
      };
    })
  );
}

test.describe('Logged-out use of the assistant', () => {
  test('a logged-out visitor can ask a question and get an answer', async ({ page, context }) => {
    const assistant = await setUp(page, context);

    await assistant.ask('What is the first verse of Genesis?');

    await expect(assistant.userMessages).toHaveCount(1);
    await expect(assistant.answers).toHaveCount(1);
  });

  test('the last free answer replaces the text box with the login prompt', async ({ page, context }) => {
    const assistant = await setUp(page, context, { longAnswers: true });
    await askQuestions(assistant, limit - 1);
    await expect(assistant.loginPrompt).toBeHidden();

    await assistant.ask('The last free question');

    // The text box and send button are replaced by the prompt.
    await expect(assistant.loginPromptText).toBeVisible();
    await expect(assistant.input).toBeHidden();
    await expect(assistant.sendButton).toBeHidden();

    // A full-width "Log in or sign up" button sits under the text.
    await expect(assistant.loginButton).toBeVisible();
    const textBox = await assistant.loginPromptText.boundingBox();
    const buttonBox = await assistant.loginButton.boundingBox();
    expect(buttonBox.y).toBeGreaterThanOrEqual(textBox.y + textBox.height - 1);
    const promptContentWidth = await assistant.loginPrompt.evaluate((el) => {
      const style = getComputedStyle(el);
      return el.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    });
    expect(Math.abs(buttonBox.width - promptContentWidth)).toBeLessThanOrEqual(1);

    // The whole last answer can still be scrolled to and read: once the list is
    // scrolled to its end, the end of the answer is visible and not under the prompt.
    await assistant.scrollMessagesToEnd();
    const listBox = await assistant.messageList.boundingBox();
    const promptBox = await assistant.loginPrompt.boundingBox();
    expect(listBox.y + listBox.height).toBeLessThanOrEqual(promptBox.y + 1);
    await expect(assistant.lastAnswer.getByText(`End of answer ${limit}.`)).toBeInViewport();

    // Keyboard focus is not moved to the prompt. (The text box that had focus is
    // gone, so "does not move" means nothing takes focus on purpose.)
    expect(await assistant.isFocusInsideLoginPrompt()).toBe(false);
  });

  test.describe('the login prompt', () => {
    test('has its colors, alignment and size', async ({ page, context }) => {
      const assistant = await setUp(page, context);
      await reachLimit(assistant);

      await expect(assistant.loginPromptText).toHaveCSS('color', 'rgb(87, 87, 87)');
      // Centered: measure the text itself, not its (possibly full-width) box.
      const textRun = await assistant.loginPromptText.evaluate((el) => {
        const range = document.createRange();
        range.selectNodeContents(el);
        const { x, width } = range.getBoundingClientRect();
        return { x, width };
      });
      const promptBox = await assistant.loginPrompt.boundingBox();
      const textCenter = textRun.x + textRun.width / 2;
      const promptCenter = promptBox.x + promptBox.width / 2;
      expect(Math.abs(textCenter - promptCenter)).toBeLessThanOrEqual(2);

      await expect(assistant.loginButton).toHaveCSS('background-color', 'rgb(24, 52, 93)');
      await expect(assistant.loginButton).toHaveCSS('color', 'rgb(255, 255, 255)');
      await expect(assistant.loginButton).toHaveCSS('border-radius', '8px');
      const buttonBox = await assistant.loginButton.boundingBox();
      expect(Math.abs(buttonBox.height - 40)).toBeLessThanOrEqual(0.5);
    });

    test('button changes color on hover', async ({ page, context }) => {
      const assistant = await setUp(page, context);
      await reachLimit(assistant);

      await assistant.loginButton.hover();

      await expect(assistant.loginButton).toHaveCSS('background-color', 'rgb(70, 93, 125)');
    });

    test('fades in, rising 6px, over 200ms', async ({ page, context, browserName }) => {
      test.skip(browserName !== 'chromium', 'Reading the fade needs the Chrome DevTools protocol');
      const assistant = await setUp(page, context);
      await askQuestions(assistant, limit - 1);
      await slowDownAnimations(page);

      await assistant.ask('The last free question');
      await expect(assistant.loginPrompt).toBeVisible();

      const animations = await runningAnimations(assistant.loginPrompt);
      expect(animations).toContainEqual({ duration: 200, startOpacity: 0, startTranslateY: 6 });
    });

    test('does not move with reduced motion', async ({ page, context, browserName }) => {
      test.skip(browserName !== 'chromium', 'Reading animations needs the Chrome DevTools protocol');
      await page.emulateMedia({ reducedMotion: 'reduce' });
      const assistant = await setUp(page, context);
      await askQuestions(assistant, limit - 1);
      await slowDownAnimations(page);

      await assistant.ask('The last free question');
      await expect(assistant.loginPrompt).toBeVisible();

      const moving = (await runningAnimations(assistant.loginPrompt)).filter(
        (a) => a.duration > 0 && a.startTranslateY !== 0
      );
      expect(moving).toEqual([]);
    });
  });

  test.describe('after a reload the assistant is as the visitor left it', () => {
    test('a closed assistant stays closed, and opens to the conversation with the login prompt', async ({ page, context }) => {
      const assistant = await setUp(page, context, { longAnswers: true });
      await reachLimit(assistant);
      await assistant.close();

      await page.reload();

      await expect(assistant.panel).toBeHidden();
      await expect(assistant.trigger).toBeVisible();
      await assistant.open();
      await expect(assistant.userMessages).toHaveCount(limit);
      await expect(assistant.answers).toHaveCount(limit);
      await expect(assistant.loginPrompt).toBeVisible();
      await expect.poll(() => assistant.isScrolledToEnd()).toBe(true);
    });

    test('an open assistant stays open, with the conversation and the login prompt', async ({ page, context }) => {
      const assistant = await setUp(page, context, { longAnswers: true });
      await reachLimit(assistant);

      await page.reload();

      await expect(assistant.panel).toBeVisible();
      await expect(assistant.userMessages).toHaveCount(limit);
      await expect(assistant.answers).toHaveCount(limit);
      await expect(assistant.loginPrompt).toBeVisible();
      await expect.poll(() => assistant.isScrolledToEnd()).toBe(true);
    });
  });

  test('days later the visitor still sees their conversation and the login prompt', async ({ page, context, browser }) => {
    const assistant = await setUp(page, context);
    await reachLimit(assistant);
    const storageState = await context.storageState();

    // Same browser, two days later.
    const later = await browser.newContext({ storageState });
    await later.clock.setSystemTime(Date.now() + TWO_DAYS_MS);
    await mockChatServer(later);
    const laterAssistant = new AssistantPage(await later.newPage());
    await laterAssistant.goto();
    await laterAssistant.open();

    await expect(laterAssistant.userMessages).toHaveCount(limit);
    await expect(laterAssistant.answers).toHaveCount(limit);
    await expect(laterAssistant.loginPrompt).toBeVisible();
    await later.close();
  });

  test('chat history is shown faded and does nothing', async ({ page, context }) => {
    const assistant = await setUp(page, context);

    await expect(assistant.historyButton).toBeVisible();
    const opacity = await assistant.historyButton.evaluate((el) => {
      // Effective opacity: the button's own times its ancestors', and its icon's.
      let value = 1;
      for (let node = el; node instanceof Element; node = node.parentElement) {
        value *= Number(getComputedStyle(node).opacity);
      }
      const icon = el.querySelector('img, svg');
      return icon ? value * Number(getComputedStyle(icon).opacity) : value;
    });
    expect(opacity).toBeLessThan(1);

    await assistant.historyButton.hover();
    await expect(assistant.tooltip).toHaveText(t('assistant.header.history.login_tooltip'));

    // force: a disabled button must still be clickable here, to show nothing happens.
    await assistant.historyButton.click({ force: true });
    await expect(assistant.historyPanel).toBeHidden();
  });

  test('New chat is disabled for a logged-out visitor', async ({ page, context }) => {
    const assistant = await setUp(page, context);
    await expect(assistant.userMessages).toHaveCount(0);

    await assistant.openMoreOptions();

    await expect(assistant.newChatMenuItem).toBeVisible();
    await expect(assistant.newChatMenuItem).toHaveText(t('assistant.history.header.new.tooltip'));
    await expect(assistant.newChatMenuItem).toBeDisabled();
  });

  test('the menu has no Disable in settings item', async ({ page, context }) => {
    const assistant = await setUp(page, context);

    await assistant.openMoreOptions();

    await expect(assistant.menuItems.filter({ hasText: t('assistant.menu.optout') })).toHaveCount(0);
  });

  test('all questions stay in one conversation', async ({ page, context }) => {
    const assistant = await setUp(page, context);

    await askQuestions(assistant, limit);

    await expect(assistant.userMessages).toHaveCount(limit);
    await expect(assistant.answers).toHaveCount(limit);
    for (let n = 1; n <= limit; n += 1) {
      await expect(assistant.userMessages.nth(n - 1)).toContainText(`Question ${n}`);
    }

    // No way to start a second conversation.
    await assistant.openMoreOptions();
    await expect(assistant.newChatMenuItem).toBeDisabled();
  });

  test('when another tab used up the free answers, sending shows the login prompt', async ({ page, context }) => {
    const thisTab = await setUp(page, context);
    const otherTab = new AssistantPage(await context.newPage());
    await otherTab.goto();
    await otherTab.open();

    // The other tab uses up the free answers; this tab still shows the text box.
    await reachLimit(otherTab);
    await expect(thisTab.input).toBeVisible();

    await thisTab.send('One more question');

    // The login prompt appears, the same as when reaching the limit, with no error.
    await expect(thisTab.loginPromptText).toBeVisible();
    await expect(thisTab.loginButton).toBeVisible();
    await expect(thisTab.input).toBeHidden();
    await expect(thisTab.alerts).toHaveCount(0);
    await expect(thisTab.retryButtons).toHaveCount(0);
    await expect(thisTab.failedMessages).toHaveCount(0);

    // After logging in, the question is still in the text box.
    await thisTab.logIn();
    await thisTab.open();
    await expect(thisTab.input).toHaveValue('One more question');
  });

  test('after signing up or logging in, the logged-out conversation is in chat history', async ({ page, context }) => {
    const assistant = await setUp(page, context);
    await askQuestions(assistant, limit);

    await assistant.logIn();
    await assistant.open();
    await assistant.startNewChat();
    await assistant.openHistory();

    await expect(assistant.historyConversations).toHaveCount(1);
    await assistant.historyConversations.first().click();
    await expect(assistant.userMessages).toHaveCount(limit);
    await expect(assistant.answers).toHaveCount(limit);
    for (let n = 1; n <= limit; n += 1) {
      await expect(assistant.userMessages.nth(n - 1)).toContainText(`Question ${n}`);
    }
  });

  test('after logging in, the visitor keeps their conversation and can keep asking', async ({ page, context }) => {
    const assistant = await setUp(page, context);
    await assistant.ask('A question while logged out');

    await assistant.logIn();
    await assistant.open();

    await expect(assistant.userMessages).toHaveCount(1);
    await expect(assistant.userMessages.first()).toContainText('A question while logged out');
    await expect(assistant.answers).toHaveCount(1);

    await assistant.ask('A question after logging in');
    await expect(assistant.userMessages).toHaveCount(2);

    // Both questions are one conversation in the user's history.
    await assistant.startNewChat();
    await assistant.openHistory();
    await expect(assistant.historyConversations).toHaveCount(1);
    await assistant.historyConversations.first().click();
    await expect(assistant.userMessages).toHaveCount(2);
  });

  test("after logging out, the user's conversation is gone", async ({ page, context }) => {
    await mockChatServer(context);
    const assistant = new AssistantPage(page);
    await assistant.logIn();
    await assistant.open();
    await assistant.ask('A question while logged in');
    await assistant.input.fill('An unsent draft');

    await assistant.logOut();
    await assistant.open();

    await expect(assistant.userMessages).toHaveCount(0);
    await expect(assistant.answers).toHaveCount(0);
    await expect(assistant.input).toHaveValue('');
  });
});
