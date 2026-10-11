/** A confirmed server stop settles the UI even if streaming/recovery is still pending. */
export function waitForTurn(stream, signal) {
  let onAbort;
  const stopped = new Promise((_, reject) => {
    onAbort = () => reject(signal.reason);
    if (signal.aborted) onAbort();
    else signal.addEventListener('abort', onAbort, {once: true});
  });
  return Promise.race([stream, stopped]).finally(() => {
    signal.removeEventListener('abort', onAbort);
  });
}
