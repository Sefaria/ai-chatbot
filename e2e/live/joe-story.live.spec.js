// LIVE end-to-end check of the "Joe" story against a deployed cauldron + preview chatbot server
// (17 steps). Real browser, real model calls, real users. Skipped unless LIVE=1. See README.md.
//
// Every step records PASS / FAIL (with the evidence it saw) into a results JSON file and never aborts
// the run, so one failing step does not hide the others. The last test fails if any step failed.
// A step that you rerun and that then passes is, by the report's rule, "FAILED, THEN RECOVERED": the
// script cannot know that, so the results file keeps every attempt appended (LIVE_RESULTS_FILE).
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { AssistantPage } from '../pages/assistantPage.js';

const SEFARIA = (process.env.LIVE_SEFARIA_URL || 'https://www.la-p13n.cauldron.sefaria.org').replace(/\/$/, '');
const CHATBOT_VERSION = process.env.LIVE_CHATBOT_VERSION || '9298';
const API_BASE = (process.env.LIVE_CHATBOT_API || `https://${CHATBOT_VERSION}.ai-server.coolifydev.sefaria.org/api`).replace(/\/$/, '');
const CHAT_PATH = process.env.LIVE_CHAT_PATH || '/Genesis.1';
const SHOTS = process.env.LIVE_SHOTS_DIR || path.resolve('test-results/live-shots');
const RESULTS = process.env.LIVE_RESULTS_FILE || path.join(SHOTS, 'results.json');
const ONLY = (process.env.LIVE_ONLY || '').split(',').map((s) => s.trim()).filter(Boolean);
// Step 16 (idle sweep): wait this long after the question before the first look, then poll until the give-up point.
const IDLE_FIRST_LOOK_MIN = Number(process.env.LIVE_IDLE_FIRST_LOOK_MIN || 36);
const IDLE_GIVE_UP_MIN = Number(process.env.LIVE_IDLE_GIVE_UP_MIN || 46);
const SETTINGS = `${SEFARIA}/settings/library-assistant`;
const ANSWER_MS = 3 * 60 * 1000;
const JOB_MS = 3 * 60 * 1000;
const EMPTY_PROFILE = 'LA will remember things about you as you move forward in your Sefaria career';

test.describe.configure({ mode: 'serial' });
test.skip(!process.env.LIVE, 'Live run: set LIVE=1 (spends model calls against the deployed servers)');

const creds = (n) => {
  const email = process.env[`LIVE_USER${n}_EMAIL`];
  const password = process.env[`LIVE_USER${n}_PASSWORD`];
  if (!email || !password) throw new Error(`Set LIVE_USER${n}_EMAIL and LIVE_USER${n}_PASSWORD`);
  return { email, password };
};

const observed = { console: [], failed: [], hosts: new Set(), widgetScripts: new Set(), stalls: [] };
const inflightStreams = new Set();
let stallsThisStep = 0;
const sessions = {}; // n -> {context, page, token}
const results = [];
const flush = () => {
  fs.mkdirSync(path.dirname(RESULTS), { recursive: true });
  const prior = fs.existsSync(RESULTS) ? JSON.parse(fs.readFileSync(RESULTS, 'utf8')) : { attempts: [] };
  prior.attempts.push(...results.splice(0));
  prior.observed = {
    stalls: observed.stalls,
    console: observed.console.slice(0, 80),
    failed: observed.failed.slice(0, 80),
    hosts: [...observed.hosts].sort(),
    widgetScripts: [...observed.widgetScripts],
  };
  fs.writeFileSync(RESULTS, JSON.stringify(prior, null, 2));
};

let evidence = [];
const quote = (label, text) => {
  const line = `${label}: ${String(text).replace(/\s+/g, ' ').slice(0, 600)}`;
  evidence.push(line);
  console.log(`[evidence] ${line}`);
};
const SKIP = (process.env.LIVE_SKIP || '').split(',').map((s) => s.trim()).filter(Boolean);
const stepIf = (id) => (!ONLY.length || ONLY.includes(String(id))) && !SKIP.includes(String(id));
const step = (id, title, fn) =>
  test(`${id} ${title}`, async ({ browser }) => {
    if (!stepIf(id)) test.skip(true, 'not selected by LIVE_ONLY');
    evidence = [];
    stallsThisStep = 0;
    const started = new Date().toISOString();
    let status = 'PASS';
    let error = null;
    try {
      await fn(browser);
    } catch (e) {
      status = 'FAIL';
      error = String(e.message || e).split('\n').slice(0, 6).join(' | ');
      console.log(`[FAIL] step ${id}: ${error}`);
    }
    if (status === 'PASS' && stallsThisStep > 0) status = 'FAILED, THEN RECOVERED';
    results.push({ step: id, title, status, started, ended: new Date().toISOString(), evidence, error });
    flush();
  });

