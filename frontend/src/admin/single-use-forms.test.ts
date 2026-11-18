import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { SINGLE_USE_FIELD, sendOnce } from "./single-use-forms";

sendOnce(window);

/** Each form sent, and whether it was stopped; none is sent on. */
let sent: boolean[] = [];
function record(event: Event) {
  sent.push(event.defaultPrevented);
  event.preventDefault();
}

beforeEach(() => {
  sent = [];
  // After sendOnce's, as the browser would send it on.
  window.addEventListener("submit", record);
  document.body.innerHTML = `
    <form id="single-use">
      <input type="hidden" name="${SINGLE_USE_FIELD}" value="k">
      <input name="label">
      <button name="go" value="yes">Make</button>
      <input type="submit" value="Also">
      <button type="button">Other</button>
    </form>
    <form id="plain"><button>Save</button></form>`;
});

afterEach(() => {
  window.removeEventListener("submit", record);
  document.body.replaceChildren();
  vi.useRealTimers();
});

const form = (id: string) => document.getElementById(id) as HTMLFormElement;

test("a form shown once is sent once, its buttons then disabled", () => {
  vi.useFakeTimers();
  form("single-use").requestSubmit();
  // Not before its data is taken, so a named button still sends its value.
  expect(form("single-use").querySelector("button")?.disabled).toBe(false);
  vi.runAllTimers();
  form("single-use").requestSubmit();
  expect(sent).toEqual([false, true]);
  const disabled = [...form("single-use").elements].map(
    (control) => (control as HTMLButtonElement).disabled,
  );
  expect(disabled).toEqual([false, false, true, true, false]);
});

test("any other form is sent as often as asked", () => {
  form("plain").requestSubmit();
  form("plain").requestSubmit();
  expect(sent).toEqual([false, false]);
});

test("a form another listener stopped is not marked sent", () => {
  const stop = (event: Event) => event.preventDefault();
  document.addEventListener("submit", stop);
  form("single-use").requestSubmit();
  document.removeEventListener("submit", stop);
  expect(form("single-use").dataset.sent).toBeUndefined();
});

/** A pageshow event; happy-dom's PageTransitionEvent has no persisted. */
function shown(persisted: boolean): Event {
  return Object.assign(new Event("pageshow"), { persisted });
}

test("back to a kept page with a form sent, the page is fetched again", () => {
  const reload = vi
    .spyOn(window.location, "reload")
    .mockImplementation(() => {});
  window.dispatchEvent(shown(true));
  expect(reload).not.toHaveBeenCalled();
  form("single-use").dataset.sent = "yes";
  window.dispatchEvent(shown(false));
  expect(reload).not.toHaveBeenCalled();
  window.dispatchEvent(shown(true));
  expect(reload).toHaveBeenCalledOnce();
});
