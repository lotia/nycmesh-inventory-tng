import { beforeEach, expect, test } from "vitest";
import { followPicks } from "./hints";

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
