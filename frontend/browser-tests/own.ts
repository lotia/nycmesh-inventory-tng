/**
 * Rows a test makes for itself, through the administrator pages, so that
 * what it changes no other test reads: tests run at once, in each browser,
 * against one database (playwright.config.ts).
 */
import { randomUUID } from "node:crypto";
import { test as base, expect, type Page } from "@playwright/test";
import { prepared } from "./prepared";

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
 * are followed (docs/decisions/0020), as the seed's is, named `what` and
 * something unique. Its name.
 */
export async function ownCable(page: Page, what = "Cable"): Promise<string> {
  const name = await named(page, "/admin/items", what);
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

/**
 * A test with `ownAdmin`: a new administrator, invited from the session
 * every test shares (confirming with its password if asked), signed in in a
 * browser of their own, closed after. Nothing was confirmed in their
 * session since signing in, as no other test, nor another run of this one,
 * uses it. Their page, at their home page, and their password. Signing
 * in and using a link are rate limited by address, which the tests' server
 * sets high enough for tests repeated (--repeat-each, browser_database.py).
 */
export const test = base.extend<{
  ownAdmin: { page: Page; password: string };
}>({
  ownAdmin: async ({ page, browser }, use) => {
    const username = `admin-${randomUUID().slice(0, 8)}`;
    await page.goto("/admin/admins");
    await page.getByLabel("Username").fill(username);
    const asked = page.getByLabel("Your password");
    const shared = prepared().password;
    if (await asked.isVisible()) {
      await asked.fill(shared);
    }
    await page.getByRole("button", { name: "Invite" }).click();
    // Or asked once it is sent, as the code's time ran out meanwhile.
    const made = page.locator(".key");
    const confirm = page.getByRole("button", { name: "Confirm" });
    await expect(made.or(confirm)).toBeVisible();
    if (await confirm.isVisible()) {
      await asked.fill(shared);
      await confirm.click();
    }
    const link = (await made.textContent()) ?? "";
    expect(link).toContain("/admin/reset?token=");

    const context = await browser.newContext({
      // Not the shared session, which a new context otherwise starts with.
      storageState: { cookies: [], origins: [] },
    });
    const admin = await context.newPage();
    const password = randomUUID();
    await admin.goto(link);
    await admin.getByLabel(/^New password(?! again)/).fill(password);
    await admin.getByLabel("New password again").fill(password);
    await admin.getByRole("button", { name: "Reset" }).click();
    await admin.getByLabel("Username").fill(username);
    await admin.getByLabel("Password").fill(password);
    await admin.getByRole("button", { name: "Sign in" }).click();
    await expect(admin).toHaveURL(/\/admin$/);
    await use({ page: admin, password });
    await context.close();
  },
});
