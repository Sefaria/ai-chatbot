// The answering model ends a personalized lab answer with `<profile_used>…</profile_used>`.
// While the answer streams, none of that tag may show: hide everything from the tag on, and hold
// back a trailing piece of the opening tag that may still turn into it.

const TAG = '<profile_used>';

export function hideProfileUsed(text) {
  const at = text.indexOf(TAG);
  if (at !== -1) return text.slice(0, at);
  for (let keep = Math.min(TAG.length - 1, text.length); keep > 0; keep--) {
    if (text.endsWith(TAG.slice(0, keep))) return text.slice(0, text.length - keep);
  }
  return text;
}
