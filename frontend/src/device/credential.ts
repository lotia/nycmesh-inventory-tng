/**
 * The token this device presents, while a deployment asks for one.
 *
 * PROVISIONAL. It exists for one meeting -- inventory-tng-81f7 -- and
 * inventory-tng-81f7.4 deletes this file along with the setting behind it.
 *
 * WHAT IT IS NOT is a security control, and this is the part not to mistake
 * later. The token is minted by `django.core.signing` on the server and stored
 * here as an opaque string; nothing on either side implements a token format
 * or a comparison. The cryptography a genuinely enrolled device would use --
 * a key the browser cannot export, signing every request -- belongs to
 * inventory-tng-jro, which is where it gets argued.
 *
 * The point is that the difference is INVISIBLE from where a volunteer is
 * standing. Same screen, same tap, same wall when you pick up a second phone
 * or clear your browser. What a room can judge is the friction, and the
 * friction is identical; what separates the two is what an attacker can do
 * afterwards, which no demo can show.
 *
 * Shaped after `telemetry/flag.ts`, deliberately: a token held on the device,
 * kept in validated storage, and attached to every request through the single
 * chokepoint in `api/client.ts`. Two of these in two shapes would be two
 * places for one of them to be wrong.
 */

import { isText, matches, read, forget as removeStored, write } from "../storage";

const KEY = "inventory.device";

/** The header the backend reads. `inventory_tng/postures.py` is the other end. */
export const HEADER = "X-Device";

/** Where a device asks for one. */
export const ENROL_AT = "/api/devices";

type Enrolled = { token: string };

const isEnrolled = (value: unknown): value is Enrolled => matches(value, { token: isText });

/**
 * What this device is enrolled as, or nothing at all.
 *
 * NO EXPIRY ON THIS SIDE, which is the one place this differs from
 * `telemetry/flag.ts`. A debug-tracing token is an authority lent for an
 * afternoon; an enrolled device is the phone somebody carries to a roof. A
 * credential that quietly ran out would put act five's cost -- "every one of
 * you does this screen again" -- on the demo rather than on the posture. What
 * ends this one is the server refusing it, which is a revoked row or a rotated
 * key.
 */
export function held(): string | null {
  return read<Enrolled>(KEY, isEnrolled)?.token ?? null;
}

/** Store the token, and say whether it was stored. The caller needs the answer. */
export function remember(token: string): boolean {
  return write(KEY, { token });
}

export function forget(): void {
  removeStored(KEY);
}
