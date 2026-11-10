/**
 * Retiring is one tap, undone from its notice; with changes not yet saved,
 * the page asks first (docs/decisions/0019). Each test brings back what it
 * retires.
 */
import { expect, test } from "@playwright/test";

test("a page with unsaved changes asks before retiring", async ({ page }) => {
  await page.goto("/admin/volunteers");
  await page.getByRole("table").getByRole("link").first().click();
  const name = page.getByLabel("Name");
  const was = await name.inputValue();
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
  await page.goto("/admin/volunteers");
  const link = page.getByRole("table").getByRole("link").first();
  const name = (await link.textContent()) ?? "";
  await link.click();
  await page.getByRole("button", { name: "Retire", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Retired.");
  await page.getByRole("button", { name: "Bring them back" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
});
