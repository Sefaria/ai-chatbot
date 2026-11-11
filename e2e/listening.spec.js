import { test, expect } from '@playwright/test';
import { setupListening, speak, question } from './support/listening.js';

test('listening suggests without sending, click asks once and preserves the draft', async ({ page, context }) => {
  const assistant = await setupListening(page, context);
  await assistant.input.fill('My unsent draft');
  await assistant.startListening.click();
  await speak(page);
  await expect(assistant.suggestedQuestion(question)).toBeVisible({ timeout: 10000 });
  await expect(assistant.userMessages).toHaveCount(0);
  await expect(assistant.input).toHaveValue('My unsent draft');
  await assistant.suggestedQuestion(question).click();
  await expect(assistant.userMessages).toHaveCount(1);
  await expect(assistant.answers).toHaveCount(1);
  await expect(assistant.input).toHaveValue('My unsent draft');
  await expect(assistant.suggestedQuestions).toHaveCount(0);
  await assistant.stopListening.click();
  await expect(assistant.startListening).toBeVisible();
});

test('dismissal does not send and closing the panel stops the microphone', async ({ page, context }) => {
  const assistant = await setupListening(page, context);
  await assistant.startListening.click(); await speak(page);
  await expect(assistant.suggestedQuestion(question)).toBeVisible({ timeout: 10000 });
  await assistant.dismissQuestion.click();
  await expect(assistant.suggestedQuestions).toHaveCount(0);
  await expect(assistant.userMessages).toHaveCount(0);
  const stopped = page.waitForRequest(request => request.url().endsWith('/test-session/stop'));
  await assistant.close(); await stopped;
  await assistant.open(); await expect(assistant.startListening).toBeVisible();
});

for (const options of [{ enabled: false }, { supported: false }]) {
  test(`listening is absent when ${JSON.stringify(options)}`, async ({ page, context }) => {
    const assistant = await setupListening(page, context, options);
    await expect(assistant.startListening).toHaveCount(0);
    await assistant.ask('An ordinary typed question');
    await expect(assistant.answers).toHaveCount(1);
  });
}
