/** A cart for the tests. Never bundled. */
import { type Cart, freshCart } from "../scan/cart";

export function cart(fields: Partial<Cart> = {}): Cart {
  return { ...freshCart(), volunteerId: 1, fromId: 10, toId: 12, ...fields };
}
