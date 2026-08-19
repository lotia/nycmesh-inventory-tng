/**
 * The catalogue the item-list tests read, in the shape the API answers with.
 *
 * The same two things the cart fixtures describe -- zip ties sold in packets
 * of a hundred, and cable measured in metres -- so a reader moving between the
 * two files is looking at one warehouse.
 */
import type { Item } from "../api/types";

export const zipTies: Item = {
  id: 1,
  name: "Zip Ties Reusable",
  category: 1,
  unit_of_measure: "each",
  minimum_stock: "0.000",
  reorder_quantity: "1.000",
  active: true,
  balances: [
    { location: 1, quantity: "400.000" },
    { location: 2, quantity: "100.000" },
  ],
  labels: [
    { code: "ZZZ111ABCD", quantity: "1.000" },
    { code: "7QK3M2XV9A", quantity: "100.000" },
  ],
};

export const cable: Item = {
  id: 2,
  name: "Cat6 Outdoor",
  category: 1,
  unit_of_measure: "metre",
  minimum_stock: "0.000",
  reorder_quantity: "1.000",
  active: true,
  balances: [{ location: 1, quantity: "305.000" }],
  labels: [],
};
