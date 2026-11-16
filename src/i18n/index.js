import { addMessages, init, locale, getLocaleFromNavigator, _ } from 'svelte-i18n';
import en from './locales/en.json';
import he from './locales/he.json';

addMessages('en', en);
addMessages('he', he);

init({
  fallbackLocale: 'en',
  initialLocale: 'en',
});

export function setLocale(value) {
  locale.set(value || 'en');
}

function numberedKeys(set) {
  // Build the rotation from locale keys so Weblate can add new numbered messages without a code change.
  const pattern = new RegExp(`^assistant\\.thinking\\.${set}\\.\\d+$`);
  return Object.keys(en)
    .filter((key) => pattern.test(key))
    .sort((a, b) => {
      const aIndex = Number(a.split('.').at(-1));
      const bIndex = Number(b.split('.').at(-1));
      return aIndex - bIndex || a.localeCompare(b);
    });
}

export function getThinkingMessageKeys({ hanukkah = false, lang = 'en' } = {}) {
  if (hanukkah) {
    const keys = numberedKeys('hanukkah');
    // Hebrew gets the Hanukkah lines only once Weblate has translated all of them;
    // until then it keeps the everyday lines rather than falling back to English.
    if (lang !== 'he' || keys.every((key) => he[key])) return keys;
  }
  return numberedKeys('array');
}

export { _, locale, getLocaleFromNavigator };
