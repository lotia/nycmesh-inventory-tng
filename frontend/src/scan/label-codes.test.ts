import { expect, test } from "vitest";
import { CATALOGUE } from "../testing/catalogue";
import { readLabel } from "./label-codes";

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
