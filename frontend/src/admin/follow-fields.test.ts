import { beforeEach, expect, test } from "vitest";
import { follow } from "./follow-fields";

let box: HTMLInputElement;
let list: HTMLSelectElement;
let shows: number;

beforeEach(() => {
  document.body.innerHTML = `
    <input name="box">
    <select name="list"><option>A</option><option>B</option></select>`;
  box = document.querySelector("input") as HTMLInputElement;
  list = document.querySelector("select") as HTMLSelectElement;
  shows = 0;
  follow(document, [box, list], () => {
    shows += 1;
  });
});

test("shows at once, and on a box's input and a list's change", () => {
  expect(shows).toBe(1);
  box.dispatchEvent(new Event("input"));
  list.dispatchEvent(new Event("change"));
  expect(shows).toBe(3);
  // A list's input and a box's change come with the other, followed already.
  list.dispatchEvent(new Event("input"));
  box.dispatchEvent(new Event("change"));
  expect(shows).toBe(3);
});

test("shows on pageshow a value put back with no field's event", () => {
  // As the browser putting values back by itself does.
  box.value = "40";
  list.value = "B";
  window.dispatchEvent(new Event("pageshow"));
  expect(shows).toBe(2);
});

test("does not show again on pageshow when nothing changed", () => {
  // As every load ends, so words in a live region are not read out again.
  window.dispatchEvent(new Event("pageshow"));
  box.value = "40";
  box.dispatchEvent(new Event("input"));
  window.dispatchEvent(new Event("pageshow"));
  expect(shows).toBe(2);
});

test("shows on pageshow a tick put back, which leaves the value as it was", () => {
  document.body.innerHTML = `<input type="checkbox" name="tick">`;
  const tick = document.querySelector("input") as HTMLInputElement;
  let ticks = 0;
  follow(document, [tick], () => {
    ticks += 1;
  });
  tick.checked = true;
  window.dispatchEvent(new Event("pageshow"));
  expect(ticks).toBe(2);
});
