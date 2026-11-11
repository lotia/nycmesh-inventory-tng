import { expect, test } from "vitest";
import { count, decimal, sent, thousandths } from "./quantity";

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
});
