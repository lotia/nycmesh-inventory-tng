import { beforeEach, expect, test } from "vitest";
import { takeSeparatorFrom } from "../shared/quantity";
import { followPicks, followTyped, followWas } from "./hints";

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
  document.body.innerHTML = `
    <input id="count-3" value="305" data-was="305">
    <p id="count-3-was" hidden>Was 305 m</p>
    <input id="lonely" value="1" data-was="1">`;
  followWas(document);
  const field = document.getElementById("count-3") as HTMLInputElement;
  const was = document.getElementById("count-3-was") as HTMLElement;
  field.value = "300";
  field.dispatchEvent(new Event("input"));
  expect(was.hidden).toBe(false);
  field.value = "305";
  field.dispatchEvent(new Event("input"));
  expect(was.hidden).toBe(true);
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
