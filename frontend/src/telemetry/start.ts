/**
 * OpenTelemetry in the browser, started only when somebody asked for it.
 *
 * The SPA produced no telemetry at all before this: its one `console.error`
 * ran on a volunteer's phone and was seen by nobody.
 *
 * NOTHING RUNS UNLESS A TOKEN IS HELD, and this module imports nothing that
 * could make that untrue. The SDK lives in `sdk.ts` and is reached only
 * through `await import()` after the token check, because a static import of
 * it patches a phone's `Promise` and timers whether or not anything is ever
 * recorded -- `sdk.ts` says what that costs. `flag.ts` is what decides, and
 * `errors.ts` is where the failures that are reported whatever the flag says
 * get their own path.
 *
 * STARTING IS ASYNCHRONOUS AND STOPPING IS TOO. `main.tsx` does not wait for
 * either: document-load spans are built from the Performance API rather than
 * from having been present at the first byte, so the render is not held up for
 * a volunteer whose phone is doing the ordinary work.
 */

let started = false;
let teardown: (() => Promise<void>) | null = null;

/**
 * Start the SDK, and say whether it did.
 *
 * Once per page. Calling it twice would register a second set of
 * instrumentations onto the same fetch, so the second call answers false.
 */
export async function start(token: string | null): Promise<boolean> {
  if (token === null || started) {
    return false;
  }
  // Claimed before the await, so two calls in the same tick cannot both get
  // past the guard and register two sets of instrumentations.
  started = true;
  const { begin } = await import("./sdk");
  teardown = await begin(token);
  return true;
}

/**
 * Stop recording, for real.
 *
 * The button on screen is a promise this application makes -- `Recording.tsx`
 * argues it -- and clearing the stored token kept only half of it: the header
 * on API calls stopped, because `api/client.ts` re-reads the flag per request,
 * while the provider went on batching spans and posting them for the rest of
 * the page's life. The half that stopped was the invisible half.
 *
 * Also what a test unstarts a page with, so the teardown a volunteer gets is
 * the one the suite exercises rather than a boolean reset beside it.
 */
export async function stop(): Promise<void> {
  const stopping = teardown;
  teardown = null;
  started = false;
  if (stopping !== null) {
    await stopping();
  }
}

/** Whether the SDK is running, which is what the indicator asks. */
export const recording = (): boolean => started;
