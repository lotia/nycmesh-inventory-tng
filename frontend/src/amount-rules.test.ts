import { describe, expect, test } from "vitest";
import {
  countedWhole,
  isLength,
  isShortEnd,
  wholeEnough,
} from "./amount-rules";
import shared from "./amount-rules-vectors.json";
import { sent } from "./shared/quantity";

describe("what a unit says of amounts, as the server says it", () => {
  test.each(shared.units)("$unit", ({ unit, counted_whole, length }) => {
    expect(countedWhole(unit)).toBe(counted_whole);
    expect(isLength(unit)).toBe(length);
  });

  test.each(shared.whole_enough)("$amount $unit", ({ unit, amount, whole }) => {
    expect(wholeEnough(unit, sent(amount))).toBe(whole);
  });

  test.each(shared.short_ends)(
    "$left left, short below $short_below",
    ({ left, short_below, short }) => {
      expect(isShortEnd(sent(left), short_below)).toBe(short);
    },
  );
});
