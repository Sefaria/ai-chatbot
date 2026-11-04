/**
 * Records the GA4 events the widget sends, for the analytics tests.
 *
 * The widget sends events through the host page's `window.gtag`. The demo page
 * has none, so this puts a recording one in place before the page's scripts run,
 * on every page the context opens (a reload starts a new, empty record).
 */
export async function recordAnalytics(context) {
  await context.addInitScript(() => {
    window.gaEvents = [];
    window.gtag = (command, name, params) => {
      if (command === 'event') window.gaEvents.push({ name, ...params });
    };
  });
}

/** The events with this name sent so far on the page, oldest first. */
export async function analyticsEvents(page, name) {
  return page.evaluate((eventName) => window.gaEvents.filter((e) => e.name === eventName), name);
}
