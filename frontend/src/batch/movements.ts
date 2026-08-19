/**
 * Turning the batch in the browser into the request the ledger takes.
 *
 * The cart holds one location and a kind; the ledger takes a movement per
 * line, with the location on whichever side the kind means. That mapping is a
 * domain fact -- a check-out that takes stock from nowhere is not a check-out
 * -- and the server states it too, in `KIND_SIDES`
 * (backend/src/inventory/views.py), which is what actually enforces it. This
 * is here so the app can tell a volunteer what is missing before it asks.
 *
 * See docs/decisions/0011-qr-batch-scanning.md section 6.
 */
import type { CartLine, CartState, TransactionKind } from "../cart/cartState";

/** Which side of a movement this kind puts the location on. */
type Side = "from_location" | "to_location";

/**
 * The kinds a batch assembled by scanning can be.
 *
 * Four of the seven. A transfer needs a location on both sides and the cart
 * carries one, so it cannot be expressed here yet; an adjustment and a count
 * are not per-line movements in the first place, which is why the server's own
 * `KIND_SIDES` leaves them out (decision 0011 section 6). Offering a kind the
 * request cannot satisfy would be a refusal the volunteer only meets after
 * pressing Save.
 */
export const KINDS: { kind: TransactionKind; label: string; side: Side }[] = [
  { kind: "checkout", label: "Taking stock out", side: "from_location" },
  { kind: "checkin", label: "Bringing stock back", side: "to_location" },
  { kind: "receipt", label: "Receiving a delivery", side: "to_location" },
  { kind: "consumption", label: "Used on a job", side: "from_location" },
];

export function sideFor(kind: TransactionKind): Side | null {
  return KINDS.find((candidate) => candidate.kind === kind)?.side ?? null;
}

/** What stops Save being offered, said in words, or null when nothing does. */
export function whatIsMissing(cart: CartState): string | null {
  if (cart.actorId === null) {
    return "Say who you are first.";
  }
  if (cart.lines.length === 0) {
    return "Nothing in this batch yet.";
  }
  if (sideFor(cart.kind) !== null && cart.locationId === null) {
    return "Say where the stock is: pick it below, or scan the code on the wall.";
  }
  return null;
}

/** One line, as a movement. */
function movementFor(line: CartLine, cart: CartState): Record<string, number> {
  const side = sideFor(cart.kind);
  const movement: Record<string, number> = { item: line.itemId, quantity: line.quantity };
  if (side !== null && cart.locationId !== null) {
    movement[side] = cart.locationId;
  }
  return movement;
}

/**
 * The whole batch, as one request.
 *
 * The idempotency key goes with it and is the cart's own, minted when the cart
 * opened rather than at submit: that is what makes Retry safe after a failure
 * nobody can see the far side of. See cartState.ts.
 */
export function batchBody(cart: CartState): Record<string, unknown> {
  return {
    idempotency_key: cart.idempotencyKey,
    kind: cart.kind,
    actor: cart.actorId,
    ...(cart.jobReference === "" ? {} : { job_reference: cart.jobReference }),
    movements: cart.lines.map((line) => movementFor(line, cart)),
  };
}
