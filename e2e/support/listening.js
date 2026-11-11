import { mockChatServer } from './mockChatServer.js';
import { AssistantPage } from '../pages/assistantPage.js';

export const question = 'How are questions used inside a Mishnah?';

export async function setupListening(page, context, { enabled = true, supported = true } = {}) {
  await mockChatServer(context);
  await page.addInitScript(({ supported }) => {
    window.webkitSpeechRecognition = undefined;
    window.SpeechRecognition = supported ? class {
      constructor() { window.testRecognition = this; }
      start() {}
      abort() { this.onend?.(); }
    } : undefined;
  }, { supported });
  let cards = [];
  await context.route('**/api/v2/listening/**', async route => {
    const request = route.request(), url = request.url();
    if (request.method() === 'GET') return route.fulfill({ json: { enabled } });
    if (url.endsWith('/sessions')) return route.fulfill({ status: 201, json: { session_id: 'test-session' } });
    if (url.endsWith('/analyze')) {
      cards = cards.length ? cards : [{ id: 'question-id', question, status: 'pending', page_url: 'https://www.sefaria.org/Berakhot.2a' }];
      return route.fulfill({ json: { received_through_seq: 1, analyzed_through_seq: 1, suggestions: cards } });
    }
    if (request.method() === 'PATCH') {
      cards = cards.map(card => ({ ...card, status: request.postDataJSON().status }));
    }
    return route.fulfill({ json: { status: 'closed' } });
  });
  const assistant = new AssistantPage(page);
  await assistant.logIn(); await assistant.open();
  return assistant;
}

export async function speak(page) {
  await page.evaluate(() => window.testRecognition.onresult({ resultIndex: 0,
    results: [Object.assign([{ transcript: "I've never seen a question inside a Mishnah." }], { isFinal: true })] }));
}
