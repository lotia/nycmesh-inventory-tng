/**
 * Reading and writing this device's own memory, guarded.
 *
 * Two things are kept here -- the cart in hand and the volunteer holding the
 * phone -- and both need the same care, so it is written once. Storage is not
 * a place values can be trusted to come back from: it can hold something an
 * older version of this app wrote, something hand-edited, or nothing at all
 * because Safari's private mode throws on access rather than returning null.
 *
 * Losing what is stored must never lose what is in hand, so every failure
 * here is swallowed and the caller carries on in memory.
 */

/** What a stored object's fields have to be for it to be worth restoring. */
export type Shape = Record<string, (value: unknown) => boolean>;

export const isText = (value: unknown): value is string => typeof value === "string";
export const isNumber = (value: unknown): value is number => typeof value === "number";

export function matches(value: unknown, shape: Shape): boolean {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const fields = value as Record<string, unknown>;
  return Object.entries(shape).every(([field, holds]) => holds(fields[field]));
}

/** What is stored under this key, if it is still the shape this version reads. */
export function read<T>(key: string, isRestorable: (value: unknown) => value is T): T | null {
  try {
    const stored = window.localStorage.getItem(key);
    const parsed: unknown = stored === null ? null : JSON.parse(stored);
    return isRestorable(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * Store this, and say whether it was stored.
 *
 * Nothing throws: the header above says why. But a caller that has *given the
 * value away* on the strength of this call -- the outbox is handed a batch and
 * the cart is emptied -- has to know, so the failure is reported in the return
 * rather than only swallowed. A caller writing a copy of what it still holds
 * can carry on ignoring it.
 */
export function forget(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // The header says why nothing here throws. Nothing is reported either:
    // a caller removing something has given nothing away, so there is no
    // decision for it to make about a failure.
  }
}

export function write(key: string, value: unknown): boolean {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    // Full, or denied outright.
    return false;
  }
}
