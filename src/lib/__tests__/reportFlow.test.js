/**
 * Run with: node --test src/lib/__tests__/
 */
import test from 'node:test';
import assert from 'node:assert/strict';

import {
  REPORT_ISSUE_FLOW,
  buildReportSeed,
  parseStartFlowDetail,
  stripSegmentMarkup
} from '../reportFlow.js';

const GENESIS = {
  flow: REPORT_ISSUE_FLOW,
  ref: 'Genesis 1:1',
  en: 'In the beginning God created the heaven and the earth.',
  he: 'בְּרֵאשִׁית בָּרָא אֱלֹהִים אֵת הַשָּׁמַיִם וְאֵת הָאָרֶץ׃'
};

test('stripSegmentMarkup removes Sefaria inline markup', () => {
  assert.equal(stripSegmentMarkup('<b>In the beginning</b> God created'), 'In the beginning God created');
  assert.equal(stripSegmentMarkup('a<br/>b'), 'a b');
  assert.equal(stripSegmentMarkup('<span class="x">text</span>'), 'text');
});

test('stripSegmentMarkup drops footnotes rather than inlining them', () => {
  const withFootnote =
    'the heaven<sup>1</sup><i class="footnote">Or "sky".</i> and the earth';
  assert.equal(stripSegmentMarkup(withFootnote), 'the heaven and the earth');
});

test('stripSegmentMarkup decodes entities and collapses whitespace', () => {
  assert.equal(stripSegmentMarkup('Jacob&nbsp;&amp;&nbsp;Esau'), 'Jacob & Esau');
  assert.equal(stripSegmentMarkup('&quot;peshat&quot;'), '"peshat"');
  assert.equal(stripSegmentMarkup('a   \n  b'), 'a b');
});

test('stripSegmentMarkup tolerates missing or non-string input', () => {
  assert.equal(stripSegmentMarkup(undefined), '');
  assert.equal(stripSegmentMarkup(null), '');
  assert.equal(stripSegmentMarkup(42), '');
  assert.equal(stripSegmentMarkup(''), '');
});

test('parseStartFlowDetail accepts a well-formed report request', () => {
  assert.deepEqual(parseStartFlowDetail(GENESIS), {
    flow: REPORT_ISSUE_FLOW,
    ref: 'Genesis 1:1',
    en: GENESIS.en,
    he: GENESIS.he
  });
});

test('parseStartFlowDetail rejects unknown or missing flows', () => {
  assert.equal(parseStartFlowDetail({ ...GENESIS, flow: 'something_else' }), null);
  assert.equal(parseStartFlowDetail({ ...GENESIS, flow: '' }), null);
  assert.equal(parseStartFlowDetail({ ref: 'Genesis 1:1', en: 'x' }), null);
});

test('parseStartFlowDetail rejects a request with no ref', () => {
  assert.equal(parseStartFlowDetail({ ...GENESIS, ref: '   ' }), null);
  assert.equal(parseStartFlowDetail({ ...GENESIS, ref: undefined }), null);
});

test('parseStartFlowDetail rejects a request with neither text', () => {
  assert.equal(parseStartFlowDetail({ ...GENESIS, en: '', he: '' }), null);
  assert.equal(parseStartFlowDetail({ ...GENESIS, en: '<b></b>', he: null }), null);
});

test('parseStartFlowDetail accepts a segment with only one language', () => {
  const enOnly = parseStartFlowDetail({ ...GENESIS, he: '' });
  assert.equal(enOnly.he, '');
  assert.equal(enOnly.en, GENESIS.en);

  const heOnly = parseStartFlowDetail({ ...GENESIS, en: '' });
  assert.equal(heOnly.en, '');
});

test('parseStartFlowDetail ignores non-object details', () => {
  assert.equal(parseStartFlowDetail(null), null);
  assert.equal(parseStartFlowDetail(undefined), null);
  assert.equal(parseStartFlowDetail('report_issue'), null);
});

