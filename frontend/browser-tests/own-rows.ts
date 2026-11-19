/**
 * Rows a test makes for itself, so that what it changes no other test
 * reads: tests run at once, in each browser, against one database, all
 * but the crawl (playwright.config.ts). Most through the administrator
 * pages; saves of stock through the scan page's Record button (`record`).
 */
import { randomUUID } from "node:crypto";
import {
  expect,
  type Locator,
  type Page,
  type Response,
} from "@playwright/test";
import { assertNone, test as base, watch } from "./csp-violations";
import { prepared } from "./prepared";

/**
 * A name no other test, or run of this one, has. Its end in letters only,
 * so no part of it is read as a unit after a number ("3f" as farads), which
 * the form would say under it, or ask about (docs/decisions/0021).
 */
export function unique(what: string): string {
  const id = randomUUID()
    .slice(0, 8)
    .replace(/[0-9]/g, (digit) => "ghijklmnop".charAt(Number(digit)));
  return `${what} ${id}`;
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
 * Adds the item filled in on the items page, saying it is a different item
 * if its name is close to another's, which is asked once. Waits for the
 * item's page by its heading, `shown` exactly, since the list's own heading
 * is also a level-1 heading. That heading.
 */
export async function addItem(page: Page, shown: string): Promise<Locator> {
  const add = page.getByRole("button", { name: "Add", exact: true });
  await add.click();
  const heading = page.getByRole("heading", {
    level: 1,
    name: shown,
    exact: true,
  });
  const asked = page.getByText("A different item?");
  await expect(heading.or(asked)).toBeVisible();
  if (await asked.isVisible()) {
    await add.click();
  }
  await expect(heading).toBeVisible();
  return heading;
}

/**
 * A new cable, in metres, that comes in a box of 305 m whose opened ones
 * are followed (docs/decisions/0020), as the seed's is, named `what` and
 * something unique, its page headed by it as shown, `shown` in place of
 * `what` (docs/decisions/0021, names shown). Its name as typed.
 */
export async function ownCable(
  page: Page,
  what = "Cable",
  shown = what,
): Promise<string> {
  const name = await named(page, "/admin/items", what);
  await page.getByLabel(/^Category/).selectOption({ label: "Cables" });
  await page.getByLabel(/^Unit/).selectOption("m");
  await addItem(page, name.replace(what, shown));
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
 * Invites a new administrator from `page`'s session, confirming with its
 * password if asked, and leaves the page showing their link. Their
 * username and the link.
 */
export async function invite(
  page: Page,
): Promise<{ username: string; link: string }> {
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
  await expect(made).toBeVisible();
  const link = (await made.textContent()) ?? "";
  expect(link).toContain("/admin/reset?token=");
  return { username, link };
}

/**
 * Records the scan page's cart, and gives its answer once the page lists it
 * saved: the answer reaches the test before the page has used it.
 */
export async function record(page: Page): Promise<Response> {
  const saving = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/transactions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Record" }).click();
  const response = await saving;
  // Refused, it has no transaction to wait for: said now, not timed out.
  expect(response.ok()).toBe(true);
  const saved = (await response.json()).transaction_id as number;
  await expect(
    page.getByText(`Recorded (transaction ${saved}).`),
  ).toBeVisible();
  return response;
}

/**
 * A test with `ownAdmin`: a new administrator, invited from the session
 * every test shares (`invite`), signed in in a browser of their own, closed
 * after. Nothing was confirmed in their session since signing in, as no
 * other test, nor another run of this one, uses it. Their page, at their
 * home page, and their password. Signing in and using a link are rate
 * limited by address, which the tests' server sets high enough for tests
 * repeated (--repeat-each, browser_database.py).
 */
export const test = base.extend<{
  ownAdmin: { page: Page; password: string };
}>({
  ownAdmin: async ({ page, browser, javaScriptEnabled }, use) => {
    const { username, link } = await invite(page);
    const context = await browser.newContext({
      // Not the shared session, which a new context otherwise starts with.
      storageState: { cookies: [], origins: [] },
    });
    const violations = await watch(context);
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
    try {
      await assertNone(context, violations, javaScriptEnabled);
    } finally {
      await context.close();
    }
  },
});
