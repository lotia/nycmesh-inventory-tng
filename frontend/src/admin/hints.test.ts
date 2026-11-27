import { beforeEach, expect, test } from "vitest";
import { takeSeparatorFrom } from "../shared/quantity";
import { followPicks, followTyped, followUnits, followWas } from "./hints";

beforeEach(() => {
  document.body.innerHTML = `
    <select name="found_package_id">
      <option value="">None</option>
      <option value="1" data-hinted>Cable, Spool</option>
      <option value="2">Plugs, Box</option>
    </select>
    <input name="found_left" aria-describedby="hint-found_left">
    <p id="hint-found_left" data-hint-for="found_package_id">Read the length</p>`;
  followPicks(document);
});

const hint = () => document.getElementById("hint-found_left") as HTMLElement;
const field = () => document.querySelector("input") as HTMLInputElement;

function pick(value: string) {
  const select = document.querySelector("select") as HTMLSelectElement;
  select.value = value;
  select.dispatchEvent(new Event("change"));
}

test("the hint shows only while a package it fits is picked", () => {
  expect(hint().hidden).toBe(true);
  expect(field().hasAttribute("aria-describedby")).toBe(false);
  pick("1");
  expect(hint().hidden).toBe(false);
  expect(field().getAttribute("aria-describedby")).toBe("hint-found_left");
  pick("2");
  expect(hint().hidden).toBe(true);
  expect(field().hasAttribute("aria-describedby")).toBe(false);
});

test("a hint with no select or field of its own is left as it is", () => {
  document.body.innerHTML = `<p id="lonely" data-hint-for="nothing">Hint</p>`;
  followPicks(document);
  expect((document.getElementById("lonely") as HTMLElement).hidden).toBe(false);
});

test("other ids describing the field are kept", () => {
  document.body.innerHTML = `
    <select name="pick"><option data-hinted>A</option><option>B</option></select>
    <input aria-describedby="problem hint">
    <p id="hint" data-hint-for="pick">Hint</p>`;
  followPicks(document);
  const input = document.querySelector("input") as HTMLInputElement;
  expect(input.getAttribute("aria-describedby")).toBe("problem hint");
  pick("B");
  expect(input.getAttribute("aria-describedby")).toBe("problem");
});

test("a select with nothing to pick hides it", () => {
  document.body.innerHTML = `
    <select name="pick"></select>
    <input aria-describedby="hint">
    <p id="hint" data-hint-for="pick">Hint</p>`;
  followPicks(document);
  expect((document.getElementById("hint") as HTMLElement).hidden).toBe(true);
});

test("a button says what is typed, and 'it' while nothing is", () => {
  document.body.innerHTML = `
    <input id="left-7" value="250">
    <button>Set to <span data-follows="left-7" data-unit="m">it</span></button>
    <span data-follows="nowhere">it</span>`;
  followTyped(document);
  const button = document.querySelector("button") as HTMLButtonElement;
  expect(button.textContent).toBe("Set to 250 m");
  const field = document.getElementById("left-7") as HTMLInputElement;
  field.value = " 40 ";
  field.dispatchEvent(new Event("input"));
  expect(button.textContent).toBe("Set to 40 m");
  // Ungrouped, as the page's words write it (inventory-tng-ua04).
  field.value = "1234.5";
  field.dispatchEvent(new Event("input"));
  expect(button.textContent).toBe("Set to 1234.5 m");
  for (const typed of ["", "abc", "4 0"]) {
    field.value = typed;
    field.dispatchEvent(new Event("input"));
    expect(button.textContent).toBe("Set to it");
  }
});

test("with no unit, the number alone", () => {
  document.body.innerHTML = `
    <input id="n" value="3">
    <button>Set to <span data-follows="n">it</span></button>`;
  followTyped(document);
  expect((document.querySelector("button") as HTMLElement).textContent).toBe(
    "Set to 3",
  );
});

test("a button says the number in the page's decimal separator (inventory-tng-qnwz)", () => {
  const root = document.createElement("html");
  root.dataset.decimal = ",";
  takeSeparatorFrom(root);
  try {
    document.body.innerHTML = `
      <input id="left-7" value="1502,50">
      <button>Set to <span data-follows="left-7" data-unit="m">it</span></button>`;
    followTyped(document);
    // As a box writes it: no grouping.
    expect((document.querySelector("button") as HTMLElement).textContent).toBe(
      "Set to 1502,5 m",
    );
  } finally {
    takeSeparatorFrom(document.createElement("html"));
  }
});

test("what a field held shows once it is changed, and hides back", () => {
  // Described by it only while shown, after its unit (inventory-tng-suqp).
  document.body.innerHTML = `
    <input id="count-3" value="305" data-was="305" aria-describedby="unit-count-3">
    <span id="unit-count-3">m</span>
    <p id="count-3-was" hidden>Was 305 m</p>
    <input id="lonely" value="1" data-was="1">`;
  followWas(document);
  const field = document.getElementById("count-3") as HTMLInputElement;
  const was = document.getElementById("count-3-was") as HTMLElement;
  const described = () => field.getAttribute("aria-describedby");
  field.value = "300";
  field.dispatchEvent(new Event("input"));
  expect(was.hidden).toBe(false);
  expect(described()).toBe("unit-count-3 count-3-was");
  field.value = "301";
  field.dispatchEvent(new Event("input"));
  expect(described()).toBe("unit-count-3 count-3-was");
  field.value = "305";
  field.dispatchEvent(new Event("input"));
  expect(was.hidden).toBe(true);
  expect(described()).toBe("unit-count-3");
});

