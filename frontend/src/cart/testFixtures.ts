/**
 * The scene the cart tests share: two items and the labels on them.
 *
 * Declared once because three test files describe the same cart, and a packet
 * that means a hundred in one file and ten in another would make the tests
 * disagree about what they are demonstrating.
 */
import type { CartItem, ScannedLabel } from "./cartState";

export const zipTies: CartItem = { id: 1, name: "Zip Ties Reusable", unitOfMeasure: "each" };
export const cable: CartItem = { id: 2, name: "Cat6 Outdoor", unitOfMeasure: "metre" };

export const packet: ScannedLabel = { code: "7QK3M2XV9A", item: zipTies, quantity: 100 };
export const single: ScannedLabel = { code: "ZZZ111ABCD", item: zipTies, quantity: 1 };
export const box: ScannedLabel = { code: "4NP8R7T2WQ", item: cable, quantity: 305 };
