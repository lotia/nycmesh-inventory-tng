/** What the scan page's tests (ScanPage.*.test.tsx) share. Never bundled.
 * Each of them mocks ./camera itself, as only a test file can, and
 * importing this module makes `camera` the fake that mock opens. */
import { fireEvent, render, screen } from "@testing-library/preact";
import { expect, vi } from "vitest";
import { FROM, RECORD, TO, YOUR_NAME } from "../scan/CartForm";
import { openCamera } from "../scan/camera";
import { STORAGE_KEY as CART_KEY, type Catalogue } from "../scan/cart";
import { FIND } from "../scan/Finder";
import { ScanPage } from "../scan/ScanPage";
import { say } from "../shared/i18n";
import { Outbox } from "../shared/outbox";
import { SETTLE_MS, stopIgnoringTaps } from "../shared/settling";
import { fakeCamera } from "./camera";
import { CATALOGUE } from "./catalogue";
import { answering, json, saved } from "./fetch";

export const camera = fakeCamera();
vi.mocked(openCamera).mockImplementation(camera.open);

export const isCatalogue = (request: Request) =>
  new URL(request.url).pathname === "/api/catalogue";

/** An API with `catalogue`, the test one unless another is given, saving
 * batches as transaction 77 unless `save` answers otherwise. */
export function server(
  save: () => Response | Promise<Response> = saved,
  catalogue: Catalogue = CATALOGUE,
) {
  return answering((request) =>
    isCatalogue(request) ? json(catalogue, 200) : save(),
  );
}

export function open(api = server().api, reload = vi.fn()) {
  const outbox = new Outbox(api);
  render(<ScanPage api={api} outbox={outbox} reload={reload} />);
  return outbox;
}

/** That `element`, a failure or refusal note, says `text` in bold as a
 * whole, without "Sorry.", and its numbers, `numbers`, in bold italic
 * (Ali, inventory-tng-93z). */
export function expectBold(
  element: Element | null,
  text: string,
  numbers: string[] = [],
) {
  const bold = element?.querySelector("strong");
  expect(bold?.textContent).toBe(text);
  expect(
    [...(bold?.querySelectorAll("em") ?? [])].map((each) => each.textContent),
  ).toEqual(numbers);
}

/** A refusal's note: only its lead in bold, in italic only the `numbers`
 * the page wrote after it, and each of its `problems` on a line of its
 * own. */
export function expectRefusal(
  element: Element,
  lead: string,
  numbers: string[],
  problems: string[],
) {
  expect(element.querySelector("strong")?.textContent).toBe(lead);
  expect(
    [...element.querySelectorAll("em")].map((each) => each.textContent),
  ).toEqual(numbers);
  expect(
    [...element.querySelectorAll("li")].map((each) => each.textContent),
  ).toEqual(problems);
}

export const pick = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

export const selected = (label: string) =>
  (screen.getByLabelText(label) as HTMLSelectElement).value;

export const saves = (sent: Request[]) =>
  sent.filter((request) => request.url.endsWith("/api/transactions"));

export async function ready() {
  await screen.findByLabelText(say(YOUR_NAME));
}

/** The page's statuses with words: the status lines under the camera
 * and the item search stay on the page, empty, once they have none. */
export const statuses = () =>
  screen.queryAllByRole("status").filter((each) => each.textContent !== "");

/** The page's one status with words. */
export function status(): HTMLElement {
  const [only, ...more] = statuses();
  if (only === undefined || more.length > 0) {
    throw new Error(`${statuses().length} statuses with words`);
  }
  return only;
}

/** A tap on `element` a moment after the one before, as a volunteer's
 * next tap comes: taps that one left ignored, as it moved what was below
 * (shared/settling.ts, ignoreTaps), are taken again. A double tap's second
 * tap is fireEvent.click, at once. */
export function tap(element: Element) {
  stopIgnoringTaps(document);
  fireEvent.click(element);
}

/** That `space`, kept where a tap took something away, is blank, `height`
 * px high and hidden from screen readers; that a tap on it, focus having
 * left for the page, puts focus back on `refocused()`; and that it goes
 * once taps are taken again (scan/settling-hooks.tsx). With fake timers. */
export async function expectSpaceBlank(
  space: HTMLElement,
  height: number,
  refocused: () => Element | null,
) {
  expect(space.getAttribute("aria-hidden")).toBe("true");
  expect(space.style.height).toBe(`${height}px`);
  expect(space.textContent).toBe("");
  (document.activeElement as HTMLElement).blur();
  fireEvent.click(space, { detail: 1 });
  expect(document.activeElement).toBe(refocused());
  await vi.advanceTimersByTimeAsync(SETTLE_MS);
  expect(document.contains(space)).toBe(false);
}

export function find(text: string) {
  fireEvent.input(screen.getByLabelText(say(FIND)), {
    target: { value: text },
  });
}

/** A move from the hub to the van, of one cable, by Ada. */
export function fillMove(cable = "Cat6 cable") {
  pick(say(YOUR_NAME), "1");
  pick(say(FROM), "10");
  pick(say(TO), "12");
  find("cable");
  tap(screen.getByRole("button", { name: cable }));
}

export const save = () =>
  tap(screen.getByRole("button", { name: say(RECORD) }));

/** Record, then wait until `outbox` holds `count` batches: Record asks for
 * the catalogue before it queues the cart (inventory-tng-h7ua). */
export async function saveQueued(outbox: Outbox, count: number) {
  save();
  await vi.waitFor(() => expect(outbox.entries.value).toHaveLength(count));
}

/** The cart as stored. */
export const storedCart = () =>
  JSON.parse(localStorage.getItem(CART_KEY) ?? "");

/** A batch key, as the page makes them. */
export const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
