/**
 * Packages on the scan page (docs/decisions/0020): a package counted is
 * saved as sealed packages of it, so stock keeps them sealed where they
 * arrive (inventory-tng-8i4.133.2).
 */
import { expect, test } from "@playwright/test";

test("a package counted on the scan page is saved sealed", async ({ page }) => {
  await page.goto("/scan");
  await page.getByLabel("Your name").selectOption({ label: "Sam Example" });
  await page
    .getByLabel("From", { exact: true })
    .selectOption({ label: "Supernode 1 › Shelf A" });
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Van" });
  await page.getByLabel(/Find an item/).fill("Cat6");
  await page.getByRole("button", { name: "Outdoor Cat6 cable" }).click();
  // Picking it adds a metre; a box instead.
  await page.getByLabel(/^Box of 305 m/).fill("1");
  await page.getByLabel(/^Loose/).fill("");
  const saving = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/transactions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save" }).click();
  const response = await saving;
  expect(response.status()).toBe(201);
  expect(response.request().postDataJSON().lines).toEqual([
    {
      item_id: expect.any(Number),
      quantity: "305",
      package_id: expect.any(Number),
    },
  ]);
});
