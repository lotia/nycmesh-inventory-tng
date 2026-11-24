import { describe, expect, test } from "vitest";
import {
  amountTyped,
  boxAmount,
  count,
  decimal,
  readBox,
  type Separator,
  sent,
  thousandths,
} from "./quantity";
import shared from "./quantity-cases.json";

// The locales the shared cases are written for, by their separator.
const SEPARATORS: Record<"en" | "de", Separator> = { en: ".", de: "," };

describe("a box, as the server reads it", () => {
  test.each(
    shared.cases.flatMap((each) =>
      (["en", "de"] as const).map((locale) => ({
        text: each.typed,
        locale,
        expected: each[locale] as { amount?: string; refused?: string },
      })),
    ),
  )("$text in $locale", ({ text, locale, expected }) => {
    const read = amountTyped(text, SEPARATORS[locale]);
    // toBe, as Object.is, tells -0 from 0.
    expect(read).toBe(
      expected.amount === undefined ? expected.refused : sent(expected.amount),
    );
  });
});

test.each([
  ["", 0],
  ["2,5", 2500],
  ["-1", null],
  ["2.5", null],
])("readBox(%j) in a comma box is %j", (text, amount) => {
  expect(readBox(text, ",")).toBe(amount);
});

test.each([".", ","] as const)(
  "a box written with %j reads back unchanged",
  (separator) => {
    for (const amount of [0, 1, 500, 12500, 1234500]) {
      expect(readBox(boxAmount(amount, separator), separator)).toBe(amount);
    }
    expect(boxAmount(1234500, separator)).toBe(`1234${separator}5`);
  },
);

test.each([
  ["", 0],
  ["  ", 0],
  ["3", 3000],
  ["12.5", 12500],
  ["0.001", 1],
  ["300.000", 300000],
  [" 7 ", 7000],
])("thousandths(%j) is %i", (text, amount) => {
  expect(thousandths(text)).toBe(amount);
});

test.each(["-1", "1.2345", "1,5", "abc", "1.", ".5", "1e3"])(
  "thousandths(%j) is unreadable",
  (text) => {
    expect(thousandths(text)).toBeNull();
  },
);

test("adding tenths stays exact", () => {
  const sum = (thousandths("0.1") ?? 0) + (thousandths("0.2") ?? 0);
  expect(decimal(sum)).toBe("0.3");
});

test.each([
  [0, "0"],
  [3000, "3"],
  [12500, "12.5"],
  [1, "0.001"],
  [300000, "300"],
  [1010, "1.01"],
])("decimal(%i) is %j", (amount, text) => {
  expect(decimal(amount)).toBe(text);
});

test.each([
  ["", 0],
  ["4", 4],
  ["4.5", null],
  ["-1", null],
  ["x", null],
])("count(%j) is %j", (text, expected) => {
  expect(count(text)).toBe(expected);
});

test("a decimal the server sent is read exactly, or is a bug", () => {
  expect(sent("120.500")).toBe(120500);
  expect(() => sent("lots")).toThrow("not a decimal: lots");
  // Less than nothing, as the records may say of an open package.
  expect(sent("-2.500")).toBe(-2500);
  expect(decimal(-2500)).toBe("-2.5");
});
