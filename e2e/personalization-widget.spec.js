/**
 * Personalization notice wording and the Settings return address: every link from the widget to
 * the Library Assistant settings carries `from=` (where to come back to), and the profile-used
 * line has an Edit link to the profile section. Chat server mocked; fixtures synthetic.
 */
import { test, expect } from '@playwright/test';
import { mockChatServer } from './support/mockChatServer.js';
import { AssistantPage, t } from './pages/assistantPage.js';

async function signedIn(page, context, personalization) {
  await mockChatServer(context, { personalization });
  const assistant = new AssistantPage(page);
  await assistant.logIn();
  await assistant.open();
  return assistant;
}

test('the notice reads in the friendlier wording and both its links carry from=', async ({ page, context }) => {
  const assistant = await signedIn(page, context, { noticeOnAnswers: [1] });
  await assistant.ask('What is a mitzvah?');
  const notice = assistant.host.getByTestId('personalization-notice');
  await expect(notice).toContainText('From now on, answers are tailored to you.');
  const change = notice.getByRole('link', { name: 'Change this' });
  const more = notice.getByRole('link', { name: 'Learn more' });
  await expect(change).toHaveAttribute('href', /^\/settings\/library-assistant\?from=.+#personalization$/);
  await expect(more).toHaveAttribute('href', /^\/settings\/library-assistant\?from=.+#about-personalization$/);
});

test('an answer with a profile-used line has an Edit link to the profile section with from=', async ({ page, context }) => {
  const assistant = await signedIn(page, context, {
    labEnabled: true,
    profileUsed: 'From your profile, I took your preference for short answers into account.',
  });
  await assistant.ask('What is a mitzvah?');
  await expect(assistant.host.getByTestId('profile-used')).toBeVisible();
  const edit = assistant.host.getByRole('link', { name: t('assistant.personalization.edit') });
  await expect(edit).toHaveText('Edit');
  await expect(edit).toHaveAttribute('href', /^\/settings\/library-assistant\?from=.+#profile$/);
});
