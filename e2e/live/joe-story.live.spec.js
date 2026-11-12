// LIVE end-to-end check of the "Joe" story (steps 1-9 of the live-run list).
// Real Sefaria site + real chatbot server + real model: it spends model calls (about 20 chat turns
// and a few Profile-job runs). Skipped unless LIVE=1. See e2e/live/README.md.
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { AssistantPage } from '../pages/assistantPage.js';

const SEFARIA = process.env.LIVE_SEFARIA_URL || 'http://localhost:8000';
const USER = process.env.LIVE_SEFARIA_USER || 'poc-la-test@example.invalid';
const PASSWORD = process.env.LIVE_SEFARIA_PASSWORD;
const SHOTS = process.env.LIVE_SHOTS_DIR || path.resolve('test-results/live-shots');
// A page that renders without library data. The reader pages (/texts, /Genesis.1) crash on a local Mongo without
// calendar data; /about mounts the assistant fine. Set LIVE_CHAT_PATH to a reader page where the library is loaded.
const CHAT_PATH = process.env.LIVE_CHAT_PATH || '/about';
const SETTINGS = `${SEFARIA}/settings/library-assistant`;
const ANSWER_MS = 4 * 60 * 1000;
const JOB_MS = 2 * 60 * 1000;

test.describe.configure({ mode: 'serial' });
test.skip(!process.env.LIVE, 'Live run: set LIVE=1 (needs both servers and spends model calls)');

let context;
let page;
let apiBase;
let token;
const quotes = {};

const shot = async (name) => {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
};
const quote = (key, text) => {
  quotes[key] = text;
  console.log(`[evidence] ${key}: ${String(text).replace(/\s+/g, ' ').slice(0, 400)}`);
};

/** A call to the personalization settings API with the page's own signed token (never printed). */
const api = async (action, body = {}) => {
  const res = await page.request.post(`${apiBase}/v2/personalization/settings`, {
    data: { userId: token, action, ...body },
  });
  return { status: res.status(), json: await res.json().catch(() => null) };
};

const openSettings = async () => {
  await page.goto(SETTINGS);
  await expect(page.locator('#personalization')).toBeVisible();
  await expect(page.locator('#instructions')).toBeVisible({ timeout: 30_000 });
};
const profileText = () => page.locator('.laProfileText').innerText();

/** Open a Library page, open the assistant, send a question, wait for the whole answer. */
const ask = async (question, { path: urlPath = CHAT_PATH, fresh = false } = {}) => {
  if (fresh || !page.url().includes(urlPath)) await page.goto(`${SEFARIA}${urlPath}`);
  const la = new AssistantPage(page);
  await expect(la.host).toHaveCount(1, { timeout: 30_000 });
  await la.open();
  const before = await la.answers.count();
  await la.send(question);
  await expect(la.answers).toHaveCount(before + 1, { timeout: ANSWER_MS });
  await expect(la.host.locator('.lc-streaming-content')).toHaveCount(0, { timeout: ANSWER_MS });
  await expect(la.input).toBeEnabled({ timeout: ANSWER_MS });
  await page.waitForTimeout(1500);
  const text = await la.lastAnswer.innerText();
  // The widget shows this apology when the server could not answer (bad model key, server error).
  expect(text, 'a real answer, not the failure apology').not.toMatch(/wasn't able to process your message/i);
  return { la, text, count: before + 1 };
};

test.beforeAll(async ({ browser }) => {
  if (!PASSWORD) throw new Error('Set LIVE_SEFARIA_PASSWORD (and LIVE_SEFARIA_USER) for the local test user');
  context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage();
  await page.goto(`${SEFARIA}/login?next=/settings/library-assistant`);
  await page.getByRole('button', { name: 'OK', exact: true }).click().catch(() => {});
  await page.getByText('Continue with Email').click();
  await page.locator('input[name="email"]').fill(USER);
  await page.locator('input[name="password"]').fill(PASSWORD);
  await page.locator('input[name="password"]').press('Enter');
  await page.waitForURL((url) => url.pathname.startsWith('/settings'), { timeout: 30_000 });
  await page.waitForFunction(() => window.DJANGO_VARS && window.DJANGO_VARS.props && window.DJANGO_VARS.props.chatbot_user_token, null, {
    timeout: 30_000,
  }).catch(() => {});
  const globals = await page.evaluate(() => ({
    token: window.DJANGO_VARS.props.chatbot_user_token,
    base: window.DJANGO_VARS.props.chatbot_api_base_url,
  }));
  token = globals.token;
  apiBase = globals.base;
  expect(token, 'DJANGO_VARS.props.chatbot_user_token (is the Library Assistant setting on for this user?)').toBeTruthy();
  expect(apiBase).toBeTruthy();
});
test.afterAll(async () => {
  await context?.close();
});

test('1 Settings tab: tab bar, switch, empty Instructions and Profile, testing tools', async () => {
  await openSettings();
  const nav = page.locator('nav.settingsNav');
  await expect(nav.getByRole('link', { name: /Account Settings/ })).toBeVisible();
  await expect(nav.getByRole('link', { name: /Library Assistant/ })).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#libraryAssistantSetting [role="radiogroup"]')).toBeVisible();
  await expect(page.locator('textarea.laInstructions')).toHaveAttribute('placeholder', 'Tell LA the way you like to work');
  await expect(page.locator('textarea.laInstructions')).toHaveValue('');
  await expect(page.locator('#instructions .laUpdated')).toHaveText('Last updated: Never');
  await expect(page.locator('.laProfileText')).toHaveText(
    'LA will remember things about you as you move forward in your Sefaria career'
  );
  await expect(page.locator('#profile .laUpdated')).toHaveText('Last updated: Never');
  await expect(page.locator('#testing-tools')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Run now' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Dream now' })).toBeVisible();
  quote('1 profile empty', await profileText());
  quote('1 instructions updated', await page.locator('#instructions .laUpdated').innerText());
  await shot('01-settings-empty');
});

