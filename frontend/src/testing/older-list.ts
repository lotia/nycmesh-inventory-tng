/** What an older build left, as the scan page's tests leave it and find
 * it listed (ScanPage.older-build-*.test.tsx). Never bundled. */
import { screen, within } from "@testing-library/preact";
import { ADDED_BACK as ADDED_BACK_PHRASE } from "../scan/CartForm";
import { DISCARD } from "../scan/Notes";
import { CHECKED_OUT, NOT_KEPT as NOT_KEPT_PHRASE } from "../scan/OlderList";
import { say } from "../shared/i18n";
import { KEY, keep, keepOlderCart, moveBody, olderSave } from "./saves";

/** The heading of the list of what older builds left. */
export const HEADING = say(CHECKED_OUT);
/** What the cart says once something an older build left goes in it. */
export const ADDED_BACK = say(ADDED_BACK_PHRASE);
/** The browser would not keep the cart: a failure, in bold (Ali,
 * inventory-tng-93z). */
export const NOT_KEPT = say(NOT_KEPT_PHRASE);
/** When what the tests leave was kept. */
export const AT = 1_700_000_000_000;

/** A move of two boxes of radios and some cable, waiting under v3, but
 * for `fields`, and `body` in its body. */
export function leaveSave(fields = {}, body = {}) {
  keep("inventory.outbox.v3", [
    olderSave(
      moveBody({
        key: KEY,
        lines: [
          { item_id: 100, quantity: "8", package_id: 1000 },
          { item_id: 101, quantity: "3", open_package_key: KEY },
        ],
        ...body,
      }),
      { what: "Move: 2 items from Hub to Van", queuedAt: AT, ...fields },
    ),
  ]);
}

/** A cart filled for an install, under v1, with a line no build wrote:
 * not whole, so it waits for the volunteer. */
export function leaveCart(fields = {}) {
  keepOlderCart({
    kind: "install",
    volunteerId: 2,
    fromId: 12,
    installNumber: "456",
    lines: [{ itemId: 101, packages: {}, loose: "12.5" }, { loose: 1 }],
    usedAt: AT,
    ...fields,
  });
}

/** The list of what older builds left. */
export const listing = () =>
  screen
    .getByRole("heading", { name: HEADING })
    .closest("section") as HTMLElement;

/** A note under a line, in the alert's style, which says `text`, its
 * bold words among the rest. */
export const noted =
  (text: string) =>
  (_: string, element: Element | null): boolean =>
    element?.classList.contains("alert") === true &&
    element.textContent === text;

/** The Discard of a whole entry, `in` the listing's first by default,
 * after any of its lines' own; once tapped, the one confirming it. */
export const lastDiscard = (entry: HTMLElement = listing()) =>
  within(entry)
    .getAllByRole("button", { name: say(DISCARD) })
    .at(-1) as HTMLElement;
/** The Discard of the listed line that says `words`. */
export const discardOf = (words: string) =>
  within(
    within(listing()).getByText(words).closest("li") as HTMLElement,
  ).getByRole("button", { name: say(DISCARD) });

/** The cart's boxes of radios, as the field shows them. */
export const boxes = () =>
  (screen.getByLabelText("Box (4 each)") as HTMLInputElement).value;