const trace = (m) => { if (process.env.LIVE_TRACE) fs.appendFileSync(process.env.LIVE_TRACE, `${new Date().toISOString()} ${m}\n`); };
const shot = async (page, name) => {
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`), fullPage: true });
};

const watch = (page, n) => {
  page.on('console', (m) => {
    if (m.type() === 'error') observed.console.push(`user${n} ${page.url().replace(SEFARIA, '')}: ${m.text().slice(0, 220)}`);
  });
  page.on('pageerror', (e) => observed.console.push(`user${n} PAGEERROR: ${String(e).slice(0, 220)}`));
  page.on('requestfinished', (r) => { if (/chat\/stream/.test(r.url())) inflightStreams.delete(r.url()); });
  page.on('requestfailed', (r) => observed.failed.push(`user${n} ${r.url().slice(0, 140)} ${r.failure()?.errorText}`));
  page.on('response', (r) => {
    if (r.status() >= 400) observed.failed.push(`user${n} ${r.status()} ${r.url().slice(0, 140)}`);
  });
  page.on('request', (r) => {
    if (/chat\/stream/.test(r.url())) inflightStreams.add(r.url());
    try {
      const u = new URL(r.url());
      observed.hosts.add(u.host);
      if (/lc-chatbot/.test(u.pathname)) observed.widgetScripts.add(r.url().replace(/\?.*$/, ''));
    } catch {}
  });
};

/** Open a URL; a cauldron that is scaling up shows "Environment is starting up" or 503: wait, up to 5 minutes. */
const gotoAwake = async (page, url, opts = {}) => {
  const deadline = Date.now() + 5 * 60 * 1000;
  for (;;) {
    const res = await page.goto(url, { waitUntil: 'domcontentloaded', ...opts }).catch(() => null);
    const starting = !res || res.status() === 503 || /Environment is starting up/i.test(await page.content().catch(() => ''));
    if (!starting) return res;
    if (Date.now() > deadline) throw new Error(`site did not wake within 5 minutes: ${url}`);
    await page.waitForTimeout(10_000);
  }
};

/** Sign in through the normal login page, then open a page with ?chatbot_version once (the site keeps it in the session). */
const signIn = async (browser, n) => {
  if (sessions[n]) return sessions[n];
  const { email, password } = creds(n);
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  watch(page, n);
  await gotoAwake(page, `${SEFARIA}/login?next=/about`);
  await page.getByRole('button', { name: 'OK', exact: true }).click().catch(() => {});
  await page.getByText('Continue with Email').click();
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.locator('input[name="password"]').press('Enter');
  await page.waitForURL((u) => u.pathname === '/about', { timeout: 60_000 });
  await gotoAwake(page, `${SEFARIA}/about?chatbot_version=${CHATBOT_VERSION}`);
  await page.waitForFunction(() => window.DJANGO_VARS?.props?.chatbot_user_token, null, { timeout: 30_000 });
  const token = await page.evaluate(() => window.DJANGO_VARS.props.chatbot_user_token);
  sessions[n] = { context, page, token };
  return sessions[n];
};
const signOut = async (n) => {
  const s = sessions[n];
  if (!s) return;
  await s.page.goto(`${SEFARIA}/logout`).catch(() => {});
  await s.context.close().catch(() => {});
  delete sessions[n];
};

/** A call to the personalization settings API with the page's own signed token (never printed). */
const api = async (s, action, body = {}) => {
  const res = await s.page.request.post(`${API_BASE}/v2/personalization/settings`, { data: { userId: s.token, action, ...body } });
  return { status: res.status(), json: await res.json().catch(() => null) };
};

const radio = (page, scope, label) => page.locator(`${scope} [role="radiogroup"]`).first().getByRole('radio', { name: label, exact: true });
const profileText = (page) => page.locator('.laProfileText').innerText();
const updated = (page, card) => page.locator(`#${card} .laUpdated`).innerText();

const openSettings = async (s) => {
  await gotoAwake(s.page, SETTINGS);
  await expect(s.page.locator('#personalization')).toBeVisible({ timeout: 30_000 });
  await expect(s.page.locator('#instructions')).toBeVisible({ timeout: 30_000 });
  await expect(s.page.locator('.laProfileText')).toBeVisible({ timeout: 30_000 });
  await closeWidget(s.page);
};
const closeWidget = async (page) => {
  const la = new AssistantPage(page);
  if (await la.panel.isVisible().catch(() => false)) await la.close().catch(() => {});
};

/** Click Run now / Dream now and wait until the result line shows; returns its text. */
const runJob = async (s, name) => {
  await openSettings(s);
  await s.page.locator('#testing-tools').getByRole('button', { name, exact: true }).click();
  const result = s.page.locator('.laRunResult');
  await expect(result).toBeVisible({ timeout: JOB_MS });
  await expect(result).not.toHaveText(/^(Running|Processing)/i, { timeout: JOB_MS });
  let text = (await result.innerText()).trim();
  // "busy" means another job holds the user's lock (for example one a new chat started): wait and press again, and say so.
  for (let i = 0; i < 6 && /^busy/i.test(text); i++) {
    evidence.push(`${name} replied "${text}" (another job was running); pressed again after 30 s`);
    await s.page.waitForTimeout(30_000);
    await s.page.locator('#testing-tools').getByRole('button', { name, exact: true }).click();
    await expect(result).not.toHaveText(/^(Running|Processing)/i, { timeout: JOB_MS });
    text = (await result.innerText()).trim();
  }
  return text;
};

/** Open a Library page, open the assistant, send a question, wait until the answer has finished streaming. */
const ask = async (s, question, opts = {}) => {
  try {
    return await askOnce(s, question, opts);
  } catch (e) {
    // A turn that never finishes (input stays disabled) is recorded as a stall with a screenshot, then asked once more on a
    // reloaded page. The step then ends as "FAILED, THEN RECOVERED" and never as a plain PASS.
    const stall = `STALL on "${question.slice(0, 50)}": ${String(e.message).split('\n')[0].slice(0, 120)}; chat/stream still open: ${inflightStreams.size > 0}`;
    observed.stalls.push(`${new Date().toISOString()} ${stall}`);
    evidence.push(stall);
    stallsThisStep++;
    trace(`stall: ${stall}`);
    await shot(s.page, `stall-${Date.now()}`).catch(() => {});
    trace('stall shot done');
    inflightStreams.clear();
    // After a stall the widget can stay disabled even after a reload (the server still counts the turn as running).
    // Give it a minute to recover on its own; otherwise start a new chat from the menu, and say which route was needed.
    await gotoAwake(s.page, `${SEFARIA}${CHAT_PATH}`);
    const la = new AssistantPage(s.page);
    await la.open();
    const recovered = await la.input.isEnabled({ timeout: 1000 }).catch(() => false) || await expect(la.input).toBeEnabled({ timeout: 60_000 }).then(() => true, () => false);
    if (!recovered) {
      await la.startNewChat();
      evidence.push('input stayed disabled after reload for 60 s: had to start a new chat from the menu');
      trace('needed new chat');
    } else evidence.push('input became enabled again after reload');
    return await askOnce(s, question, { ...opts, fresh: false });
  }
};
const askOnce = async (s, question, { fresh = false, compare = false } = {}) => {
  trace(`ask start: ${question.slice(0, 40)}`);
  const page = s.page;
  if (fresh || !page.url().includes(CHAT_PATH)) await gotoAwake(page, `${SEFARIA}${CHAT_PATH}`);
  const la = new AssistantPage(page);
  await expect(la.host).toHaveCount(1, { timeout: 45_000 });
  await la.open();
  const before = await la.answers.count();
  const compareBefore = await la.host.getByTestId('lab-compare').count();
  trace('sending');
  await la.send(question);
  trace('sent');
  if (compare) await expect(la.host.getByTestId('lab-compare')).toHaveCount(compareBefore + 1, { timeout: ANSWER_MS });
  else await expect(la.answers).toHaveCount(before + 1, { timeout: ANSWER_MS });
  trace('answer element appeared');
  await expect(la.host.locator('.lc-streaming-content')).toHaveCount(0, { timeout: ANSWER_MS });
  trace('streaming gone');
  await expect(la.input).toBeEnabled({ timeout: ANSWER_MS });
  // The text is final when two reads one second apart agree.
  let last = '';
  await expect
    .poll(async () => {
      const now = compare ? await la.host.getByTestId('lab-compare').last().innerText() : await la.lastAnswer.innerText();
      const same = now === last && now.length > 0;
      last = now;
      return same;
    }, { timeout: 30_000, intervals: [1000] })
    .toBe(true);
  expect(last, 'a real answer, not the failure apology').not.toMatch(/wasn't able to process your message/i);
  return { la, text: last };
};
const words = (t, n = 15) => t.replace(/\s+/g, ' ').trim().split(' ').slice(0, n).join(' ');
const NOTICE = 'personalization-notice';
const lastAnswerNotice = (la) => la.lastAnswer.getByTestId(NOTICE);

let q1;
let idle = {};
test.afterAll(async () => {
  flush();
});


// ---------------------------------------------------------------------------------------------- 1
step(1, 'Settings tab: tab bar, five cards, switch, empty Instructions and Profile, testing tools, screenshots', async (browser) => {
  const s = await signIn(browser, 1);
  const p = s.page;
  await openSettings(s);
  await expect(p.getByRole('link', { name: 'Account Settings' }).first()).toBeVisible();
  const laTab = p.getByRole('link', { name: 'Library Assistant' }).filter({ has: p.locator(':scope') });
  await expect(p.locator('[aria-current="page"]').filter({ hasText: 'Library Assistant' }).first()).toBeVisible();
  void laTab;
  await expect(p.locator('#libraryAssistantSetting')).toBeVisible();
  await expect(p.locator('#libraryAssistantSetting [role="radiogroup"]')).toBeVisible();
  await expect(p.locator('#personalization .laIntro')).toBeVisible();
  await expect(p.locator('#instructions')).toBeVisible();
  await expect(p.locator('#profile')).toBeVisible();
  await expect(p.locator('#testing-tools')).toBeVisible();
  await expect(p.locator('textarea.laInstructions')).toHaveAttribute('placeholder', 'Tell LA the way you like to work');
  await expect(p.locator('textarea.laInstructions')).toHaveValue('');
  await expect(p.locator('#instructions .laUpdated')).toHaveText('Last updated: Never');
  await expect(p.locator('.laProfileText')).toHaveText(EMPTY_PROFILE);
  await expect(p.getByRole('button', { name: 'Run now' })).toBeVisible();
  quote('cards', 'Library Assistant / Personalization / Instructions / Profile / Testing tools all visible');
  quote('instructions', `placeholder "Tell LA the way you like to work", ${await updated(p, 'instructions')}`);
  quote('profile', await profileText(p));
  await shot(p, '01-settings-1280');
  await p.setViewportSize({ width: 400, height: 900 });
  await p.waitForTimeout(500);
  await shot(p, '01-settings-400');
  const overflow = await p.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  quote('400px horizontal overflow (px)', overflow);
  await p.setViewportSize({ width: 1280, height: 900 });
});

// ---------------------------------------------------------------------------------------------- 2
step(2, 'Instructions: 301 characters cannot be saved, 300 saves, date shows after reload, then empty', async (browser) => {
  const s = await signIn(browser, 1);
  const p = s.page;
  await openSettings(s);
  const box = p.locator('textarea.laInstructions');
  const save = p.getByRole('button', { name: 'Save instructions' });
  await box.fill('x'.repeat(301));
  await expect(p.locator('.laInstructionsCount')).toHaveText('301 / 300');
  await expect(save).toBeDisabled();
  quote('301', `${await p.locator('.laInstructionsCount').innerText()}; Save instructions disabled`);
  await shot(p, '02-instructions-301');
  const refused = await api(s, 'set_instructions', { text: 'y'.repeat(301) });
  quote('api 301', `${refused.status} ${JSON.stringify(refused.json)}`);
  expect(refused.status).toBe(400);
  const text300 = ('Keep answers short and plain. ' + 'x'.repeat(300)).slice(0, 300);
  await box.fill(text300);
  await expect(p.locator('.laInstructionsCount')).toHaveText('300 / 300');
  await expect(save).toBeEnabled();
  await save.click();
  await expect(p.locator('#instructions .laUpdated')).not.toHaveText('Last updated: Never', { timeout: 30_000 });
  await p.reload();
  await expect(p.locator('textarea.laInstructions')).toHaveValue(text300, { timeout: 30_000 });
  quote('300 after reload', await updated(p, 'instructions'));
  await shot(p, '02-instructions-300-after-reload');
  await p.locator('textarea.laInstructions').fill('');
  await p.getByRole('button', { name: 'Save instructions' }).click();
  await expect.poll(async () => (await api(s, 'get')).json.instructions.text).toBe('');
  quote('reset', 'Instructions empty again');
});

// ---------------------------------------------------------------------------------------------- 3
step(3, 'Chat before any Profile: three answers (one Chassidic), no notice', async (browser) => {
  const s = await signIn(browser, 1);
  const qs = [
    "Give me a two-sentence overview of this week's Torah portion.",
    'Which central story or theme does the weekly Torah portion focus on?',
    "I'm new to Chassidic thought: what is the Chassidic angle on this week's Torah portion? Please answer in English, kept simple.",
  ];
  for (const [i, q] of qs.entries()) {
    const { la, text } = await ask(s, q, { fresh: i === 0 });
    expect(text.length).toBeGreaterThan(40);
    await expect(lastAnswerNotice(la)).toHaveCount(0);
    quote(`answer ${i + 1} (first 15 words)`, words(text));
    await shot(s.page, `03-chat-before-profile-${i + 1}`);
  }
  quote('notice', 'none shown on any of the three answers');
});

// ---------------------------------------------------------------------------------------------- 4
step(4, 'Run now writes a Profile (headings, "by LA")', async (browser) => {
  const s = await signIn(browser, 1);
  const result = await runJob(s, 'Run now');
  quote('run result line', result);
  await expect(s.page.locator('#profile .laUpdated')).toContainText('by LA', { timeout: 30_000 });
  const text = await profileText(s.page);
  quote('profile', text);
  quote('updated line', await updated(s.page, 'profile'));
  expect(text).not.toBe(EMPTY_PROFILE);
  await shot(s.page, '04-run-now-profile');
});

// ---------------------------------------------------------------------------------------------- 5
step(5, 'Next answer shows the notice with two links; the one after does not', async (browser) => {
  const s = await signIn(browser, 1);
  const first = await ask(s, 'Can you point me to sources about gratitude?', { fresh: true });
  const notice = lastAnswerNotice(first.la);
  await expect(notice).toHaveCount(1);
  await expect(notice).toContainText("I'm personalizing your responses.");
  const links = await notice.getByRole('link').evaluateAll((as) => as.map((a) => `${a.textContent.trim()} -> ${a.getAttribute('href')}`));
  quote('notice', await notice.innerText());
  quote('links', links.join(' ; '));
  expect(links.length).toBe(2);
  await shot(s.page, '05-notice-first');
  const second = await ask(s, 'And one short teaching from Pirkei Avot on that theme?');
  await expect(lastAnswerNotice(second.la)).toHaveCount(0);
  quote('second answer', `no notice; first 15 words: ${words(second.text)}`);
  await shot(s.page, '05-notice-not-repeated');
});

// ---------------------------------------------------------------------------------------------- 6
step(6, 'Injection: Instructions obeyed when on, ignored when off', async (browser) => {
  const s = await signIn(browser, 1);
  const p = s.page;
  await openSettings(s);
  await p.locator('textarea.laInstructions').fill('Always end your answer with the word SHALOM-TEST.');
  await p.getByRole('button', { name: 'Save instructions' }).click();
  await expect(p.locator('#instructions .laUpdated')).toContainText('by You', { timeout: 30_000 });
  const on = await ask(s, 'In one sentence, what is the Shema?', { fresh: true });
  quote('on: end of answer', on.text.slice(-120));
  expect(on.text).toContain('SHALOM-TEST');
  await shot(p, '06-instructions-on');
  await openSettings(s);
  await radio(p, '#instructions', 'Off').click();
  await expect(radio(p, '#instructions', 'Off')).toHaveAttribute('aria-checked', 'true');
  await expect.poll(async () => (await api(s, 'get')).json.instructions.enabled).toBe(false);
  const off = await ask(s, 'In one sentence, what is the Amidah?', { fresh: true });
  quote('off: end of answer', off.text.slice(-120));
  expect(off.text).not.toContain('SHALOM-TEST');
  await shot(p, '06-instructions-off');
  await openSettings(s);
  await radio(p, '#instructions', 'On').click();
  await p.locator('textarea.laInstructions').fill('');
  await p.getByRole('button', { name: 'Save instructions' }).click();
  await expect.poll(async () => JSON.stringify((await api(s, 'get')).json.instructions)).toMatch(/"text":"","enabled":true/);
  quote('reset', 'Instructions empty and on');
});

// ---------------------------------------------------------------------------------------------- 7
step(7, 'Two-answer display follows the testing switch', async (browser) => {
  const s = await signIn(browser, 1);
  const p = s.page;
  await openSettings(s);
  await p.locator('[aria-labelledby="laPlainResponseLabel"]').getByRole('radio', { name: 'On', exact: true }).click();
  await expect.poll(async () => (await api(s, 'get')).json.testing.showPlainResponse).toBe(true);
  const two = await ask(s, 'In one sentence, what is Shabbat?', { fresh: true, compare: true });
  await expect(two.la.host.getByTestId('lab-compare-personalized').last()).toBeVisible();
  await expect(two.la.host.getByTestId('lab-compare-plain').last()).toBeVisible();
  quote('two columns', `personalized and plain columns visible: ${words(two.text, 20)}`);
  await shot(p, '07-two-columns');
  await openSettings(s);
  await p.locator('[aria-labelledby="laPlainResponseLabel"]').getByRole('radio', { name: 'Off', exact: true }).click();
  await expect.poll(async () => (await api(s, 'get')).json.testing.showPlainResponse).toBe(false);
  // A reload of the chat page, then one question: a single column (no new lab-compare block).
  await gotoAwake(p, `${SEFARIA}${CHAT_PATH}`);
  const la = new AssistantPage(p);
  await la.open();
  const compareBefore = await la.host.getByTestId('lab-compare').count();
  const one = await ask(s, 'In one sentence, what is Havdalah?');
  expect(await la.host.getByTestId('lab-compare').count()).toBe(compareBefore);
  quote('one column', `no new two-column block (lab-compare count ${compareBefore} -> ${await la.host.getByTestId('lab-compare').count()}); answer: ${words(one.text, 15)}`);
  await shot(p, '07-one-column');
});

// ---------------------------------------------------------------------------------------------- 8
const editRequest = async (p, text) => {
  await p.locator('textarea.laEditRequest').fill(text);
  await p.locator('#profile').getByRole('button', { name: 'Send', exact: true }).click();
};
step(8, 'Chat-to-edit: applied, then two refusals leave the Profile unchanged', async (browser) => {
  const s = await signIn(browser, 1);
  const p = s.page;
  await openSettings(s);
  const before = await profileText(p);
  const beforeLine = await updated(p, 'profile');
  quote('before', `${before} || ${beforeLine}`);
  await editRequest(p, 'I like chassidut, but only in the context of the parsha');
  await expect(p.locator('#profile')).toContainText('Processing…');
  await shot(p, '08-processing');
  await expect(p.locator('#profile')).not.toContainText('Processing…', { timeout: JOB_MS }).catch(() => quote('still "Processing…" after 3 minutes', 'yes'));
  quote('edit result shown', (await p.locator('#profile .laEditResult, #profile [role="status"], #profile [role="alert"]').allInnerTexts()).join(' | ') || '(none)');
  await shot(p, '08-after-edit');
  await expect(p.locator('#profile .laUpdated')).toContainText('by You/LA', { timeout: 20_000 });
  const after = await profileText(p);
  quote('after', `${after} || ${await updated(p, 'profile')}`);
  expect(after).not.toBe(before);
  await shot(p, '08-applied');
  for (const [i, req] of ['Please remember that my mother has been seriously ill with cancer this year.', 'Never show me sources from the Chabad movement.'].entries()) {
    await editRequest(p, req);
    await expect(p.locator('#profile')).not.toContainText('Processing…', { timeout: JOB_MS });
    const resultText = await p.locator('#profile .laEditResult, #profile [role="status"], #profile [role="alert"]').allInnerTexts();
    quote(`refusal ${i + 1} (${req.slice(0, 30)})`, resultText.join(' | ') || '(no result element found)');
    expect(await profileText(p)).toBe(after);
    expect(resultText.join('').length, 'a reason is shown').toBeGreaterThan(5);
    await shot(p, `08-refused-${i + 1}`);
  }
});

// ---------------------------------------------------------------------------------------------- 9
step(9, 'Sensitive content: a made-up family illness does not reach the Profile', async (browser) => {
  const s = await signIn(browser, 1);
  await ask(s, 'My uncle Dov has a made-up illness called zorbitis and I want to visit him: what do the sources say about visiting the sick?', { fresh: true });
  const result = await runJob(s, 'Run now');
  quote('run result line', result);
  const text = await profileText(s.page);
  quote('profile', text);
  expect(text).not.toMatch(/zorbitis|uncle|dov|illness|sick|ill\b/i);
  await shot(s.page, '09-sensitive-profile');
});

// ---------------------------------------------------------------------------------------------- 10
step(10, 'Hebrew questions: what the Profile says about reply language and the run outcome', async (browser) => {
  const s = await signIn(browser, 1);
  await ask(s, 'מה עיקר הסיפור בפרשת השבוע?', { fresh: true });
  await ask(s, 'תוכל להציע לי מקורות על הכרת הטוב?');
  const result = await runJob(s, 'Run now');
  quote('run result line', result);
  const text = await profileText(s.page);
  quote('profile', text);
  const lang = await s.page.locator('.laProfileText').evaluate((el) => {
    const t = el.innerText;
    const m = t.match(/Reply language[\s\S]*?(?=\n\s*\n|$)/i);
    return m ? m[0] : '(no Reply language heading)';
  });
  quote('reply-language section', lang);
  await shot(s.page, '10-hebrew-profile');
});

// ---------------------------------------------------------------------------------------------- 11
step(11, 'Jev gate (outside view): a plain factual question, then compare run statuses', async (browser) => {
  const s = await signIn(browser, 1);
  await ask(s, 'How many chapters are in the book of Genesis?', { fresh: true });
  const r = await api(s, 'run_now', { kind: 'session' });
  quote('api run (factual only)', JSON.stringify(r.json?.run));
  quote('gate source', r.json?.run && 'source' in r.json.run ? r.json.run.source : 'gate source not observable from outside');
  const keys = Object.keys(r.json?.run || {}).join(',');
  quote('run object keys', keys);
  await openSettings(s);
  await shot(s.page, '11-factual-run');
});

// ---------------------------------------------------------------------------------------------- 12
step(12, 'Clear: confirm dialog, empty Profile, then a new Profile from post-Clear prompts only', async (browser) => {
  const s = await signIn(browser, 1);
  const p = s.page;
  await openSettings(s);
  const before = await profileText(p);
  quote('profile before clear', before);
  await p.getByRole('button', { name: 'Clear profile' }).click();
  await expect(p.getByRole('alertdialog')).toBeVisible();
  quote('dialog', await p.getByRole('alertdialog').innerText());
  await shot(p, '12-clear-confirm');
  await p.getByRole('button', { name: 'Yes, clear' }).click();
  await expect(p.locator('.laProfileText')).toHaveText(EMPTY_PROFILE, { timeout: 30_000 });
  quote('after clear', await profileText(p));
  await shot(p, '12-cleared');
  await ask(s, "I'd like to learn Ramban's commentary on Bereshit in simple English, with short answers.", { fresh: true });
  const result = await runJob(s, 'Run now');
  quote('run result line', result);
  const after = await profileText(p);
  quote('new profile', after);
  // Judged by content: terms that only earlier (pre-Clear) prompts or edits introduced must be absent.
  const stale = ['gratitude', 'shabbat', 'havdalah', 'shema', 'amidah', 'chassid', 'chasid', 'parsha', 'zorbitis', 'pirkei'].filter((w) => after.toLowerCase().includes(w));
  quote('stale pre-Clear terms found', stale.length ? stale.join(', ') : 'none');
  expect(after).not.toBe(EMPTY_PROFILE);
  expect(stale).toEqual([]);
  await shot(p, '12-new-profile');
});

// ---------------------------------------------------------------------------------------------- 13
step(13, 'Dream now runs and reports a status; job health shows both jobs', async (browser) => {
  const s = await signIn(browser, 1);
  const result = await runJob(s, 'Dream now');
  quote('dream result line', result);
  await s.page.locator('#testing-tools').getByRole('button', { name: 'Check job health' }).click();
  await expect(s.page.locator('#testing-tools')).toContainText(/last run/i, { timeout: 30_000 });
  const health = await s.page.locator('#testing-tools').innerText();
  quote('job health', health.slice(health.indexOf('Job health')));
  const h = await api(s, 'health');
  quote('api health', JSON.stringify(h.json));
  expect((h.json?.jobs || []).length).toBeGreaterThanOrEqual(2);
  await shot(s.page, '13-dream-health');
});

// ---------------------------------------------------------------------------------------------- 15
step(15, 'Profile off: no notice, Run now does nothing', async (browser) => {
  const s = await signIn(browser, 1);
  const p = s.page;
  await openSettings(s);
  await radio(p, '#profile', 'Off').click();
  await expect(radio(p, '#profile', 'Off')).toHaveAttribute('aria-checked', 'true');
  await expect.poll(async () => (await api(s, 'get')).json.profile.enabled).toBe(false);
  try {
    const a = await ask(s, 'Can you suggest a verse about patience?', { fresh: true });
    await expect(lastAnswerNotice(a.la)).toHaveCount(0);
    quote('answer', `no notice; first 15 words: ${words(a.text)}`);
    const result = await runJob(s, 'Run now');
    quote('run result line', result);
    expect(result).toMatch(/profile_off|off|nothing|not run|skipped/i);
    await shot(p, '15-profile-off-run');
  } finally {
    await openSettings(s);
    await radio(p, '#profile', 'On').click();
    await expect.poll(async () => (await api(s, 'get')).json.profile.enabled).toBe(true);
    quote('restore', 'Profile switched back on');
  }
});

// ------------------------------------------------------------------------------------ 16, part 1
step('16a', 'Idle sweep: one profile-relevant question, then user 1 does nothing', async (browser) => {
  const s = await signIn(browser, 1);
  idle.beforeState = (await api(s, 'get')).json;
  idle.askedAt = new Date();
  const a = await ask(s, "I'm preparing a short talk on Rashi's commentary to Genesis 1: please keep answers brief and always name your sources.", { fresh: true });
  idle.askedAt = new Date();
  idle.rev0 = idle.beforeState.profile.revision;
  idle.updated0 = idle.beforeState.profile.updatedAt;
  idle.text0 = idle.beforeState.profile.text;
  quote('question sent (UTC)', idle.askedAt.toISOString());
  quote('profile before', `revision ${idle.rev0}, updatedAt ${idle.updated0}`);
  quote('answer', words(a.text));
  fs.mkdirSync(SHOTS, { recursive: true });
  fs.writeFileSync(path.join(SHOTS, 'idle-start.json'), JSON.stringify({ askedAt: idle.askedAt, rev0: idle.rev0, updated0: idle.updated0 }));
  await signOut(1); // user 1 does nothing more until the check
});

// ------------------------------------------------------------------------------------------- 14
step(14, 'Isolation: user 2 sees an empty state and none of user 1 text', async (browser) => {
  const s = await signIn(browser, 2);
  await openSettings(s);
  await expect(s.page.locator('textarea.laInstructions')).toHaveValue('');
  await expect(s.page.locator('.laProfileText')).toHaveText(EMPTY_PROFILE);
  quote('user 2 page', `Instructions empty, Profile: "${await profileText(s.page)}", ${await updated(s.page, 'instructions')}`);
  const r = await api(s, 'get');
  quote('api get as user 2', JSON.stringify(r.json));
  expect(r.status).toBe(200);
  expect(r.json.instructions.text).toBe('');
  expect(r.json.profile.text).toBe('');
  expect(r.json.profile.revision).toBe(0);
  const dump = JSON.stringify(r.json).toLowerCase();
  for (const w of ['shalom-test', 'ramban', 'rashi', 'chass', 'gratitude']) expect(dump).not.toContain(w);
  await shot(s.page, '14-user2-empty');
});

// ------------------------------------------------------------------------------------------- 17
const tabTo = async (p, selector) => {
  await p.evaluate(() => { document.activeElement?.blur(); window.scrollTo(0, 0); });
  await p.locator('body').click({ position: { x: 2, y: 2 } }).catch(() => {});
  for (let i = 1; i <= 160; i++) {
    await p.keyboard.press('Tab');
    const inside = await p.evaluate((sel) => { let el = document.activeElement; while (el?.shadowRoot?.activeElement) el = el.shadowRoot.activeElement; return !!el?.closest(sel); }, selector);
    if (inside) return i;
  }
  throw new Error(`focus never reached ${selector} in 160 Tab presses`);
};
step(17, 'Keyboard: Tab to the switches, arrow key changes them (user 2)', async (browser) => {
  const s = await signIn(browser, 2);
  const p = s.page;
  await openSettings(s);
  const checked = (scope) => p.locator(`${scope} [role="radio"][aria-checked="true"]`).innerText();
  // Library Assistant switch: never Saved.
  const tabs1 = await tabTo(p, '#libraryAssistantSetting [role="radiogroup"]');
  const start1 = await checked('#libraryAssistantSetting');
  await p.keyboard.press('ArrowRight');
  const moved1 = await checked('#libraryAssistantSetting');
  await shot(p, '17-keyboard-la-switch');
  quote('Library Assistant switch', `reached after ${tabs1} Tab presses; ${start1} -> ${moved1} with ArrowRight`);
  await p.keyboard.press('ArrowLeft');
  const back1 = await checked('#libraryAssistantSetting');
  quote('restored (not saved)', back1);
  expect(moved1).not.toBe(start1);
  expect(back1).toBe(start1);
  expect((await api(s, 'get')).status).toBe(200);
  // Instructions switch: saves on change; restore to On.
  const tabs2 = await tabTo(p, '#instructions [role="radiogroup"]');
  const start2 = await checked('#instructions');
  await p.keyboard.press('ArrowRight');
  // This switch saves on change: the selected option follows once the server answers.
  await expect.poll(() => checked('#instructions'), { timeout: 15_000 }).not.toBe(start2);
  const moved2 = await checked('#instructions');
  await expect.poll(async () => (await api(s, 'get')).json.instructions.enabled).toBe(false);
  await shot(p, '17-keyboard-instructions-switch');
  quote('Instructions switch', `reached after ${tabs2} Tab presses; ${start2} -> ${moved2}; API enabled=false`);
  await p.keyboard.press('ArrowLeft');
  await expect.poll(() => checked('#instructions'), { timeout: 15_000 }).toBe(start2);
  await expect.poll(async () => (await api(s, 'get')).json.instructions.enabled).toBe(true);
  quote('restored', `${await checked('#instructions')}; API enabled=true`);
  expect(moved2).not.toBe(start2);
});

// ------------------------------------------------------------------------------------ 16, part 2
step('16b', 'Idle sweep: Profile changed on its own 36 to 45 minutes later; job health shows a session run', async (browser) => {
  if (!idle.askedAt) {
    const saved = JSON.parse(fs.readFileSync(path.join(SHOTS, 'idle-start.json'), 'utf8'));
    idle = { askedAt: new Date(saved.askedAt), rev0: saved.rev0, updated0: saved.updated0 };
  }
  const waitUntil = idle.askedAt.getTime() + IDLE_FIRST_LOOK_MIN * 60_000;
  const giveUp = idle.askedAt.getTime() + IDLE_GIVE_UP_MIN * 60_000;
  // The deliberate idle wait: no browser activity as user 1.
  while (Date.now() < waitUntil) await new Promise((r) => setTimeout(r, Math.min(30_000, waitUntil - Date.now())));
  const s = await signIn(browser, 1);
  let changed = false;
  let state;
  for (;;) {
    await openSettings(s);
    state = (await api(s, 'get')).json;
    changed = state.profile.revision !== idle.rev0 || state.profile.updatedAt !== idle.updated0;
    if (changed || Date.now() > giveUp) break;
    await new Promise((r) => setTimeout(r, 60_000));
  }
  const minutes = ((Date.now() - idle.askedAt.getTime()) / 60000).toFixed(1);
  quote('question asked (UTC)', idle.askedAt.toISOString());
  quote('looked at (UTC)', `${new Date().toISOString()} (${minutes} min after the question)`);
  quote('before', `revision ${idle.rev0}, updatedAt ${idle.updated0}`);
  quote('after', `revision ${state.profile.revision}, updatedAt ${state.profile.updatedAt}, by ${state.profile.updatedBy}`);
  quote('page Last updated', await updated(s.page, 'profile'));
  quote('profile now', state.profile.text);
  await s.page.locator('#testing-tools').getByRole('button', { name: 'Check job health' }).click();
  await expect(s.page.locator('#testing-tools')).toContainText(/last run/i, { timeout: 30_000 });
  const h = await api(s, 'health');
  quote('api health', JSON.stringify(h.json));
  await shot(s.page, '16-idle-after');
  expect(changed, `Profile unchanged ${minutes} min after the question (Run now was not used)`).toBe(true);
});

// -------------------------------------------------------------------------------------- cleanup
test('cleanup and verdict', async ({ browser }) => {
  test.skip(!!ONLY.length, 'partial run');
  evidence = [];
  try {
    for (const n of [1, 2]) {
      const s = await signIn(browser, n);
      await api(s, 'clear_profile');
      await api(s, 'set_instructions', { text: '' });
      await api(s, 'toggle', { target: 'instructions', enabled: true });
      await api(s, 'toggle', { target: 'profile', enabled: true });
      await api(s, 'set_testing', { showPlainResponse: false, updateEveryMessage: false });
      await signOut(n);
    }
  } finally {
    flush();
  }
  const all = JSON.parse(fs.readFileSync(RESULTS, 'utf8')).attempts;
  const failed = all.filter((a) => a.status !== 'PASS');
  expect(failed.map((f) => f.step), 'steps that failed in this results file').toEqual([]);
});
