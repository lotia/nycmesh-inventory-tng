/**
 * Retiring is one tap, undone from its notice; with changes not yet saved,
 * the page asks first (docs/decisions/0019). Each test retires a volunteer
 * of its own (own-rows.ts), and brings them back.
 */
import { expect } from "@playwright/test";
import { SETTLE_MS } from "../src/shared/settling";
import { test } from "./csp-violations";
import { ownVolunteer } from "./own-rows";

test("a page with unsaved changes asks before retiring", async ({ page }) => {
  const was = await ownVolunteer(page);
  const name = page.getByLabel(/^Name/);
  await name.fill(`${was} (typed)`);
  const retire = page.getByRole("button", { name: "Retire", exact: true });
  await retire.click();
  const asked = page.getByRole("dialog");
  await expect(asked).toContainText("changes you haven't saved");
  await asked.getByRole("button", { name: "Go back" }).click();
  await expect(asked).toHaveCount(0);
  await expect(name).toHaveValue(`${was} (typed)`);

  // Taps wait a moment once the question goes (docs/decisions/0019,
  // Double taps).
  await page.waitForTimeout(SETTLE_MS);
  await retire.click();
  await page.getByRole("button", { name: "Retire anyway" }).click();
  await expect(page.getByRole("status")).toContainText("Retired.");
  await page.getByRole("button", { name: "Bring them back" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(was);
});

test("a double tap on Go back closes the question and does nothing more (inventory-tng-r3jx)", async ({
  page,
}) => {
  const was = await ownVolunteer(page);
  const name = page.getByLabel(/^Name/);
  await name.fill(`${was} (typed)`);
  const retire = page.getByRole("button", { name: "Retire", exact: true });
  await retire.click();
  const back = page
    .getByRole("dialog")
    .getByRole("button", { name: "Go back" });
  const box = await back.boundingBox();
  if (box === null) {
    throw new Error("Go back is not shown");
  }
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  const url = page.url();
  await page.touchscreen.tap(x, y);
  await page.waitForTimeout(150);
  await page.touchscreen.tap(x, y);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(retire).toBeFocused();
  // Nothing under Go back was tapped: no form sent, the change still there.
  expect(page.url()).toBe(url);
  await expect(name).toHaveValue(`${was} (typed)`);
});

test("with nothing unsaved, retiring asks nothing", async ({ page }) => {
  const name = await ownVolunteer(page);
  await page.getByRole("button", { name: "Retire", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Retired.");
  await page.getByRole("button", { name: "Bring them back" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
});
