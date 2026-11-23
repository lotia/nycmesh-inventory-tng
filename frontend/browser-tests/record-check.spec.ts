/**
 * Record checks the cart against the catalogue as it is now before it
 * queues it: an amount typed in a unit an administrator changed since is
 * cleared, said beside its box, and nothing sent until it is typed again
 * (Ali, 2026-11-23, inventory-tng-h7ua). The item is the test's own
 * (own-rows.ts), with no stock moved, so its unit may still change.
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";
import { addItem, itemIdOf, record, unique } from "./own-rows";
import { fillMove } from "./scan-page";

test("an amount typed in a unit changed since is asked for again at Record, and recorded in the new unit", async ({
  page,
  context,
}) => {
  const name = unique("Wire");
  await page.goto("/admin/items");
  await page.getByLabel(/^Name/).fill(name);
  await page.getByLabel(/^Category/).selectOption({ label: "Cables" });
  await page.getByLabel(/^Unit/).selectOption("m");
  await addItem(page, name);
  const itemPage = page.url();
  const itemId = itemIdOf(page);

  await page.goto("/scan");
  await fillMove(page, name);
  await page.getByLabel("How many m").fill("50");

  // Meanwhile, an administrator counts it in feet.
  const admin = await context.newPage();
  await admin.goto(itemPage);
  await admin.getByLabel(/^Unit/).selectOption("ft");
  await admin.getByRole("button", { name: "Save", exact: true }).click();
  await expect(admin).toHaveURL(/notice=saved/);
  await admin.close();

  let sent = 0;
  page.on("request", (request) => {
    if (request.url().endsWith("/api/transactions")) {
      sent += 1;
    }
  });
  await page.getByRole("button", { name: "Record" }).click();
  await expect(
    page.getByText(
      "The unit changed from m to ft. Enter the amount again in ft.",
    ),
  ).toBeVisible();
  await expect(page.getByLabel("How many ft")).toHaveValue("");
  expect(sent).toBe(0);

  // Typed again, in feet, it is recorded in feet.
  await page.getByLabel("How many ft").fill("40");
  const response = await record(page);
  expect(response.request().postDataJSON().lines).toEqual([
    expect.objectContaining({ item_id: itemId, quantity: "40", unit: "ft" }),
  ]);
});
