import { expect, test } from "vitest";
import { type Cart, freshCart } from "./cart";
import { aboutLine, lineChanged } from "./refused-line";

const withLines = (lines: Cart["lines"]): Cart => ({ ...freshCart(), lines });

test("a refusal's line is changed only by a change to that line", () => {
  const before = withLines([
    { itemId: 1, packages: {}, loose: "2.5" },
    { itemId: 2, packages: {}, loose: "1" },
  ]);
  const about = aboutLine(before, 1);
  expect(lineChanged(about, before)).toBe(false);
  // Its fields read back in another order, as from storage: the same.
  expect(
    lineChanged(about, withLines([{ loose: "2.5", packages: {}, itemId: 1 }])),
  ).toBe(false);
  expect(
    lineChanged(about, withLines([{ itemId: 1, packages: {}, loose: "2,5" }])),
  ).toBe(true);
  // Removed, it changed.
  expect(lineChanged(about, withLines([]))).toBe(true);
  // Words about no line never are.
  expect(lineChanged(undefined, withLines([]))).toBe(false);
});

test("a refusal about an item with no line is changed once one is added", () => {
  const about = aboutLine(withLines([]), 1);
  expect(lineChanged(about, withLines([]))).toBe(false);
  expect(
    lineChanged(about, withLines([{ itemId: 1, packages: {}, loose: "" }])),
  ).toBe(true);
});
