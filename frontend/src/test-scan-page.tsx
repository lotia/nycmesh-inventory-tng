/** What the scan page's tests (ScanPage.*.test.tsx) share. Never bundled.
 * Each of them mocks ./camera itself, as only a test file can, and
 * importing this module makes `camera` the fake that mock opens. */
import { fireEvent, render, screen } from "@testing-library/preact";
import { expect, vi } from "vitest";
import { openCamera } from "./camera";
import { STORAGE_KEY as CART_KEY } from "./cart";
import { say } from "./i18n";
import { Outbox } from "./outbox";
import { FIND, FROM, RECORD, ScanPage, TO, YOUR_NAME } from "./ScanPage";
import { fakeCamera } from "./test-camera";
import { CATALOGUE } from "./test-catalogue";
import { answering, json, saved } from "./test-fetch";

export const camera = fakeCamera();
vi.mocked(openCamera).mockImplementation(camera.open);

export const isCatalogue = (request: Request) =>
  new URL(request.url).pathname === "/api/catalogue";

/** An API with the test catalogue, saving batches as transaction 77. */
export function server(
  save: () => Response | Promise<Response> = () => saved(),
) {
  return answering((request) =>
    isCatalogue(request) ? json(CATALOGUE, 200) : save(),
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

/** A refusal's note: only its lead in bold, and in italic only the
 * `numbers` the page wrote after it. */
export function expectRefusal(
  element: Element,
  lead: string,
  numbers: string[],
) {
  expect(element.querySelector("strong")?.textContent).toBe(lead);
  expect(
    [...element.querySelectorAll("em")].map((each) => each.textContent),
  ).toEqual(numbers);
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
  fireEvent.click(screen.getByRole("button", { name: cable }));
}

export const save = () =>
  fireEvent.click(screen.getByRole("button", { name: say(RECORD) }));

/** The cart as stored. */
export const storedCart = () =>
  JSON.parse(localStorage.getItem(CART_KEY) ?? "");

/** A batch key, as the page makes them. */
export const UUID =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
