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

export const isText = (value: unknown): boolean => typeof value === "string";
export const isNumber = (value: unknown): boolean => typeof value === "number";

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

export function write(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Full, or denied outright.
  }
}
