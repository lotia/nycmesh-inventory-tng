/**
 * Reading and writing the cart to `localStorage`, so a phone that locks or
 * reloads does not lose the scans in it. See
 * docs/decisions/0011-qr-batch-scanning.md section 6.
 */
import { KINDS } from "../batch/movements";
import { isNumber, isText, matches, read, type Shape, write } from "../storage";
import { type CartState, createCart } from "./cartState";

/** Versioned: a cart written by an older shape is ignored, never migrated. */
export const STORAGE_KEY = "nycmesh-inventory.cart.v1";

const isIdOrNobody = (value: unknown): boolean => value === null || typeof value === "number";

/**
 * Not merely a string: one of the kinds this app offers.
 *
 * A stored `"transfer"` restores into a cart the submit bar cannot express --
 * it needs a location on both sides and the cart carries one -- and would be
 * sent, refused with a 409 nothing renders, and offered a Retry that cannot
 * succeed. The versioned key above catches a change of shape; this catches a
 * value that was never ours.
 */
const isOfferedKind = (value: unknown): boolean =>
  typeof value === "string" && KINDS.some((offered) => offered.kind === value);

/**
 * Every field the cart carries, by what it has to be -- not just the two the
 * submit reads. A stored object missing one of these is restorable in name
 * only: a cart without `kind` posts an undefined kind and is refused after the
 * volunteer has pressed Save, and a line without `name` shows up as a blank
 * row they cannot identify. The version in the key above says *when* a stored
 * cart is another shape; these say so for anything the key cannot catch.
 */
const CART_SHAPE: Shape = {
  idempotencyKey: isText,
  createdAt: isText,
  kind: isOfferedKind,
  jobReference: isText,
  actorId: isIdOrNobody,
  locationId: isIdOrNobody,
};

const LINE_SHAPE: Shape = {
  itemId: isNumber,
  quantity: isNumber,
  name: isText,
  unitOfMeasure: isText,
};

function isCartState(value: unknown): value is CartState {
  if (!matches(value, CART_SHAPE)) return false;
  const { lines } = value as Record<string, unknown>;
  return Array.isArray(lines) && lines.every((line: unknown) => matches(line, LINE_SHAPE));
}

/** The stored cart, or a new one if there is nothing usable to restore. */
export function loadCart(): CartState {
  return read(STORAGE_KEY, isCartState) ?? createCart();
}

export function saveCart(cart: CartState): void {
  write(STORAGE_KEY, cart);
}
