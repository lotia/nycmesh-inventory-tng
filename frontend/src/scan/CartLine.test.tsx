import { render, screen } from "@testing-library/preact";
import { expect, test } from "vitest";
import { messages as english } from "../locales/en.po";
import { speak } from "../shared/i18n";
import { cable } from "../testing/catalogue";
import { CartLine } from "./CartLine";

/** A spool of 1,234.567 m, one of them and 1,000 m loose in the cart. */
function shown() {
  const spool = cable.packages[0] as (typeof cable.packages)[number];
  const item = { ...cable, packages: [{ ...spool, size: "1234.567" }] };
  render(
    <CartLine
      line={{ itemId: item.id, packages: { [spool.id]: "1" }, loose: "1000" }}
      item={item}
      opens={[]}
      moving={false}
      onChange={() => {}}
      onRemove={() => {}}
    />,
  );
}

test("a line's amounts are written as English writes numbers", () => {
  shown();
  expect(screen.getByText("2,234.567 m in all")).toBeDefined();
  expect(screen.getByLabelText("Spool (1,234.567 m)")).toBeDefined();
});

test("a line's amounts are written as the locale writes numbers, its boxes plain", () => {
  // German's separators, read with the English words.
  speak("de", english);
  shown();
  expect(screen.getByText("2.234,567 m in all")).toBeDefined();
  const spool = screen.getByLabelText("Spool (1.234,567 m)");
  expect((spool as HTMLInputElement).value).toBe("1");
  expect((screen.getByLabelText("Loose m") as HTMLInputElement).value).toBe(
    "1000",
  );
});
