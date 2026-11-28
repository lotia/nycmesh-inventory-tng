/**
 * A browser set to German, whose Accept-Language the server picks the
 * pages' decimal separator from (web/decimal_separator.py): number boxes
 * take a comma, refuse a point naming the comma, and fill and save as
 * shown (Ali, 2026-11-23, inventory-tng-qnwz). The item is the test's own
 * (own-rows.ts).
 */
import { expect, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { addItem, record, unique } from "./own-rows";
import { fillMove } from "./scan-page";
import { settled } from "./taps";

test.use({ locale: "de-DE" });

const USE_COMMA = "Use “,” for decimals, such as 0,5.";

/** A new cable, in metres, whose page is left open. Its name. */
async function ownWire(page: Page): Promise<string> {
  const name = unique("Wire");
  await page.goto("/admin/items");
  await page.getByLabel(/^Name/).fill(name);
  await page.getByLabel(/^Category/).selectOption({ label: "Cables" });
  await page.getByLabel(/^Unit/).selectOption("m");
  await addItem(page, name);
  return name;
}

test("an item's Reorder at takes a comma, and shows it so", async ({
  page,
}) => {
  await ownWire(page);
  const reorder = page.getByLabel(/^Reorder at/);
  const save = page.getByRole("button", { name: "Save", exact: true });
  await reorder.fill("2.5");
  await save.click();
  await expect(page.getByText(USE_COMMA)).toBeVisible();
  await reorder.fill("2,5");
  // Each a form's answer, whose taps wait a moment (docs/decisions/0019,
  // Double taps).
  await settled(page);
  await save.click();
  await expect(page).toHaveURL(/notice=saved/);
  await expect(reorder).toHaveValue("2,5");
  // Sent again as shown, it saves.
  await settled(page);
  await save.click();
  await expect(page).toHaveURL(/notice=saved/);
  await expect(reorder).toHaveValue("2,5");
});

test("the scan page takes a comma, and refuses a point naming it", async ({
  page,
}) => {
  const name = await ownWire(page);
  await page.goto("/scan");
  await fillMove(page, name);
  const loose = page.getByLabel("How many m");
  await loose.fill("1.5");
  await page.getByRole("button", { name: "Record" }).click();
  await expect(page.getByText(USE_COMMA).first()).toBeVisible();
  await loose.fill("1,5");
  const response = await record(page);
  expect(response.request().postDataJSON().lines).toEqual([
    expect.objectContaining({ quantity: "1.5", unit: "m" }),
  ]);
});
