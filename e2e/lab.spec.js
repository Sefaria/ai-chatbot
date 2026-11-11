/**
 * Personalization lab: compare, Profile tab, the job, Load learner. All of it shows only when
 * the server says overrideAllowedForYou. The chat and profile servers are mocked
 * (support/mockChatServer.js); fixtures are synthetic.
 */
import { test, expect } from '@playwright/test';
import { mockChatServer } from './support/mockChatServer.js';
import { AssistantPage } from './pages/assistantPage.js';

async function setUp(page, context, lab) {
  await mockChatServer(context, { lab });
  const assistant = new AssistantPage(page);
  await assistant.logIn();
  await assistant.open();
  return assistant;
}

/** Open Settings (the lab user is not a moderator, so it lands on the Profile tab). */
async function openProfile(assistant) {
  await assistant.openMoreOptions();
  await assistant.menu.getByRole('menuitem', { name: 'Open settings' }).click();
  await expect(assistant.host.getByTestId('lab-profile')).toBeVisible();
}

const compareToggle = (assistant) => assistant.host.getByLabel('Compare: personalized vs plain');

test.describe('lab UI hidden', () => {
  test('nothing lab-related shows when the override is not allowed', async ({ page, context }) => {
    const assistant = await setUp(page, context, { allowed: false });
    await expect(assistant.input).toBeVisible();
    await expect(compareToggle(assistant)).toHaveCount(0);
    await assistant.openMoreOptions();
    await expect(assistant.menu.getByRole('menuitem', { name: 'Open settings' })).toHaveCount(0);
  });

  test('nothing lab-related shows without the lab at all', async ({ page, context }) => {
    const assistant = await setUp(page, context, null);
    await expect(compareToggle(assistant)).toHaveCount(0);
  });
});

test.describe('compare', () => {
  test('one question shows a Personalized and a Plain answer side by side', async ({ page, context }) => {
    const assistant = await setUp(page, context, { allowed: true });
    const requests = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/chat/stream')) requests.push(r.postDataJSON());
    });
    await compareToggle(assistant).check();
    await assistant.send('What is a mitzvah?');

    const personalized = assistant.host.getByTestId('lab-compare-personalized');
    const plain = assistant.host.getByTestId('lab-compare-plain');
    await expect(personalized).toContainText('Personalized');
    await expect(personalized).toContainText('Answer 1.');
    await expect(plain).toContainText('Plain');
    await expect(plain).toContainText('Plain answer.');

    const a = await personalized.boundingBox();
    const b = await plain.boundingBox();
    expect(Math.abs(a.y - b.y)).toBeLessThan(4); // same row: side by side
    expect(b.x).toBeGreaterThan(a.x);

    // Two streams; the plain one is "off" in its own session.
    expect(requests).toHaveLength(2);
    const off = requests.find((r) => r.context.personalizationMode === 'off');
    const normal = requests.find((r) => r.context.personalizationMode !== 'off');
    expect(off.sessionId).not.toBe(normal.sessionId);
    await expect(assistant.answers).toHaveCount(2); // personalized bubble + plain bubble, one turn
  });

  test('without the toggle a question gets one normal answer', async ({ page, context }) => {
    const assistant = await setUp(page, context, { allowed: true });
    await assistant.ask('Hello');
    await expect(assistant.host.getByTestId('lab-compare')).toHaveCount(0);
  });
});

