# LC Chatbot Injector Extension

Loads the lc-chatbot module from localhost and injects the lc-chatbot element into every page.

## Load in Chrome
1. Open `chrome://extensions`.
2. Enable "Developer mode".
3. Click "Load unpacked" and select this folder.

## Notes
- Pages with strict Content Security Policy may block loading the module from `http://localhost:5173`.
- Update `content.js` if the dev server URL or query string changes.
