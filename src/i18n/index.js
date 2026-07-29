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

export function getThinkingMessageKeys() {
  // Build the rotation from locale keys so Weblate can add new numbered messages without a code change.
  return Object.keys(en)
    .filter((key) => /^assistant\.thinking\.array\.\d+$/.test(key))
    .sort((a, b) => {
      const aIndex = Number(a.split('.').at(-1));
      const bIndex = Number(b.split('.').at(-1));
      return aIndex - bIndex || a.localeCompare(b);
    });
}

export { _, locale, getLocaleFromNavigator };