test('buildReportSeed produces the documented shape', () => {
  assert.equal(
    buildReportSeed(GENESIS),
    'Genesis 1:1 says:\n' +
      `English: "${GENESIS.en}"\n` +
      `Hebrew: ${GENESIS.he}\n` +
      '\n' +
      'Comment: '
  );
});

test('buildReportSeed ends with an empty Comment for the reader to fill in', () => {
  const seed = buildReportSeed(GENESIS);
  assert.ok(seed.endsWith('Comment: '), 'reader types directly after the seed');
});

test('buildReportSeed omits a language the segment does not have', () => {
  const seed = buildReportSeed({ ref: 'Genesis 1:1', en: 'In the beginning', he: '' });
  assert.ok(!seed.includes('Hebrew:'));
  assert.ok(seed.includes('English: "In the beginning"'));
});

test('buildReportSeed strips markup out of the seeded text', () => {
  const seed = buildReportSeed({
    ref: 'Genesis 1:1',
    en: '<b>In the beginning</b><sup>1</sup><i class="footnote">gloss</i>',
    he: '<span>בראשית</span>'
  });
  assert.ok(!seed.includes('<'), `seed still contains markup: ${seed}`);
  assert.ok(!seed.includes('gloss'));
  assert.ok(seed.includes('English: "In the beginning"'));
  assert.ok(seed.includes('Hebrew: בראשית'));
});

const LONG_EN = 'word '.repeat(400).trim();
const LONG_HE = 'מילה '.repeat(400).trim();

test('buildReportSeed leaves a comfortable seed untouched', () => {
  assert.equal(buildReportSeed(GENESIS, 10000), buildReportSeed(GENESIS));
});

test('buildReportSeed with no limit does not truncate', () => {
  const seed = buildReportSeed({ ref: 'Genesis 1:1', en: LONG_EN, he: LONG_HE });
  assert.ok(seed.includes(LONG_EN));
  assert.ok(seed.includes(LONG_HE));
});

test('buildReportSeed trims to fit a tight limit, leaving room to type', () => {
  const limit = 600;
  const seed = buildReportSeed({ ref: 'Genesis 1:1', en: LONG_EN, he: LONG_HE }, limit);
  assert.ok(seed.length <= limit, `seed was ${seed.length}, limit ${limit}`);
  assert.ok(limit - seed.length >= 150, 'reader needs room for their comment');
});

test('buildReportSeed keeps the scaffolding intact when it trims', () => {
  const seed = buildReportSeed({ ref: 'Genesis 1:1', en: LONG_EN, he: LONG_HE }, 600);
  assert.ok(seed.startsWith('Genesis 1:1 says:'));
  assert.ok(seed.includes('English: "'));
  assert.ok(seed.includes('Hebrew: '));
  assert.ok(seed.endsWith('Comment: '));
  assert.ok(seed.includes('…'), 'trimming should be visible to the reader');
});

test('buildReportSeed trims both languages rather than starving one', () => {
  const seed = buildReportSeed({ ref: 'Genesis 1:1', en: LONG_EN, he: LONG_HE }, 600);
  const english = seed.match(/English: "(.*)"/)[1];
  const hebrew = seed.match(/Hebrew: (.*)/)[1];
  assert.ok(english.length > 50, `English was starved: ${english.length}`);
  assert.ok(hebrew.length > 50, `Hebrew was starved: ${hebrew.length}`);
});

test('buildReportSeed survives an absurd limit by keeping the ref', () => {
  const seed = buildReportSeed({ ref: 'Genesis 1:1', en: LONG_EN, he: LONG_HE }, 30);
  assert.ok(seed.includes('Genesis 1:1'));
  assert.ok(seed.endsWith('Comment: '));
});

test('buildReportSeed treats a non-numeric limit as no limit', () => {
  const seed = buildReportSeed({ ref: 'Genesis 1:1', en: LONG_EN, he: LONG_HE }, undefined);
  assert.ok(seed.includes(LONG_EN));
});
