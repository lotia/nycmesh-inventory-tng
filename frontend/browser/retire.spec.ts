/**
 * Retiring is one tap, undone from its notice; with changes not yet saved,
 * the page asks first (docs/decisions/0019). Each test retires a volunteer
 * of its own (own.ts), and brings them back.
 */
import { expect, test } from "@playwright/test";
import { ownVolunteer } from "./own";

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

  await retire.click();
  await page.getByRole("button", { name: "Retire anyway" }).click();
  await expect(page.getByRole("status")).toContainText("Retired.");
  await page.getByRole("button", { name: "Bring them back" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(was);
});

test("with nothing unsaved, retiring asks nothing", async ({ page }) => {
  const name = await ownVolunteer(page);
  await page.getByRole("button", { name: "Retire", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Retired.");
  await page.getByRole("button", { name: "Bring them back" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
});
