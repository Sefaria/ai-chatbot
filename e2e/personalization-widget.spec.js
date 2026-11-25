/**
 * Personalization toast wording and the Settings return address: every link from the widget to
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

test('the toast reads "Responses are personalized." and its Manage link carries from=', async ({ page, context }) => {
  const assistant = await signedIn(page, context, { noticeOnAnswers: [1] });
  await assistant.ask('What is a mitzvah?');
  const toast = assistant.host.getByTestId('personalization-toast');
  await expect(toast).toContainText('Responses are personalized.');
  const manage = toast.getByRole('link', { name: 'Manage' });
  await expect(manage).toHaveAttribute('href', /^\/settings\/library-assistant\?from=.+#personalization$/);
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
