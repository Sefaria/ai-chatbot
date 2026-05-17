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

export { _, locale, getLocaleFromNavigator };
