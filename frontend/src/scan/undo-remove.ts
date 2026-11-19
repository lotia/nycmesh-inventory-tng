/**
 * Undo after Remove on the cart (Ali, inventory-tng-5uy, 2026-11-13): a
 * line removed, kept so Undo can put it back where it was, as it was,
 * until the cart changes again. Kept in the tab's memory, never with the
 * cart: a reload, or another tab, offers no Undo.
 */
import type { Cart, Line } from "./cart";

export type Removed = {
  line: Line;
  /** Where it was among the cart's lines. */
  at: number;
  /** The cart before it was removed: what removing the last line drops
   * (CartStore.change), its batch key and when it was started, is put
   * back from it. */
  before: Cart;
  /** The cart's revision once it was removed: Undo is offered while the
   * cart still has it, so any change after drops the offer. */
  revision: string;
};

/** The cart without its line for this item, and that line, kept to put
 * back; null if it has none. Its revision is filled in once the cart is
 * changed (CartStore.change). */
export function removeLine(
  cart: Cart,
  itemId: number,
): { next: Cart; removed: Omit<Removed, "revision"> } | null {
  const at = cart.lines.findIndex((line) => line.itemId === itemId);
  const line = cart.lines[at];
  if (line === undefined) {
    return null;
  }
  return {
    next: { ...cart, lines: cart.lines.filter((_, each) => each !== at) },
    removed: { line, at, before: cart },
  };
}

/** `current`, the cart as removing left it, with the line put back where
 * it was, under the batch key it had and started when it was. */
export function undone(current: Cart, removed: Removed): Cart {
  const lines = [...current.lines];
  lines.splice(removed.at, 0, removed.line);
  const { batchKey, changedFrom, startedAt } = removed.before;
  return {
    ...current,
    lines,
    startedAt,
    ...(batchKey === undefined ? {} : { batchKey }),
    ...(changedFrom === undefined ? {} : { changedFrom }),
  };
}
