import { beforeEach, expect, test } from "vitest";
import { followPicks, followTyped } from "./hints";

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
