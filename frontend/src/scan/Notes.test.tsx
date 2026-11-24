import { render } from "@testing-library/preact";
import { expect, test } from "vitest";
import type { Said } from "../shared/outbox-notes";
import { Counted } from "./Notes";

/** The numbers `said` shows in italic. */
function italic(said: Said): (string | null)[] {
  const { container } = render(<Counted said={said} />);
  return [...container.querySelectorAll("em")].map((em) => em.textContent);
}

test("a number is in italic whole, in either decimal separator, grouped or not (inventory-tng-qnwz)", () => {
  expect(italic(["Cable: 1,234.5 m"])).toEqual(["1,234.5"]);
  expect(italic(["Cable: 1234,5 m"])).toEqual(["1234,5"]);
  expect(italic(["Use “,” for decimals, such as 0,5."])).toEqual(["0,5"]);
  expect(italic(["2 × Box"])).toEqual(["2"]);
});

test("numbers a comma and a space part stay apart", () => {
  expect(italic(["Boxes 2, 3 and 4"])).toEqual(["2", "3", "4"]);
});

test("neither a number of a quoted name nor digits joined to letters", () => {
  expect(italic([{ quoted: "Spool 305" }, ": 1 m"])).toEqual(["1"]);
  expect(italic(["LiteBeam 5AC: 1 × Box"])).toEqual(["1"]);
});
