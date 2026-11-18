import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ONCE_FIELD, sendOnce } from "./once";

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
    <form id="once">
      <input type="hidden" name="${ONCE_FIELD}" value="k">
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
  form("once").requestSubmit();
  // Not before its data is taken, so a named button still sends its value.
  expect(form("once").querySelector("button")?.disabled).toBe(false);
  vi.runAllTimers();
  form("once").requestSubmit();
  expect(sent).toEqual([false, true]);
  const disabled = [...form("once").elements].map(
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
  form("once").requestSubmit();
  document.removeEventListener("submit", stop);
  expect(form("once").dataset.sent).toBeUndefined();
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
  form("once").dataset.sent = "yes";
  window.dispatchEvent(shown(false));
  expect(reload).not.toHaveBeenCalled();
  window.dispatchEvent(shown(true));
  expect(reload).toHaveBeenCalledOnce();
});
