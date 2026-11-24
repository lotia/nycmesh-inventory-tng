import { beforeEach, expect, test } from "vitest";
import { takeSeparatorFrom } from "../shared/quantity";
import { followTotals } from "./running-totals";

// Shelf B held 3 radios before 2 came from Shelf C and 1 from Bin 4, and a
// spool of 305 m sealed, with an open package beside it.
beforeEach(() => {
  document.body.innerHTML = `
    <table>
      <tr data-total="7-5" hidden><td>Radio</td><td><span role="status" aria-atomic="true">Shelf B after saving: <span data-total-amount>6</span> each</span></td></tr>
      <tr data-total="7-6" hidden><td>Cable</td><td><span role="status" aria-atomic="true">Shelf B after saving: <span data-total-amount>0</span> m</span></td></tr>
    </table>
    <input name="at_7_counted_5" value="3" data-adds-to="7-5" data-unset="3">
    <input name="at_7_arrived_8_loose_5" value="2" data-adds-to="7-5" data-unset="2">
    <input name="at_7_arrived_9_loose_5" value="1" data-adds-to="7-5" data-unset="1">
    <input name="at_7_sealed_6_2" value="1" data-adds-to="7-6" data-unset="1" data-per="305">
    <input name="at_7_opened_4" value="20.5" data-adds-to="7-6" data-unset="20.5" data-state="at_7_state_4">
    <select name="at_7_state_4"><option value="here">Here</option><option value="used_up">Used up</option></select>`;
  followTotals(document);
});

const line = (key: string) =>
  document.querySelector(`[data-total="${key}"]`) as HTMLElement;
const total = (key: string) =>
  line(key).querySelector("[data-total-amount]")?.textContent;

function type(name: string, value: string) {
  const box = document.querySelector(`[name="${name}"]`) as HTMLInputElement;
  box.value = value;
  box.dispatchEvent(new Event("input"));
}

test("the line shows what the place holds once saved", () => {
  expect(line("7-5").hidden).toBe(false);
  expect(total("7-5")).toBe("6");
  // A sealed package by its size, and an open package's metres.
  expect(total("7-6")).toBe("325.5");
});

test("it follows what is typed, a blank box as it was sent", () => {
  type("at_7_arrived_8_loose_5", "1");
  expect(total("7-5")).toBe("5");
  type("at_7_counted_5", "");
  expect(total("7-5")).toBe("5");
  type("at_7_counted_5", "4");
  expect(total("7-5")).toBe("6");
});

test("an open package closed counts as none", () => {
  const state = document.querySelector("select") as HTMLSelectElement;
  state.value = "used_up";
  state.dispatchEvent(new Event("change"));
  expect(total("7-6")).toBe("305");
});

test("a box with no number hides the line until it has one", () => {
  type("at_7_arrived_9_loose_5", "x");
  expect(line("7-5").hidden).toBe(true);
  type("at_7_arrived_9_loose_5", "3");
  expect(line("7-5").hidden).toBe(false);
  expect(total("7-5")).toBe("8");
});

test("a sealed package is counted whole, as the server reads it", () => {
  type("at_7_sealed_6_2", "1.5");
  expect(line("7-6").hidden).toBe(true);
  type("at_7_sealed_6_2", "2");
  expect(line("7-6").hidden).toBe(false);
  expect(total("7-6")).toBe("630.5");
});

test("a move fixed in the forms it arrived in: sealed spools whole", () => {
  // Two spools and 10 m moved to Shelf B, which held 5 m loose.
  document.body.innerHTML = `
    <p data-total="7-6" hidden><span data-total-amount></span></p>
    <input name="at_7_loose_6" value="5" data-adds-to="7-6" data-unset="5">
    <input name="at_7_arrived_8_sealed_6_2" value="2" data-adds-to="7-6" data-unset="2" data-per="305">
    <input name="at_7_arrived_8_loose_6" value="10" data-adds-to="7-6" data-unset="10">`;
  followTotals(document);
  expect(total("7-6")).toBe("625");
  type("at_7_arrived_8_sealed_6_2", "1");
  type("at_7_arrived_8_loose_6", "4");
  expect(total("7-6")).toBe("314");
  type("at_7_arrived_8_sealed_6_2", "0.5");
  expect(line("7-6").hidden).toBe(true);
});

test("the words are a polite live region, read out whole", () => {
  const words = line("7-5").querySelector("[role=status]") as HTMLElement;
  expect(words.getAttribute("aria-atomic")).toBe("true");
  expect(words.contains(line("7-5").querySelector("[data-total-amount]"))).toBe(
    true,
  );
});

test("a blank box that says nothing of its own counts as none", () => {
  document.body.innerHTML = `
    <p data-total="1-1" hidden><span data-total-amount></span></p>
    <input data-adds-to="1-1" value="">
    <input data-adds-to="1-1" value="2">`;
  followTotals(document);
  expect(document.querySelector("[data-total-amount]")?.textContent).toBe("2");
});

test("a line with no boxes, or no number in it, is left hidden", () => {
  document.body.innerHTML = `
    <p data-total="1-1" hidden><span data-total-amount>0</span></p>
    <p data-total="1-2" hidden>No number</p>
    <input data-adds-to="1-2" value="1">`;
  followTotals(document);
  for (const each of document.querySelectorAll<HTMLElement>("[data-total]")) {
    expect(each.hidden).toBe(true);
  }
});

test("a decimal with no units before it counts, as the server saves it", () => {
  type("at_7_opened_4", ".5");
  expect(line("7-6").hidden).toBe(false);
  expect(total("7-6")).toBe("305.5");
});

test("a box is read, and the total shown, in the page's decimal separator (inventory-tng-qnwz)", () => {
  const root = document.createElement("html");
  root.dataset.decimal = ",";
  takeSeparatorFrom(root);
  try {
    type("at_7_opened_4", "10,5");
    // As a box writes it: no grouping.
    expect(total("7-6")).toBe("315,5");
    type("at_7_opened_4", "1000,5");
    expect(total("7-6")).toBe("1305,5");
    type("at_7_opened_4", "10.5");
    expect(line("7-6").hidden).toBe(true);
  } finally {
    takeSeparatorFrom(document.createElement("html"));
  }
});
