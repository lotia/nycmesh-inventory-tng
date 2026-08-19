/**
 * Reading and writing the cart to `localStorage`, so a phone that locks or
 * reloads does not lose the scans in it. See
 * docs/decisions/0011-qr-batch-scanning.md section 6.
 */
import { type CartState, createCart } from "./cartState";

/** Versioned: a cart written by an older shape is ignored, never migrated. */
export const STORAGE_KEY = "nycmesh-inventory.cart.v1";

type Shape = Record<string, (value: unknown) => boolean>;

const isText = (value: unknown): boolean => typeof value === "string";
const isNumber = (value: unknown): boolean => typeof value === "number";
const isIdOrNobody = (value: unknown): boolean => value === null || typeof value === "number";

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
  kind: isText,
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

function matches(value: unknown, shape: Shape): boolean {
  if (typeof value !== "object" || value === null) return false;
  const fields = value as Record<string, unknown>;
  return Object.entries(shape).every(([field, holds]) => holds(fields[field]));
}

function isCartState(value: unknown): value is CartState {
  if (!matches(value, CART_SHAPE)) return false;
  const { lines } = value as Record<string, unknown>;
  return Array.isArray(lines) && lines.every((line: unknown) => matches(line, LINE_SHAPE));
}

/** The stored cart, or a new one if there is nothing usable to restore. */
export function loadCart(): CartState {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    const parsed: unknown = stored === null ? null : JSON.parse(stored);
    if (isCartState(parsed)) return parsed;
  } catch {
    // Unparseable, or storage denied outright -- Safari's private mode throws
    // on access rather than returning null. An in-memory cart still works.
  }
  return createCart();
}

export function saveCart(cart: CartState): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(cart));
  } catch {
    // Full or denied. Losing persistence must not lose the scan in hand.
  }
}
