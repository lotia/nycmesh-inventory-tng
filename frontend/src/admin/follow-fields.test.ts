import { afterEach, expect, test } from "vitest";
import { follow } from "./follow-fields";

let shows = 0;
const show = () => {
  shows += 1;
};

afterEach(() => {
  window.removeEventListener("pageshow", show);
  shows = 0;
});

test("shows at once, on a box's input and a list's change, and on pageshow", () => {
  document.body.innerHTML = `
    <input name="box">
    <select name="list"><option>A</option></select>`;
  const box = document.querySelector("input") as HTMLInputElement;
  const list = document.querySelector("select") as HTMLSelectElement;
  follow(document, [box, list], show);
  expect(shows).toBe(1);
  box.dispatchEvent(new Event("input"));
  list.dispatchEvent(new Event("change"));
  expect(shows).toBe(3);
  // A list's input and a box's change come with the other, followed already.
  list.dispatchEvent(new Event("input"));
  box.dispatchEvent(new Event("change"));
  expect(shows).toBe(3);
  // As the browser putting values back by itself does: no field's event.
  window.dispatchEvent(new Event("pageshow"));
  expect(shows).toBe(4);
});