test.describe('profile tab', () => {
  test('lists the slots', async ({ page, context }) => {
    const assistant = await setUp(page, context, { allowed: true });
    await openProfile(assistant);
    const rows = assistant.host.locator('.slot-row');
    await expect(rows).toHaveCount(2);
    await expect(rows.first()).toContainText('learning_level');
    await expect(rows.first()).toContainText('beginner');
    await expect(rows.first()).toContainText('stated');
    await expect(rows.first()).toContainText('2 evidence');
  });

  test('editing a value saves it', async ({ page, context }) => {
    const assistant = await setUp(page, context, { allowed: true });
    await openProfile(assistant);
    const row = assistant.host.locator('.slot-row[data-slot="learning_level"]');
    await row.getByRole('button', { name: 'Edit' }).click();
    await row.getByRole('textbox').fill('advanced');
    await row.getByRole('button', { name: 'Save' }).click();
    await expect(row).toContainText('advanced');
  });

  test('run the job shows a change; Apply refreshes the slots', async ({ page, context }) => {
    const assistant = await setUp(page, context, { allowed: true });
    await openProfile(assistant);
    await assistant.host.getByRole('button', { name: 'Run the job' }).click();
    const result = assistant.host.getByTestId('lab-job-result');
    await expect(result).toContainText('beginner → intermediate');
    await expect(result).toContainText('Asked about commentaries twice');
    await expect(result).toContainText('7 observations read');
    await result.getByRole('button', { name: 'Apply' }).click();
    await expect(assistant.host.locator('.slot-row[data-slot="learning_level"]')).toContainText('intermediate');
    await expect(result).toHaveCount(0);
  });

  test('load learner posts the file turns and reports how many', async ({ page, context }) => {
    const assistant = await setUp(page, context, { allowed: true });
    await openProfile(assistant);
    const posted = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/v2/personalization/profile') && r.postDataJSON().action === 'seed_persona') {
        posted.push(r.postDataJSON());
      }
    });
    const turns = [
      { user: 'Q one', assistant: 'A one', sessionId: 's1', ts: '2026-01-01T10:00:00Z' },
      { user: 'Q two', assistant: 'A two', sessionId: 's1', ts: '2026-01-01T10:01:00Z' },
    ];
    await assistant.host.getByLabel('Load learner file').setInputFiles({
      name: 'learner.json',
      mimeType: 'application/json',
      buffer: Buffer.from(JSON.stringify({ label: 'Synthetic learner', turns })),
    });
    await expect(assistant.host.getByText('seeded 2 turns')).toBeVisible();
    expect(posted).toHaveLength(1);
    expect(posted[0].turns).toEqual(turns);
  });
});

test.describe('logged-out lab visitor', () => {
  async function fresh(page, context, query) {
    await mockChatServer(context, { lab: { allowed: true } });
    const assistant = new AssistantPage(page);
    await page.goto(`/${query}`);
    await expect(assistant.host).toHaveCount(1);
    await assistant.open();
    return assistant;
  }

  test('a fresh browser with the personalization-lab attribute sees the lab UI and sends hybrid', async ({ page, context }) => {
    const assistant = await fresh(page, context, '?personalization-lab=1');
    await expect(compareToggle(assistant)).toBeVisible();
    const requests = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/chat/stream')) requests.push(r.postDataJSON());
    });
    await assistant.ask('Hello');
    expect(requests[0].anonId).toBeTruthy();
    expect(requests[0].context.personalizationMode).toBe('hybrid');
  });

  test('?la_lab=1 also turns it on, and ?la_personalization= wins over the hybrid default', async ({ page, context }) => {
    const assistant = await fresh(page, context, '?la_lab=1&la_personalization=injected');
    await expect(compareToggle(assistant)).toBeVisible();
    const requests = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/chat/stream')) requests.push(r.postDataJSON());
    });
    await assistant.ask('Hello');
    expect(requests[0].context.personalizationMode).toBe('injected');
  });

  test('without the attribute a fresh browser is unchanged: no probe, no lab UI, no mode', async ({ page, context }) => {
    const probes = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/v2/personalization/status')) probes.push(r.url());
    });
    const assistant = await fresh(page, context, '');
    await expect(assistant.input).toBeVisible();
    await expect(compareToggle(assistant)).toHaveCount(0);
    const requests = [];
    page.on('request', (r) => {
      if (r.url().endsWith('/chat/stream')) requests.push(r.postDataJSON());
    });
    await assistant.ask('Hello');
    expect(requests[0].context.personalizationMode).toBeUndefined();
    expect(probes).toHaveLength(0);
  });
});

test('a list slot is edited as comma-separated text and saved as an array', async ({ page, context }) => {
  const assistant = await setUp(page, context, { allowed: true });
  await openProfile(assistant);
  const row = assistant.host.locator('.slot-row[data-slot="interests"]');
  await expect(row).toContainText('Talmud, ethics');
  const posted = [];
  page.on('request', (r) => {
    if (r.url().endsWith('/v2/personalization/profile') && r.postDataJSON().action === 'set') posted.push(r.postDataJSON());
  });
  await row.getByRole('button', { name: 'Edit' }).click();
  await row.getByRole('textbox').fill('Talmud, ethics, Halakha');
  await row.getByRole('button', { name: 'Save' }).click();
  await expect(row).toContainText('Talmud, ethics, Halakha');
  expect(posted[0].value).toEqual(['Talmud', 'ethics', 'Halakha']);
});
