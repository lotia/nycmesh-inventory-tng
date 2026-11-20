/**
 * Filling in the scan page as a volunteer would: the seed's volunteer and
 * shelf, a kind of save, and items picked from a search.
 */
import type { Page } from "@playwright/test";

/** The seed's volunteer and shelf, as the scan page names them. */
export const SAM = "Sam Example";
export const SHELF = "Supernode 1 › Shelf A";

/** Picks `cable` on the scan page, from a search for it. */
export async function pick(page: Page, cable: string): Promise<void> {
  await page.getByLabel(/Find an item/).fill(cable);
  await page.getByRole("button", { name: cable, exact: true }).click();
}

/** Fills in a move of `cable` from the shelf to the van, by Sam. */
export async function fillMove(page: Page, cable: string): Promise<void> {
  await page.getByLabel("Your name").selectOption({ label: SAM });
  await page.getByLabel("Moving stock").check();
  await page.getByLabel("From", { exact: true }).selectOption({ label: SHELF });
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Van" });
  await pick(page, cable);
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
