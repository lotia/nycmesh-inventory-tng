/**
 * Whether this device has been asked to record what it is doing.
 *
 * Volunteers do not sign in (decision 0012), so the person who meets a failure
 * is the person with no credential to raise. An administrator mints a signed
 * token -- `manage.py mint_debug_token` -- and sends a link with it in;
 * opening that link is the whole of turning this on.
 *
 * WHY THE LINK CARRIES THE TOKEN RATHER THAN A `1`. The token is what the
 * backend checks: it is what raises the sampling rate there, and it is what
 * the ingest path this app posts spans to requires. A flag that was merely
 * truthy would set the W3C sampled bit and nothing else, and the backend
 * deliberately does not honour that bit on its own -- decision 0021.
 *
 * IT COMES OUT OF THE URL IMMEDIATELY. A token left in the address bar is a
 * token in the history, in a screenshot, and in whatever the volunteer pastes
 * back when they say what they did. It is read once, stored, and the address
 * bar is rewritten without it.
 *
 * AND IT EXPIRES ON ITS OWN, twice over: the signature carries its own expiry,
 * which is the one that matters because the backend enforces it, and this side
 * keeps its own so the indicator goes away and the app stops paying for an SDK
 * on a phone in a basement.
 */

import { isNumber, isText, matches, read, forget as removeStored, write } from "../storage";

const KEY = "inventory.debugTrace";

/** The query parameter an administrator's link carries. */
export const PARAMETER = "trace";

/** The header the backend reads. `inventory_tng/debugging.py` is the other end. */
export const HEADER = "X-Debug-Trace";

/**
 * How long this side keeps one.
 *
 * An hour, matching the backend's own default rather than guessing: a token
 * outliving the indicator would leave somebody recording with nothing on
 * screen to say so, and an indicator outliving the token would promise
 * recording that is not happening.
 */
export const LIFETIME = 60 * 60 * 1000;

type Asked = { token: string; until: number };

const isAsked = (value: unknown): value is Asked =>
  matches(value, { token: isText, until: isNumber });

/**
 * Take the token out of the address bar, if there is one there.
 *
 * Returns it rather than storing it, so the caller decides -- which is what
 * lets a test drive this without a `window.history`.
 */
export function claimed(location: Location): string | null {
  const asked = new URLSearchParams(location.search).get(PARAMETER);
  return asked?.trim() ? asked.trim() : null;
}

/** The address this page should have, once the token is out of it. */
export function withoutIt(location: Location): string {
  const parameters = new URLSearchParams(location.search);
  parameters.delete(PARAMETER);
  const query = parameters.toString();
  return `${location.pathname}${query ? `?${query}` : ""}${location.hash}`;
}

/** Whatever this device was asked to record, if it has not run out. */
export function asked(now: number = Date.now()): string | null {
  const stored = read<Asked>(KEY, isAsked);
  if (stored === null) {
    return null;
  }
  if (stored.until <= now) {
    forget();
    return null;
  }
  return stored.token;
}

/** Store the token, and say whether it was stored. `settle` needs the answer. */
export function remember(token: string, now: number = Date.now()): boolean {
  return write(KEY, { token, until: now + LIFETIME });
}

export function forget(): void {
  removeStored(KEY);
}

/**
 * Read a link's token, put it away, and take it out of the address bar.
 *
 * Returns what this device is recording under, whether it arrived just now or
 * was already stored, so the caller has one answer to act on.
 */
export function settle(): string | null {
  const arriving = claimed(window.location);
  if (arriving !== null) {
    // ONLY ONCE IT IS SOMEWHERE ELSE. `storage.write` says its return value
    // exists for a caller that gives the value away on the strength of it,
    // and this is one: the address bar was the only copy. In a private window
    // or with site data blocked, `setItem` throws, and stripping the parameter
    // anyway destroyed the token instead of merely failing to store it --
    // leaving nothing to reload and an administrator having to mint another,
    // with nothing at either end saying why. Leaving it in the URL is the
    // lesser harm of the two the header above weighs.
    if (remember(arriving)) {
      window.history.replaceState(null, "", withoutIt(window.location));
    }
    return arriving;
  }
  return asked();
}
