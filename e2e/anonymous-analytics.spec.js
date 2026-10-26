/**
 * Analytics of logged-out use of the assistant.
 *
 * The widget's GA4 events are recorded through a stub `window.gtag`
 * (support/analytics.js); the chat server is mocked (support/mockChatServer.js).
 * The number of free answers comes from getFreeAnswerLimit().
 */
import { test, expect } from '@playwright/test';
import { mockChatServer } from './support/mockChatServer.js';
import { getFreeAnswerLimit } from './support/freeAnswerLimit.js';
import { recordAnalytics, analyticsEvents } from './support/analytics.js';
import { AssistantPage } from './pages/assistantPage.js';

const limit = getFreeAnswerLimit();

/** Open the assistant as a logged-out visitor, recording analytics. */
async function setUp(page, context, gotoOptions = { hostFreeAnswers: limit }) {
  await mockChatServer(context);
  await recordAnalytics(context);
  const assistant = new AssistantPage(page);
  await assistant.goto(gotoOptions);
  await assistant.open();
  return assistant;
}

async function promptsLeftReported(page) {
  return (await analyticsEvents(page, 'assistant_message_sent')).map((e) => e.prompts_left);
}

test.describe('Analytics of logged-out use', () => {
  test('each question reports how many free answers were left before it', async ({ page, context }) => {
    const assistant = await setUp(page, context);

    for (let n = 1; n <= limit; n += 1) {
      await assistant.ask(`Question ${n}`);
    }

    // A new visitor's first question reports the host's limit; then it counts down.
    const expected = Array.from({ length: limit }, (_, i) => limit - i);
    expect(await promptsLeftReported(page)).toEqual(expected);
  });

  test("without the host's limit, a new visitor's first question doesn't report what's left", async ({ page, context }) => {
    const assistant = await setUp(page, context, {});

    await assistant.ask('Question 1');
    await assistant.ask('Question 2');

    expect(await promptsLeftReported(page)).toEqual([undefined, limit - 1]);
  });

  test('a question sent after another tab used up the free answers reports none left', async ({ page, context }) => {
    const thisTab = await setUp(page, context);
    const otherTab = new AssistantPage(await context.newPage());
    await otherTab.goto({ hostFreeAnswers: limit });
    await otherTab.open();
    for (let n = 1; n <= limit; n += 1) {
      await otherTab.ask(`Question ${n}`);
    }

    await thisTab.send('One more question');

    await expect(thisTab.loginPromptText).toBeVisible();
    expect(await promptsLeftReported(page)).toEqual([0]);
  });

  test("a logged-in user's questions don't report what's left", async ({ page, context }) => {
    const assistant = await setUp(page, context);
    await assistant.logIn();
    await assistant.open();

    await assistant.ask('A question');

    const sent = await analyticsEvents(page, 'assistant_message_sent');
    expect(sent).toHaveLength(1);
    expect(sent[0].prompts_left).toBeUndefined();
  });

  test('the last free answer reports the limit reached, once', async ({ page, context }) => {
    const assistant = await setUp(page, context);
    for (let n = 1; n < limit; n += 1) {
      await assistant.ask(`Question ${n}`);
    }
    expect(await analyticsEvents(page, 'chat_rate_limit_reached')).toEqual([]);

    await assistant.ask('The last free question');

    const reached = await analyticsEvents(page, 'chat_rate_limit_reached');
    expect(reached).toHaveLength(1);
    expect(reached[0]).toMatchObject({ rate_limit: limit, total_messages_sent: limit });

    // Not again when the visitor comes back to the prompt.
    await page.reload();
    await assistant.open();
    await expect(assistant.loginPrompt).toBeVisible();
    expect(await analyticsEvents(page, 'chat_rate_limit_reached')).toEqual([]);
  });

  test("a click on the host page's sign-up option is reported with its feature name", async ({ page, context }) => {
    const assistant = await setUp(page, context);
    await assistant.addHostSignupOptions({ featureName: 'rate_limit_login_button' });
    for (let n = 1; n <= limit; n += 1) {
      await assistant.ask(`Question ${n}`);
    }

    await assistant.hostSignupOptions.getByRole('button').click();

    const clicks = await analyticsEvents(page, 'assistant_click');
    expect(clicks.map((e) => e.feature_name)).toContain('rate_limit_login_button');
  });
});
