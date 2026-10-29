import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {JSDOM, VirtualConsole} from 'jsdom';
import {BASE_ALLOWED_PARAMS, createAnalytics} from '@sefaria/analytics';
import {CHATBOT_EXTRA_ALLOWED_PARAMS, assistantClickParams} from '../src/lib/analytics.js';

// A fake browser page. The package's listen() checks `instanceof Element`, so it needs the page's globals.
// Clicking a link makes jsdom try to open it, which it can't do; it reports that as a "not implemented"
// error on its console. Hide only those; anything else is still printed.
const virtualConsole = new VirtualConsole().sendTo(console, {omitJSDOMErrors: true});
virtualConsole.on('jsdomError', error => {
  if (!error.message.startsWith('Not implemented')) console.error(error);
});
const {window} = new JSDOM('<!doctype html><body></body>', {virtualConsole});
globalThis.document = window.document;
globalThis.Element = window.Element;

const allowed = new Set([...BASE_ALLOWED_PARAMS, ...CHATBOT_EXTRA_ALLOWED_PARAMS]);

/**
 * Builds <lc-chatbot> with `html` inside its shadow DOM, wires the same click listener as
 * LCChatbot.svelte, clicks `#target`, and returns what was sent to gtag as [event, params] pairs.
 */
function clickInChatbot(html) {
  document.body.innerHTML = '';
  const host = document.createElement('lc-chatbot');
  document.body.appendChild(host);
  const shadow = host.attachShadow({mode: 'open'});
  shadow.innerHTML = html;

  const sent = [];
  const analytics = createAnalytics({
    gtag: (_, event, params) => sent.push([event, params]),
    extraAllowedParams: CHATBOT_EXTRA_ALLOWED_PARAMS,
    commonParams: () => ({is_staff: 'false', la_version: '1.2.3'}),
  });
  const stop = analytics.listen(host, 'click', (e, path) => {
    const params = assistantClickParams(path);
    if (params) analytics.track('assistant_click', params);
  });
  shadow.querySelector('#target').dispatchEvent(new window.MouseEvent('click', {bubbles: true, composed: true}));
  stop();
  return sent;
}

const common = {is_staff: 'false', la_version: '1.2.3'};

test('a click behind the shadow DOM wall reports the nearest aria-label, plus is_staff and la_version', () => {
  const sent = clickInChatbot('<div aria-label="Chat messages"><p><span id="target">hi</span></p></div>');
  assert.deepEqual(sent, [['assistant_click', {feature_name: 'Chat messages', ...common}]]);
});

test('the docked/floating toggle is skipped, since toggleMode() sends its own event', () => {
  const sent = clickInChatbot('<button aria-label="Toggle docked/floating"><svg id="target"></svg></button>');
  assert.deepEqual(sent, []);
});

test('data-feature-name wins over a link and an aria-label', () => {
  const sent = clickInChatbot(`
    <div aria-label="Chat messages">
      <a href="/topics/shabbat" data-feature-name="appetizer_topic"><b id="target"> Shabbat </b></a>
    </div>`);
  assert.deepEqual(sent, [['assistant_click', {feature_name: 'appetizer_topic', link_text: 'Shabbat', ...common}]]);
});

test('a link reports its text and only the path and query of a full URL', () => {
  const sent = clickInChatbot(`
    <div aria-label="Chat messages">
      <a id="target" href="https://www.sefaria.org/Genesis.1.1?lang=bi"> Genesis 1:1 </a>
    </div>`);
  assert.deepEqual(sent, [['assistant_click', {
    feature_name: 'Response link', text: 'Genesis 1:1', link_url: '/Genesis.1.1?lang=bi', link_text: 'Genesis 1:1', ...common,
  }]]);
});

test('a relative link keeps its href as is', () => {
  const [[, params]] = clickInChatbot('<a id="target" href="/texts">Texts</a>');
  assert.equal(params.link_url, '/texts');
});

test('a malformed link URL is sent as written instead of crashing the handler', () => {
  const [[, params]] = clickInChatbot('<a id="target" href="http://[bad">Bad</a>');
  assert.equal(params.link_url, 'http://[bad');
});

test('a click with no label anywhere sends nothing', () => {
  const sent = clickInChatbot('<div><span id="target">x</span></div>');
  assert.deepEqual(sent, []);
});

// Scans LCChatbot.svelte for track('event', { ... }) calls. A parameter name missing from the allowed
// list is silently dropped by the package (only a console warning), so catch it here.
test('every parameter passed to track() in LCChatbot.svelte is on the allowed list', () => {
  const source = readFileSync(new URL('../src/components/LCChatbot.svelte', import.meta.url), 'utf8');
  const calls = [...source.matchAll(/\btrack\(\s*'[^']+'\s*,\s*\{([^}]*)\}/g)];
  assert.ok(calls.length >= 5, `expected to find track() calls, found ${calls.length}`);
  const used = new Set();
  for (const [, body] of calls) {
    for (const part of body.split(',')) {
      const name = part.trim().match(/^([A-Za-z_]\w*)\s*(?::|$)/)?.[1];
      if (name) used.add(name);
    }
  }
  assert.deepEqual([...used].filter(name => !allowed.has(name)), []);
});
