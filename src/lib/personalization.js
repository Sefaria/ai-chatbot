/**
 * Test-only query parameters for the personalization prototype: `?la_personalization=<mode>` and
 * `?la_injection=<where>`. They ride along in the chat request `context`; the server honours them
 * only for allowlisted users on a non-production deployment, and ignores them everywhere else.
 */

const MODE_PARAM = 'la_personalization';
const INJECTION_PARAM = 'la_injection';
const CLEAR_VALUE = 'clear';

function read(storage, key) {
  try {
    return storage ? storage.getItem(key) : null;
  } catch {
    return null;
  }
}

function write(storage, key, value) {
  try {
    if (storage) storage.setItem(key, value);
  } catch {
    // Storage can be blocked; the parameter then applies to this page load only.
  }
}

function remove(storage, key) {
  try {
    if (storage) storage.removeItem(key);
  } catch {
    // Nothing to clean up.
  }
}

/**
 * @param {string} search - `window.location.search`
 * @param {{getItem: Function, setItem: Function, removeItem: Function} | null} storage
 * @returns {{personalizationMode?: string, personalizationInjection?: string}}
 */
export function personalizationContext(search, storage) {
  const params = new URLSearchParams(search || '');
  const mode = params.get(MODE_PARAM);
  if (mode === CLEAR_VALUE) {
    remove(storage, MODE_PARAM);
    remove(storage, INJECTION_PARAM);
    return {};
  }
  const injection = params.get(INJECTION_PARAM);
  if (mode) write(storage, MODE_PARAM, mode);
  if (injection) write(storage, INJECTION_PARAM, injection);

  const context = {};
  const storedMode = mode || read(storage, MODE_PARAM);
  const storedInjection = injection || read(storage, INJECTION_PARAM);
  if (storedMode) context.personalizationMode = storedMode;
  if (storedInjection) context.personalizationInjection = storedInjection;
  return context;
}