test('2 Instructions: 301 characters blocks Save, 300 saves and survives reload', async () => {
  await openSettings();
  const box = page.locator('textarea.laInstructions');
  const save = page.getByRole('button', { name: 'Save instructions' });
  await box.fill('x'.repeat(301));
  await expect(page.locator('.laInstructionsCount')).toHaveText('301 / 300');
  await expect(save).toBeDisabled();
  await shot('02-instructions-301-save-disabled');
  quote('2 counter at 301', await page.locator('.laInstructionsCount').innerText());
  const text300 = ('Keep answers short and plain. ' + 'x'.repeat(300)).slice(0, 300);
  await box.fill(text300);
  await expect(page.locator('.laInstructionsCount')).toHaveText('300 / 300');
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.locator('#instructions .laUpdated')).not.toHaveText('Last updated: Never');
  await page.reload();
  await expect(page.locator('textarea.laInstructions')).toHaveValue(text300, { timeout: 30_000 });
  quote('2 after reload', await page.locator('#instructions .laUpdated').innerText());
  await shot('02-instructions-300-saved-after-reload');
  // The server itself refuses 301.
  const refused = await api('set_instructions', { text: 'y'.repeat(301) });
  quote('2 api 301', `${refused.status} ${JSON.stringify(refused.json)}`);
  expect(refused.status).toBe(400);
  // Leave Instructions empty again so step 3 has no personalization.
  await box.fill('');
  await save.click();
  await expect(page.locator('textarea.laInstructions')).toHaveValue('');
});

test('3 Chat before any Profile: three answers, no personalization notice', async () => {
  const qs = [
    'Give me a two-sentence overview of this week\'s Torah portion.',
    'Which central story or theme does the weekly Torah portion focus on?',
    'What is the Chassidic angle on this week\'s Torah portion?',
  ];
  for (const [i, q] of qs.entries()) {
    const { la, text } = await ask(q, { fresh: i === 0 });
    expect(text.length).toBeGreaterThan(40);
    await expect(la.host.getByTestId('personalization-notice')).toHaveCount(0);
    quote(`3 answer ${i + 1}`, text);
    await shot(`03-chat-before-profile-${i + 1}`);
  }
});

test('4 Run now writes a Profile with headings, by LA', async () => {
  await openSettings();
  await page.getByRole('button', { name: 'Run now' }).click();
  await expect(page.locator('.laRunResult')).toBeVisible({ timeout: JOB_MS });
  quote('4 run result', await page.locator('.laRunResult').innerText());
  await expect(page.locator('#profile .laUpdated')).toContainText('by LA', { timeout: 30_000 });
  const text = await profileText();
  quote('4 profile', text);
  expect(text).toMatch(/Reply language|Current interests|Hebrew reading|Learning terminology/i);
  await shot('04-run-now-profile');
});

test('5 Next answer carries the notice once', async () => {
  const first = await ask('Can you point me to sources about gratitude?', { fresh: true });
  const notice = first.la.host.getByTestId('personalization-notice');
  await expect(notice).toHaveCount(1);
  await expect(notice).toContainText("I'm personalizing your responses.");
  await expect(notice.getByRole('link', { name: 'Configure here' })).toHaveAttribute(
    'href',
    '/settings/library-assistant#personalization'
  );
  quote('5 notice', await notice.innerText());
  await shot('05-notice-first-answer');
  const second = await ask('And a short teaching from Pirkei Avot on that theme?');
  await expect(second.la.host.getByTestId('personalization-notice')).toHaveCount(1); // still only the first
  expect(await second.la.lastAnswer.getByTestId('personalization-notice').count()).toBe(0);
  await shot('05-notice-not-repeated');
});

