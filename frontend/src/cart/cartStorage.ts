/**
 * Reading and writing the cart to `localStorage`, so a phone that locks or
 * reloads does not lose the scans in it. See
 * docs/decisions/0011-qr-batch-scanning.md section 6.
 */
import { type CartState, createCart } from "./cartState";

/** Versioned: a cart written by an older shape is ignored, never migrated. */
export const STORAGE_KEY = "nycmesh-inventory.cart.v1";

function isCartState(value: unknown): value is CartState {
  if (typeof value !== "object" || value === null) return false;
  const cart = value as Record<string, unknown>;
  if (typeof cart.idempotencyKey !== "string" || !Array.isArray(cart.lines)) return false;
  return cart.lines.every((line: unknown) => {
    if (typeof line !== "object" || line === null) return false;
    const fields = line as Record<string, unknown>;
    return typeof fields.itemId === "number" && typeof fields.quantity === "number";
  });
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
