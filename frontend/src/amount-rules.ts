/**
 * What an item's unit says of its amounts, as the server says it
 * (backend/src/inventory_tng/catalogue/amount_rules.py, inventory-tng-xu6x):
 * counted each, in whole units, or a length; and when what is left on an
 * open package is a short end. The server writes which units are which to
 * amount-rules.json; amount-rules-vectors.json holds what both must make
 * of some units and amounts. Amounts here are thousandths (shared/quantity).
 */

import table from "./amount-rules.json";
import { thousandths } from "./shared/quantity";

/** Whether an item in `unit` is counted, in whole units, not measured. */
export function countedWhole(unit: string): boolean {
  return table.counted_whole.includes(unit);
}

/** Whether an item in `unit` is measured as a length, as cable is. */
export function isLength(unit: string): boolean {
  return table.lengths.includes(unit);
}

/** An item counted each moves, and is held, in whole units: `amount` in
 * thousandths. */
export function wholeEnough(unit: string, amount: number): boolean {
  return !countedWhole(unit) || amount % 1000 === 0;
}

/** Whether an open package with `left` (thousandths) on it is a short
 * end: some left, but less than its package's too-short-to-use length.
 * Never one showing nothing, so a card is flagged one or the other, not
 * both. */
export function isShortEnd(left: number, shortBelow: string | null): boolean {
  const below = shortBelow === null ? null : thousandths(shortBelow);
  return below !== null && 0 < left && left < below;
}
