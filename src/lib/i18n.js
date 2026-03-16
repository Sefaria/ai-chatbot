import en from '../locales/en.json';
import he from '../locales/he.json';

const locales = { english: en, hebrew: he };

export function t(lang, key) {
  return locales[lang]?.[key] ?? locales.english[key] ?? key;
}