test("a field sent back changed loses its was words once put back", () => {
  document.body.innerHTML = `
    <input id="count-3" value="300" data-was="305" aria-describedby="count-3-problem unit-count-3 count-3-was">
    <p id="count-3-was">Was 305 m</p>`;
  followWas(document);
  const field = document.getElementById("count-3") as HTMLInputElement;
  field.value = "305";
  field.dispatchEvent(new Event("input"));
  expect(field.getAttribute("aria-describedby")).toBe(
    "count-3-problem unit-count-3",
  );
});

/** A count of 305 m as the page sends it, its was words hidden. */
function sentCount() {
  document.body.innerHTML = `
    <input id="count-3" value="305" data-was="305" aria-describedby="unit-count-3">
    <span id="unit-count-3">m</span>
    <p id="count-3-was" hidden>Was 305 m</p>`;
  return {
    field: document.getElementById("count-3") as HTMLInputElement,
    was: document.getElementById("count-3-was") as HTMLElement,
  };
}

test("a count the browser put back changed shows its was words at once", () => {
  const { field, was } = sentCount();
  // As a browser restoring a form does: the value, with no input event.
  field.value = "300";
  followWas(document);
  expect(was.hidden).toBe(false);
  expect(field.getAttribute("aria-describedby")).toBe(
    "unit-count-3 count-3-was",
  );
});

test("a count put back on Back from a kept page is checked again", () => {
  const { field, was } = sentCount();
  followWas(document);
  expect(was.hidden).toBe(true);
  field.value = "300";
  window.dispatchEvent(new Event("pageshow"));
  expect(was.hidden).toBe(false);
  expect(field.getAttribute("aria-describedby")).toBe(
    "unit-count-3 count-3-was",
  );
  field.value = "305";
  window.dispatchEvent(new Event("pageshow"));
  expect(was.hidden).toBe(true);
  expect(field.getAttribute("aria-describedby")).toBe("unit-count-3");
});

test("with nothing typed, the words the page sent", () => {
  // The server says "it" in the page's language (stock/length_checks.html).
  document.body.innerHTML = `
    <input id="n" value="3">
    <button>Régler sur <span data-follows="n">ça</span></button>`;
  followTyped(document);
  const field = document.getElementById("n") as HTMLInputElement;
  field.value = "";
  field.dispatchEvent(new Event("input"));
  expect((document.querySelector("button") as HTMLElement).textContent).toBe(
    "Régler sur ça",
  );
});

test("the unit of the item picked follows its box", () => {
  document.body.innerHTML = `
    <select name="item_id">
      <option value="">Pick one</option>
      <option value="1" data-unit="m">Cable (m)</option>
      <option value="2" data-unit="each">Radio (each)</option>
    </select>
    <label>How much <input name="amount" aria-describedby="unit-amount"><span id="unit-amount" aria-hidden="true" data-unit-of="item_id"></span></label>
    <span id="lonely" data-unit-of="nothing">kept</span>`;
  followUnits(document);
  const label = document.querySelector("label") as HTMLLabelElement;
  expect(label.textContent).toBe("How much ");
  pick("1");
  expect(label.textContent).toBe("How much m");
  pick("2");
  expect(label.textContent).toBe("How much each");
  pick("");
  expect(label.textContent).toBe("How much ");
  expect(document.getElementById("lonely")?.textContent).toBe("kept");
});

test("Reorder at's unit and keypad follow the item's unit chosen", () => {
  document.body.innerHTML = `
    <select name="unit">
      <option data-unit="m" data-inputmode="decimal" selected>m</option>
      <option data-unit="each" data-inputmode="numeric">each</option>
    </select>
    <input name="minimum" inputmode="decimal" aria-describedby="unit-minimum"><span id="unit-minimum" aria-hidden="true" data-unit-of="unit">m</span>`;
  followUnits(document);
  const box = document.querySelector("input") as HTMLInputElement;
  const words = document.getElementById("unit-minimum") as HTMLElement;
  pick("each");
  expect(words.textContent).toBe("each");
  expect(box.inputMode).toBe("numeric");
  pick("m");
  expect(words.textContent).toBe("m");
  expect(box.inputMode).toBe("decimal");
});

test("a box whose item's options give no keypad keeps its own", () => {
  document.body.innerHTML = `
    <select name="item_id">
      <option value="1" data-unit="each">Radio (each)</option>
    </select>
    <input name="amount" inputmode="decimal" aria-describedby="unit-amount"><span id="unit-amount" data-unit-of="item_id"></span>`;
  followUnits(document);
  pick("1");
  expect((document.querySelector("input") as HTMLInputElement).inputMode).toBe(
    "decimal",
  );
});

test("the unit of an item picked when the page is shown is there at once", () => {
  document.body.innerHTML = `
    <select name="item_id">
      <option value="1" data-unit="m">Cable (m)</option>
      <option value="2" data-unit="ft" selected>Cord (ft)</option>
    </select>
    <span data-unit-of="item_id"></span>`;
  followUnits(document);
  expect(document.querySelector("span")?.textContent).toBe("ft");
});
