/**
 * Filling in the scan page as a volunteer would: the seed's volunteer and
 * shelf, a kind of save, and items picked from a search; and the cart it
 * saved last, as the outbox lists it.
 */
import type { Locator, Page } from "@playwright/test";
import { settled } from "./taps";

/** The seed's volunteer and shelf, as the scan page names them. */
export const SAM = "Sam Example";
export const SHELF = "Supernode 1 › Shelf A";

/** Picks `cable` on the scan page, from a search for it; then waits as
 * the page ignores taps a moment, as the matches went (docs/decisions/0019,
 * Double taps), so the next tap is taken. */
export async function pick(page: Page, cable: string): Promise<void> {
  await page.getByLabel(/Find an item/).fill(cable);
  await page.getByRole("button", { name: cable, exact: true }).click();
  await settled(page);
}

/** The newest of the carts this device saved, as the page lists them. */
export function newestCart(page: Page): Locator {
  return page
    .locator("section", {
      has: page.getByRole("heading", { name: "Carts from this device" }),
    })
    .getByRole("listitem")
    .first();
}

/** Fills in a move from the shelf to the van, by Sam, with no items. */
export async function startMove(page: Page): Promise<void> {
  await page.getByLabel("Your name").selectOption({ label: SAM });
  await page.getByLabel("Moving stock").check();
  await page.getByLabel("From", { exact: true }).selectOption({ label: SHELF });
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Van" });
}

/** Fills in a move of `cable` from the shelf to the van, by Sam. */
export async function fillMove(page: Page, cable: string): Promise<void> {
  await startMove(page);
  await pick(page, cable);
}

/** The loose box of `item`'s cart line, the last box on it. */
export function looseBox(page: Page, item: string): Locator {
  return page
    .getByRole("listitem")
    .filter({ has: page.getByText(item, { exact: true }) })
    .getByRole("textbox")
    .last();
}

/** Fills in an opening of `cable` on the shelf, by Sam. */
export async function fillOpening(page: Page, cable: string): Promise<void> {
  await page.getByLabel("Your name").selectOption({ label: SAM });
  await page.getByLabel("Opening packages").check();
  await page
    .getByLabel("Where", { exact: true })
    .selectOption({ label: SHELF });
  await pick(page, cable);
}
