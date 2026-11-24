/** Label pages, made from an item's page as an administrator. */
import { expect, type Page } from "@playwright/test";
import { prepared } from "./prepared";

/** Prints `copies` labels of the item named `name` (the seed's labelled
 * item unless given), from its page. */
export async function printLabels(
  page: Page,
  copies: number,
  name = prepared().labelled,
): Promise<void> {
  // Found by name: other tests add items, which may push it off a page.
  await page.goto(`/admin/items?q=${encodeURIComponent(name)}`);
  await page.getByRole("link", { name, exact: true }).click();
  await printFromHere(page, copies);
}

/** Prints `copies` labels from the item's or place's page that is open. */
export async function printFromHere(page: Page, copies: number): Promise<void> {
  await page.getByLabel("Copies").fill(String(copies));
  await page.getByRole("button", { name: "Print labels" }).click();
  await expect(
    page.getByRole("heading", { name: "Print labels" }),
  ).toBeVisible();
}
