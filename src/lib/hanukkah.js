// Hanukkah-only touches to the thinking state (dreidel glyph, themed lines).
// Window: the first candle (evening of 25 Kislev) through the last day, in the
// visitor's local time. Update these each year.
export const HANUKKAH_START = new Date(2026, 11, 4, 17, 0); // Fri 4 Dec 2026, 17:00
export const HANUKKAH_END = new Date(2026, 11, 13, 0, 0); // end of Sat 12 Dec 2026

// `?hanukkah=1` / `?hanukkah=0` on the page URL, or localStorage
// `lc-hanukkah` = "1" / "0", forces the window on or off for testing.
function forcedHanukkah() {
  try {
    const param = new URLSearchParams(window.location.search).get('hanukkah');
    const flag = param ?? window.localStorage.getItem('lc-hanukkah');
    if (flag === '1') return true;
    if (flag === '0') return false;
  } catch {
    // No window or storage blocked: fall through to the dates.
  }
  return null;
}

export function isHanukkah(now = new Date()) {
  // ALWAYS ON for Hanukkah 2026: shipped on by hand, switched off by reverting this commit.
  // Reverting brings back the date window below, which is already closed after 12 Dec 2026.
  return forcedHanukkah() ?? true;
}
