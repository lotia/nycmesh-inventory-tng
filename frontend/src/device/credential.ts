/**
 * The name this browser is told apart by, asked for once and then kept.
 *
 * Nobody signs in (decision 0012), so without this every request from a hub is
 * one address, one rate-limit bucket, and a log nothing can separate. The
 * server mints an opaque string, this stores it, and every request afterwards
 * carries it. `backend/src/inventory_tng/devices.py` is what it is for and
 * what it deliberately is not -- attribution, never admission.
 *
 * IT COSTS NO SCREEN AND NO GESTURE. Minted silently on first use, the way
 * `telemetry/flag.ts` stores its own flag, because friction here would buy
 * nothing: the network is what keeps anybody out (decision 0030), so asking a
 * volunteer to do something would be asking them to prove a thing that is not
 * being checked. That decision is also what bounds this reasoning -- it may
 * justify asking for no gesture, and may never justify guarding less.
 *
 * AND NOTHING WAITS FOR IT. `enrol()` is called once from `main.tsx` and is
 * not awaited; `api/client.ts` reads what is stored and sends it if there is
 * anything, so a request made before this browser has one simply goes without,
 * exactly as every request did before this existed. A batch a volunteer is
 * holding must never be delayed by a credential that decides nothing -- which
 * is also why a failure to mint is swallowed rather than surfaced.
 *
 * MINTING IS THE APP'S CALL AND NOT THE TRANSPORT'S, which is why `held()` is
 * what `client.ts` imports. A read that quietly posted would let any module
 * sending a request create a row, and it made counting requests in a test
 * depend on what happened to be in storage.
 *
 * WHAT ENDS ONE is the server, and this side finds out from `/api/me` rather
 * than by expiring anything of its own. `revoked` is a state the app can draw
 * a sentence for; `unknown` is what a client holding something the server has
 * stopped honouring is told, and the answer to that is to forget it and let
 * the next request mint again.
 */

import { apiPost } from "../api/client";
import { isText, matches, read, forget as removeStored, write } from "../storage";

const KEY = "inventory.device";

/** The header the backend reads. `inventory_tng/devices.py` is the other end. */
export const HEADER = "X-Device";

/** Where one is asked for. */
export const ENROL_PATH = "/api/devices";

type Enrolled = { token: string };

const isEnrolled = (value: unknown): value is Enrolled => matches(value, { token: isText });

/**
 * The mint in flight, so a second caller joins it rather than starting another.
 *
 * The app opens with several fetches at once and `main.tsx` asks as it starts;
 * without this a cold start mints a device per caller, which is precisely the
 * burst `DEVICE_ENROLMENT_RATE` exists to refuse, produced by this app on an
 * ordinary load rather than by anybody misbehaving.
 */
let minting: Promise<void> | null = null;

/**
 * The token, once one has been found, so it is not re-read per request.
 *
 * `api/client.ts` asks on the way into every `fetch`, and the answer never
 * changes once it is a string: a token is written once and never rewritten.
 * Reading storage each time cost a `getItem`, a `JSON.parse` and a shape check
 * on a phone in a basement, for an answer that was settled -- and the same
 * path already had an allocation taken out of it on that argument.
 *
 * ONLY THE POSITIVE ANSWER IS KEPT. While there is nothing, storage is read
 * afresh every time, so a token another tab minted a moment ago is picked up
 * rather than waited out. `write` and `forget` below are the only two things
 * that move this, and both go through here.
 */
let known: string | null = null;

/** What this browser stores, if it has been given anything. */
export function held(): string | null {
  known ??= read<Enrolled>(KEY, isEnrolled)?.token ?? null;
  return known;
}

/** Throw away what is stored, so the next mint starts from nothing. */
export function forget(): void {
  known = null;
  removeStored(KEY);
}

/**
 * Ask for one, unless this browser has one or is already asking.
 *
 * Every failure is swallowed. A device credential decides nothing this app
 * needs in order to work, so a mint refused by a rate limit, by a network
 * that is not there, or by a server having a bad minute must be invisible:
 * the request that prompted it goes without a header and the app carries on,
 * which is what it did before any of this existed.
 */
export function enrol(): Promise<void> {
  if (minting !== null) {
    return minting;
  }
  if (held() !== null) {
    return Promise.resolve();
  }
  minting = apiPost<Enrolled>(ENROL_PATH, {})
    .then((given) => {
      if (isEnrolled(given)) {
        known = given.token;
        write(KEY, { token: given.token });
      }
    })
    .catch(() => {
      // Deliberately nothing. The header is an improvement to the next
      // request, not a precondition of this one.
    })
    .finally(() => {
      minting = null;
    });
  return minting;
}