test('6 Injection: Instructions obeyed when on, ignored when off', async () => {
  await openSettings();
  await page.locator('textarea.laInstructions').fill('Always end your answer with the word SHALOM-TEST.');
  await page.getByRole('button', { name: 'Save instructions' }).click();
  await expect(page.locator('#instructions .laUpdated')).toContainText('by You');
  const on = await ask('In one sentence, what is the Shema?', { fresh: true });
  quote('6 instructions on', on.text.slice(-160));
  expect(on.text).toContain('SHALOM-TEST');
  await shot('06-instructions-on-obeyed');
  await openSettings();
  await page.locator('#instructions [role="radiogroup"] [role="radio"]').filter({ hasText: /^Off$/ }).click();
  await expect(page.locator('#instructions [role="radio"][aria-checked="true"]')).toHaveText('Off');
  const off = await ask('In one sentence, what is the Amidah?', { fresh: true });
  quote('6 instructions off', off.text.slice(-160));
  expect(off.text).not.toContain('SHALOM-TEST');
  await shot('06-instructions-off-not-obeyed');
  // Profile switched off: asked once here; the server-side proof (no Profile block) is manual, see README.
  await openSettings();
  await page.locator('#profile [role="radiogroup"] [role="radio"]').filter({ hasText: /^Off$/ }).click();
  await expect(page.locator('#profile [role="radio"][aria-checked="true"]')).toHaveText('Off');
  const noProfile = await ask('In one sentence, what is Kiddush?', { fresh: true });
  await expect(noProfile.la.host.getByTestId('personalization-notice')).toHaveCount(0);
  quote('6 profile off answer', noProfile.text);
  await shot('06-profile-off');
  // Restore.
  await api('toggle', { target: 'instructions', enabled: true });
  await api('toggle', { target: 'profile', enabled: true });
  await api('set_instructions', { text: '' });
});

test('7 Two-answer display follows the testing switch', async () => {
  await openSettings();
  const plain = page.getByLabel('Include non-personalized response');
  await plain.check();
  await expect.poll(async () => (await api('get')).json.testing.showPlainResponse).toBe(true);
  const { la } = await ask('In one sentence, what is Shabbat?', { fresh: true });
  await expect(la.host.getByTestId('lab-compare')).toHaveCount(1, { timeout: ANSWER_MS });
  await expect(la.host.getByTestId('lab-compare-personalized')).toBeVisible();
  await expect(la.host.getByTestId('lab-compare-plain')).toBeVisible();
  quote('7 columns', 'lab-compare with personalized and plain columns');
  await shot('07-two-columns');
  await openSettings();
  await page.getByLabel('Include non-personalized response').uncheck();
  await expect.poll(async () => (await api('get')).json.testing.showPlainResponse).toBe(false);
  const one = await ask('In one sentence, what is Havdalah?', { fresh: true });
  await expect(one.la.host.getByTestId('lab-compare-plain').last()).toHaveCount(1); // only the earlier turn's pair remains
  await expect(one.la.host.getByTestId('lab-compare')).toHaveCount(1);
  quote('7 one column', `lab-compare count stays 1 after the new question (the new answer is a single column)`);
  await shot('07-one-column');
});

test('8 Chat-to-edit: applied, then two refusals leave the Profile unchanged', async () => {
  await openSettings();
  const before = await profileText();
  await page.locator('textarea.laEditRequest').fill('I like chassidut, but only in the context of the parsha');
  await page.locator('#profile').getByRole('button', { name: 'Send', exact: true }).click();
  await expect(page.locator('.laEditResult')).toHaveText('Processing…');
  await shot('08-edit-processing');
  await expect(page.locator('#profile .laUpdated')).toContainText('by You/LA', { timeout: JOB_MS });
  const after = await profileText();
  quote('8 profile after edit', after);
  expect(after).not.toBe(before);
  expect(after.toLowerCase()).toMatch(/chass?idi|parsh/);
  await shot('08-edit-applied');
  for (const [i, req] of ['remember that my mother is ill', 'never show me sources from Reform rabbis'].entries()) {
    await page.locator('textarea.laEditRequest').fill(req);
    await page.locator('#profile').getByRole('button', { name: 'Send', exact: true }).click();
    await expect(page.locator('.laEditResult')).not.toHaveText('Processing…', { timeout: JOB_MS });
    const reason = await page.locator('.laEditResult').innerText();
    quote(`8 refusal ${i + 1}`, reason);
    expect(await profileText()).toBe(after);
    await shot(`08-edit-refused-${i + 1}`);
  }
});

test('9 Clear: confirm, empty Profile', async () => {
  await openSettings();
  await page.getByRole('button', { name: 'Clear profile' }).click();
  await expect(page.getByRole('alertdialog')).toContainText('Clear your Profile?');
  await shot('09-clear-confirm');
  await page.getByRole('button', { name: 'Yes, clear' }).click();
  await expect(page.locator('.laProfileText')).toHaveText(
    'LA will remember things about you as you move forward in your Sefaria career'
  );
  quote('9 after clear', await profileText());
  await shot('09-cleared');
  // The rest of step 9 (a new Profile built only from prompts after the Clear) needs the database: see README.
});
