/**
 * Rows a test makes for itself, through the administrator pages, so that
 * what it changes no other test reads: tests run at once, in each browser,
 * against one database (playwright.config.ts).
 */
import { randomUUID } from "node:crypto";
import { expect, type Page } from "@playwright/test";

/** A name no other test, or run of this one, has. */
function unique(what: string): string {
  return `${what} ${randomUUID().slice(0, 8)}`;
}

/** Opens a list's page and fills in a new row's name, unique. The name. */
async function named(page: Page, path: string, what: string): Promise<string> {
  const name = unique(what);
  await page.goto(path);
  await page.getByLabel(/^Name/).fill(name);
  return name;
}

/** A new volunteer, whose page is left open. Their name. */
export async function ownVolunteer(page: Page): Promise<string> {
  const name = await named(page, "/admin/volunteers", "Volunteer");
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
  return name;
}

/**
 * A new cable, in metres, that comes in a box of 305 m whose opened ones
 * are followed (docs/decisions/0020), as the seed's is. Its name.
 */
export async function ownCable(page: Page): Promise<string> {
  const name = await named(page, "/admin/items", "Cable");
  await page.getByLabel("Category").selectOption({ label: "Cables" });
  await page.getByLabel(/^Unit/).selectOption("m");
  const add = page.getByRole("button", { name: "Add", exact: true });
  await add.click();
  // Close to another test's name, it asks once.
  const heading = page.getByRole("heading", { level: 1, name, exact: true });
  const asked = page.getByText("A different item?");
  await expect(heading.or(asked)).toBeVisible();
  if (await asked.isVisible()) {
    await add.click();
  }
  await expect(heading).toBeVisible();
  const box = "Box of 305 m";
  await page.getByLabel("Package", { exact: true }).fill(box);
  await page.getByLabel("Holds").fill("305");
  await page.getByRole("button", { name: "Add package" }).click();
  await page
    .getByLabel(`When one of ${box} is opened`)
    .selectOption({ label: "Track" });
  await page.getByRole("button", { name: `Set for ${box}` }).click();
  await expect(page.getByText("Opened ones followed")).toBeVisible();
  return name;
}
