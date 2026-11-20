/** A cart for the tests, and what the page says of a cart's problems.
 * Never bundled. */
import { type Cart, freshCart } from "../scan/cart";
import type { Said } from "../shared/outbox-notes";

export function cart(fields: Partial<Cart> = {}): Cart {
  return { ...freshCart(), volunteerId: 1, fromId: 10, toId: 12, ...fields };
}

/** A batch's problems, or a quantity's, as text, each name the page quoted
 * in it (outbox-notes.Said) in [brackets]. */
export function shown<T extends object>(result: T) {
  const text = (said: Said) =>
    said
      .map((part) => (typeof part === "string" ? part : `[${part.quoted}]`))
      .join("");
  if ("problems" in result) {
    return { problems: (result.problems as Said[]).map(text) };
  }
  return "problem" in result
    ? { problem: text(result.problem as Said) }
    : result;
}
