import { addMessages, init, locale, getLocaleFromNavigator, _ } from 'svelte-i18n';
import en from './locales/en.json';
import he from './locales/he.json';

addMessages('en', en);
addMessages('he', he);

init({
  fallbackLocale: 'en',
  initialLocale: 'en',
});

// Accepts the legacy widget values ("english"/"hebrew") and BCP-47 codes ("en"/"he").
// Returns a normalized locale code ("en" or "he"), defaulting to "en".
export function normalizeLocale(value) {
  if (!value) return 'en';
  const v = String(value).toLowerCase();
  if (v === 'he' || v === 'hebrew') return 'he';
  if (v === 'en' || v === 'english') return 'en';
  return 'en';
}

export function setLocale(value) {
  locale.set(normalizeLocale(value));
}

export { _, locale, getLocaleFromNavigator };
