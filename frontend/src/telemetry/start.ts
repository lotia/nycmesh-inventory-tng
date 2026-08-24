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
 *
 * AND THIS MODULE OWNS WHETHER THIS DEVICE IS RECORDING. It was held in three
 * places that nothing reconciled -- the token and its expiry in `flag.ts`, the
 * boolean here, and a copy the badge took into component state at mount -- so
 * they could disagree, and did. The badge subscribes to this one now.
 *
 * WHICH MAKES THE TWO `await`s BELOW THE WHOLE DIFFICULTY, and getting them
 * wrong reintroduced the defect this module exists to fix, one layer down.
 * Loading the SDK chunk takes hundreds of milliseconds on a phone, and both
 * the button and the expiry can fire inside that window. So:
 *
 * - `started` is claimed before the awaits, because two calls in one tick must
 *   not both get past the guard and register two sets of instrumentations.
 * - `generation` says WHICH attempt is running, so an attempt that was stopped
 *   -- or superseded by a later start -- tears down what it built rather than
 *   assigning it over the top of somebody else's.
 * - `stop` notifies AFTER the flush, not before, because `provider.shutdown()`
 *   posts what is queued: telling the badge first would take the words off the
 *   screen while the phone was still sending, which is the direction `flag.ts`
 *   says must never happen.
 */

import { notifier } from "../store";
import { LIFETIME, runsOutIn } from "./flag";

let started = false;
let teardown: (() => Promise<void>) | null = null;
let expiring: ReturnType<typeof setTimeout> | null = null;
/** The attempt in flight, so a stopped one knows it is no longer the one. */
let generation = 0;
/** A teardown already running, so a second `stop` waits for it rather than
 * finding `teardown` already taken and returning having awaited nothing. */
let halting: Promise<void> | null = null;

/**
 * Hear about it when this device starts or stops recording.
 *
 * `Recording.tsx` is the only caller, and its using this is what replaced the
 * badge holding a copy of the answer in `useState`. The mechanism is shared
 * with `batch/outbox.ts` rather than written a second time here; `store.ts`
 * says which half of the contract it keeps and which stays with the caller.
 */
const { subscribe, changed } = notifier();

export { subscribe };

/**
 * Start the SDK, and say whether it did.
 *
 * Once per page. Calling it twice would register a second set of
 * instrumentations onto the same fetch, so the second call answers false.
 *
 * Rejects if the SDK chunk cannot be loaded, having first put everything back.
 * `main.tsx` calls this with `void`, and installs `watch()` before it, so such
 * a failure is reported as an unhandled rejection rather than swallowed -- a
 * redeploy that leaves a phone asking for a chunk that is gone is exactly the
 * thing nobody would otherwise hear about.
 */
export async function start(token: string | null): Promise<boolean> {
  if (token === null || started) {
    return false;
  }
  started = true;
  const attempt = ++generation;
  changed();
  // `runsOutIn` answers from storage, and `settle` deliberately hands back a
  // token it could not store -- a private window, or site data blocked. The
  // fallback is what stops that case running with no ceiling at all while the
  // badge says in words that it stops on its own within the hour.
  expiring = setTimeout(() => void stop(), runsOutIn() ?? LIFETIME);

  let begun: () => Promise<void>;
  try {
    const { begin } = await import("./sdk");
    begun = await begin(token);
  } catch (reason) {
    // Nothing was built, so there is nothing to tear down -- but `started`,
    // the badge and the timer are all claimed and have to be given back, or
    // the badge stays on for an hour in front of no SDK and `start` can never
    // be retried.
    if (generation === attempt) {
      await stop();
    }
    throw reason;
  }

  // The generation alone, and not `started` beside it: `stop` is the only
  // thing that clears `started`, it bumps `generation` on the next line, and
  // the number only ever increases -- so a stopped attempt cannot still be the
  // current one. Asking twice would be two guards a reader has to prove agree.
  if (generation !== attempt) {
    // Stopped, or superseded by a later start, while the chunk was loading.
    // Tearing down what this attempt built is the whole point: assigning it to
    // `teardown` would leave a registered provider that nothing will ever stop
    // -- posting spans with the badge gone, which is the invisible half-stop
    // this module was written to close.
    await begun();
    return false;
  }
  teardown = begun;
  return true;
}

/**
 * Stop recording, for real.
 *
 * ONE WAY DOWN, and everything that stops recording takes it: the button on
 * screen -- `Recording.tsx` argues why that control is a promise this
 * application makes rather than a convenience -- the expiry armed above, and
 * a start that failed. Clearing the stored token kept only half of it, and the
 * half that stopped was the invisible one.
 *
 * Safe to call twice, and the second call waits for the first rather than
 * returning early: a test's `beforeEach` racing the expiry timer would
 * otherwise carry a live provider and two patched instrumentations into the
 * next test.
 */
export async function stop(): Promise<void> {
  if (halting !== null) {
    await halting;
    return;
  }
  if (expiring !== null) {
    clearTimeout(expiring);
    expiring = null;
  }
  const was = started;
  started = false;
  generation += 1;
  const down = teardown;
  teardown = null;
  if (down === null) {
    if (was) {
      changed();
    }
    return;
  }
  halting = down();
  try {
    await halting;
  } finally {
    halting = null;
  }
  changed();
}

/** Whether the SDK is running, which is what the indicator asks. */
export const recording = (): boolean => started;
