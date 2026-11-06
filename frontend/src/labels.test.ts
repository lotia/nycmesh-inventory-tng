import { expect, test } from "vitest";
import { freshCart } from "./cart";
import { placeSides, Repeats, readLabel } from "./labels";
import { CATALOGUE } from "./test-catalogue";

test("a label names an item, a package of one, or a place", () => {
  expect(readLabel(CATALOGUE, "B3ZT9W")).toEqual({ kind: "item", itemId: 100 });
  expect(readLabel(CATALOGUE, " k7qx2m\n")).toEqual({
    kind: "item",
    itemId: 101,
    packageId: 1001,
  });
  expect(readLabel(CATALOGUE, "S4HELF")).toEqual({
    kind: "place",
    locationId: 11,
  });
  // An item's old code reads as its label does.
  expect(readLabel(CATALOGUE, "lb5")).toEqual({ kind: "item", itemId: 100 });
});

test("a code the catalogue lacks says so, as read", () => {
  expect(readLabel(CATALOGUE, "https://docs.google.com/forms/x")).toEqual({
    kind: "unknown",
    code: "HTTPS://DOCS.GOOGLE.COM/FORMS/X",
  });
});

test("a place is offered for the side still empty first", () => {
  const cart = freshCart();
  expect(placeSides(cart)).toEqual(["from", "to"]);
  expect(placeSides({ ...cart, fromId: 10 })).toEqual(["to", "from"]);
  // A receipt has only a to side, an install only a from side.
  expect(placeSides({ ...cart, kind: "receipt" })).toEqual(["to"]);
  expect(placeSides({ ...cart, kind: "install" })).toEqual(["from"]);
});

test("a label held in view counts once, and again once shown again", () => {
  const repeats = new Repeats(2000);
  expect(repeats.fresh("B3ZT9W", 0)).toBe(true);
  // Read many times a second while held there.
  expect(repeats.fresh("B3ZT9W", 150)).toBe(false);
  expect(repeats.fresh("B3ZT9W", 2100)).toBe(false);
  // Taken away for two seconds, then shown again.
  expect(repeats.fresh("B3ZT9W", 4200)).toBe(true);
  // Another label is a scan of its own at once.
  expect(repeats.fresh("K7QX2M", 4300)).toBe(true);
});

test("a label that names nothing reads as unknown", () => {
  const catalogue = {
    ...CATALOGUE,
    labels: [
      { code: "N0THNG", item_id: null, package_id: null, location_id: null },
    ],
  };
  expect(readLabel(catalogue, "N0THNG")).toEqual({
    kind: "unknown",
    code: "N0THNG",
  });
});
