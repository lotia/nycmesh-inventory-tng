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

test("the loose box's refusal is read with the box, and only its own", () => {
  const spool = cable.packages[0] as (typeof cable.packages)[number];
  const line = (packs: string, loose: string) => (
    <CartLine
      line={{ itemId: cable.id, packages: { [spool.id]: packs }, loose }}
      item={cable}
      opens={[]}
      moving={false}
      onChange={() => {}}
      onRemove={() => {}}
    />
  );
  const { rerender } = render(line("1", "abc"));
  const loose = screen.getByLabelText("Loose m");
  expect(loose.getAttribute("aria-invalid")).toBe("true");
  const said = document.getElementById(
    loose.getAttribute("aria-describedby") ?? "",
  );
  expect(said?.textContent).toBe(
    "Enter the loose amount in m, up to 3 decimals.",
  );
  // The spool's count refused, the loose box is not named.
  rerender(line("x", "abc"));
  expect(loose.getAttribute("aria-invalid")).toBeNull();
  expect(loose.getAttribute("aria-describedby")).toBeNull();
});
