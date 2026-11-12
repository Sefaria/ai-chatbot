/**
 * Personalization in the widget: the two menu links, the notice under a personalized answer, and
 * the two-answer display that the settings state switches on. The chat and settings servers are
 * mocked (support/mockChatServer.js); fixtures are synthetic.
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

const plainColumn = (assistant) => assistant.host.getByTestId('lab-compare-plain');
const notice = (assistant) => assistant.host.getByTestId('personalization-notice');

function watchStreams(page) {
  const streams = [];
  page.on('request', (r) => {
    if (r.url().endsWith('/chat/stream')) streams.push(r.postDataJSON());
  });
  return streams;
}

test.describe('menu', () => {
  test('a signed-in user gets the opt-out and Personalization links to the Library Assistant settings', async ({ page, context }) => {
    const assistant = await signedIn(page, context, null);
    await assistant.openMoreOptions();
    const optOut = assistant.menu.getByRole('menuitem', { name: t('assistant.menu.optOut.aria') });
    const personalization = assistant.menu.getByRole('menuitem', { name: t('assistant.menu.personalization.aria') });
    await expect(optOut).toHaveAttribute('href', '/settings/library-assistant');
    await expect(personalization).toHaveAttribute('href', '/settings/library-assistant#personalization');
    await expect(personalization).toContainText(t('assistant.menu.personalization'));
  });

  test('a signed-out visitor sees neither link', async ({ page, context }) => {
    await mockChatServer(context, { personalization: { labEnabled: true, showPlainResponse: true } });
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();
    await assistant.openMoreOptions();
    await expect(assistant.menu.getByRole('menuitem', { name: t('assistant.menu.personalization.aria') })).toHaveCount(0);
    await expect(assistant.menu.getByRole('menuitem', { name: t('assistant.menu.optOut.aria') })).toHaveCount(0);
  });
});

test.describe('notice', () => {
  test('shows under a flagged answer with both links, and not under an unflagged one', async ({ page, context }) => {
    const assistant = await signedIn(page, context, { noticeOnAnswers: [1] });
    await assistant.ask('What is a mitzvah?');
    await expect(notice(assistant)).toHaveCount(1);
    await expect(notice(assistant)).toContainText(t('assistant.personalization.notice'));
    await expect(notice(assistant).getByRole('link', { name: t('assistant.personalization.configure') }))
      .toHaveAttribute('href', '/settings/library-assistant#personalization');
    await expect(notice(assistant).getByRole('link', { name: t('assistant.personalization.learn_more') }))
      .toHaveAttribute('href', '/settings/library-assistant#about-personalization');

    await assistant.ask('And a minhag?');
    await expect(assistant.answers).toHaveCount(2);
    await expect(notice(assistant)).toHaveCount(1); // still only the first answer's
  });

  test('is absent when the final payload does not carry the flag', async ({ page, context }) => {
    const assistant = await signedIn(page, context, { labEnabled: true });
    await assistant.ask('What is a mitzvah?');
    await expect(notice(assistant)).toHaveCount(0);
  });
});

test.describe('two answers', () => {
  test('appear side by side only when the state says lab on and show-plain on', async ({ page, context }) => {
    const assistant = await signedIn(page, context, { labEnabled: true, showPlainResponse: true });
    const streams = watchStreams(page);
    await assistant.send('What is a mitzvah?');

    const personalized = assistant.host.getByTestId('lab-compare-personalized');
    await expect(personalized).toContainText('Answer 1.');
    await expect(plainColumn(assistant)).toContainText('Plain answer.');
    const a = await personalized.boundingBox();
    const b = await plainColumn(assistant).boundingBox();
    expect(Math.abs(a.y - b.y)).toBeLessThan(4);
    expect(b.x).toBeGreaterThan(a.x);

    expect(streams).toHaveLength(2);
    const off = streams.find((r) => r.context.personalizationMode === 'off');
    expect(off.context.labCompare).toBe(true);
    expect(streams.filter((r) => r.context.labCompare)).toHaveLength(1);
    const normal = streams.find((r) => r.context.personalizationMode !== 'off');
    expect(normal.context.personalizationMode).toBeUndefined();
    expect(off.sessionId).not.toBe(normal.sessionId);
  });

  test('there is no Compare checkbox any more', async ({ page, context }) => {
    const assistant = await signedIn(page, context, { labEnabled: true, showPlainResponse: true });
    await expect(assistant.input).toBeVisible();
    await expect(assistant.host.getByRole('checkbox')).toHaveCount(0);
  });

  for (const [name, state] of [
    ['show-plain is off', { labEnabled: true, showPlainResponse: false }],
    ['the lab is off', { labEnabled: false, showPlainResponse: true }],
    ['the settings call fails', { status: 500, labEnabled: true, showPlainResponse: true }],
    ['the settings call is 404', { status: 404 }],
  ]) {
    test(`one normal answer when ${name}`, async ({ page, context }) => {
      const assistant = await signedIn(page, context, state);
      const streams = watchStreams(page);
      await assistant.ask('Hello');
      await expect(plainColumn(assistant)).toHaveCount(0);
      expect(streams).toHaveLength(1);
      expect(streams[0].context.personalizationMode).toBeUndefined();
    });
  }
});

test.describe('signed out', () => {
  test('never fetches the state, never shows two columns or a notice', async ({ page, context }) => {
    await mockChatServer(context, {
      personalization: { labEnabled: true, showPlainResponse: true, noticeOnAnswers: [1] },
    });
    const personalizationCalls = [];
    page.on('request', (r) => {
      if (r.url().includes('/v2/personalization/')) personalizationCalls.push(r.url());
    });
    const assistant = new AssistantPage(page);
    await assistant.goto();
    await assistant.open();
    const streams = watchStreams(page);
    await assistant.ask('Hello');
    await expect(plainColumn(assistant)).toHaveCount(0);
    await expect(notice(assistant)).toHaveCount(0);
    expect(streams).toHaveLength(1);
    expect(personalizationCalls).toEqual([]);
  });
});

test.describe('state request', () => {
  test('is one POST with the token in the body as userId and action get', async ({ page, context }) => {
    await mockChatServer(context, { personalization: { labEnabled: true } });
    const bodies = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/v2/personalization/settings')) bodies.push(r.postDataJSON());
    });
    const assistant = new AssistantPage(page);
    await assistant.logIn();
    await assistant.open();
    await expect.poll(() => bodies.length).toBe(1);
    expect(bodies[0]).toEqual({ userId: 'test-user-token', action: 'get' });
  });
});
