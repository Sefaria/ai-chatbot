import test from 'node:test';
import assert from 'node:assert/strict';

test('the Hanukkah string keys the code uses exist in both locales (from Weblate)', async () => {
  const {readFile} = await import('node:fs/promises');
  const load = async (lang) => JSON.parse(await readFile(new URL(`../src/i18n/locales/${lang}.json`, import.meta.url)));
  const expected = [
    'assistant.loading.hanukkah.initial',
    ...[1, 2, 3, 4, 5].map((n) => `assistant.thinking.array.hanukkah.0${n}`),
    'assistant.thinking.hanukkah.final',
  ];
  for (const lang of ['en', 'he']) {
    const messages = await load(lang);
    for (const key of expected) assert.ok(messages[key], `${lang}.json is missing ${key}`);
  }
});
