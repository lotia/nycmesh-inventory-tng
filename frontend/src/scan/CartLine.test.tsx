import { render, screen } from "@testing-library/preact";
import { expect, test } from "vitest";
import { takeSeparatorFrom } from "../shared/quantity";
import { cable, openAt } from "../testing/catalogue";
import { CartLine } from "./CartLine";
import type { Line } from "./cart";

/** A spool of 1234.567 m, one of them and 1000 m loose in the cart. */
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

test("a line's amounts are written ungrouped, as its boxes take them", () => {
  // Ali, 2026-11-24 (inventory-tng-ua04).
  shown();
  expect(screen.getByText("2234.567 m in all")).toBeDefined();
  expect(screen.getByLabelText("Spool (1234.567 m)")).toBeDefined();
});

test("on a comma page a line's amounts take its comma, its boxes too", () => {
  const page = document.createElement("html");
  page.dataset.decimal = ",";
  takeSeparatorFrom(page);
  shown();
  expect(screen.getByText("2234,567 m in all")).toBeDefined();
  const spool = screen.getByLabelText("Spool (1234,567 m)");
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
  // The spool's count refused and the loose box read, it is not named.
  rerender(line("x", "1"));
  expect(loose.getAttribute("aria-invalid")).toBeNull();
  expect(loose.getAttribute("aria-describedby")).toBeNull();
});

/** The cable's line with `fields`, its spool open at the hub offered. */
function lineOf(fields: Partial<Line>) {
  return (
    <CartLine
      line={{ itemId: cable.id, packages: {}, loose: "", ...fields }}
      item={cable}
      opens={[openAt(1, 120000)]}
      moving={false}
      onChange={() => {}}
      onRemove={() => {}}
    />
  );
}

/** What `box` is described by, or null if it is not marked refused. */
function refusalOf(box: HTMLElement): string | null {
  if (box.getAttribute("aria-invalid") !== "true") {
    return null;
  }
  const ids = (box.getAttribute("aria-describedby") ?? "").split(" ");
  return ids.map((id) => document.getElementById(id)?.textContent).join("|");
}

const SPOOL = "Spool (305 m)";
const LOOSE = "Loose m";
const TAKEN = /^Cut or take from the Spool/;

test("a package's count refused is read with its box, and only it", () => {
  const { rerender } = render(lineOf({ packages: { 1001: "x" } }));
  expect(refusalOf(screen.getByLabelText(SPOOL))).toBe(
    "Count whole packages: Spool.",
  );
  expect(refusalOf(screen.getByLabelText(LOOSE))).toBeNull();
  expect(refusalOf(screen.getByLabelText(TAKEN))).toBeNull();
  rerender(lineOf({ packages: { 1001: "1" } }));
  expect(refusalOf(screen.getByLabelText(SPOOL))).toBeNull();
  expect(
    screen.getByLabelText(SPOOL).getAttribute("aria-describedby"),
  ).toBeNull();
});

test("an amount from an open package refused is read with its box, and only it", () => {
  const open = (amount: string) =>
    lineOf({ open: { "open-1": { amount, whole: false } } });
  const { rerender } = render(open("lots"));
  const taken = screen.getByLabelText(TAKEN);
  expect(refusalOf(taken)).toMatch(
    /\|Enter how much from the open package, up to 3 decimals\.$/,
  );
  expect(refusalOf(screen.getByLabelText(SPOOL))).toBeNull();
  expect(refusalOf(screen.getByLabelText(LOOSE))).toBeNull();
  rerender(open("2"));
  expect(taken.getAttribute("aria-invalid")).toBeNull();
  // Still read with what is on it now and after the cut.
  expect(taken.getAttribute("aria-describedby")?.split(" ")).toHaveLength(2);
});

test("the loose box unread is marked though a package's refusal comes first", () => {
  render(lineOf({ packages: { 1001: "x" }, loose: "lots" }));
  // Each box read with its own words, both shown under the line.
  expect(refusalOf(screen.getByLabelText(SPOOL))).toBe(
    "Count whole packages: Spool.",
  );
  expect(refusalOf(screen.getByLabelText(LOOSE))).toBe(
    "Enter the loose amount in m, up to 3 decimals.",
  );
});

test("an amount typed for an open package no longer offered is not read, and kept", () => {
  const gone = { "open-2": { amount: "lots", whole: false } };
  const { rerender } = render(lineOf({ loose: "3", open: gone }));
  // No box for it on the page, so nothing refused for it.
  expect(screen.getByText("3 m in all")).toBeDefined();
  expect(refusalOf(screen.getByLabelText(LOOSE))).toBeNull();
  // Offered again, what was typed there is read again.
  rerender(
    <CartLine
      line={{ itemId: cable.id, packages: {}, loose: "3", open: gone }}
      item={cable}
      opens={[openAt(2, 120000)]}
      moving={false}
      onChange={() => {}}
      onRemove={() => {}}
    />,
  );
  expect(refusalOf(screen.getByLabelText(TAKEN))).toMatch(
    /\|Enter how much from the open package, up to 3 decimals\.$/,
  );
});
