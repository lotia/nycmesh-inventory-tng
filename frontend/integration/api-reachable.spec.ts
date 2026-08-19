import { type BrowserContext, expect, type Page, test } from "@playwright/test";

/**
 * Can a browser actually use this API?
 *
 * Nothing else in the project can answer that; the reasoning, and the setup
 * these tests need, are in DEVELOPERS.md "Integration tests".
 */

const ADMIN_LOGIN = "http://localhost:8000/admin/login/";
const USERNAME = "integration";
const PASSWORD = "integration-only-not-a-real-password";

/** Read a cookie the browser is holding for this origin. */
async function cookie(context: BrowserContext, name: string) {
  const cookies = await context.cookies();
  return cookies.find((c) => c.name === name)?.value;
}

/**
 * Log in through the admin, because the app has no login of its own yet: which
 * posture it takes is inventory-tng-0pj. What these tests prove is the write
 * path, not the way in.
 *
 * Waits for the session to actually exist. Without that, a test asserting a
 * refusal cannot tell "rejected for a missing CSRF token" from "rejected for
 * not being logged in yet".
 */
async function login(page: Page) {
  await page.goto(ADMIN_LOGIN);
  await page.getByLabel(/username/i).fill(USERNAME);
  await page.getByLabel(/password/i).fill(PASSWORD);
  await page.getByRole("button", { name: /log in/i }).click();
  await expect(page).toHaveURL(/\/admin\/$/);
}

test("the app loads", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /nyc mesh inventory/i })).toBeVisible();
});

test("the browser reaches the API through the dev proxy on its own origin", async ({ page }) => {
  await page.goto("/");
  // Relative path on purpose: application code must never carry an API base
  // URL, so this is the only shape worth testing. See the frontend skill.
  const response = await page.evaluate(async () => {
    const result = await fetch("/api");
    return { status: result.status, body: await result.json() };
  });

  expect(response.status).toBe(200);
  expect(Object.keys(response.body)).toContain("volunteers");
});

test("an authenticated browser can record a batch", async ({ page, context }) => {
  await login(page);

  await page.goto("/");
  await page.evaluate(() => fetch("/api"));
  const token = await cookie(context, "csrftoken");
  expect(token).toBeTruthy();

  // The item and location are the first rows `seed_integration_data` creates,
  // so this assumes the freshly migrated database CI gives it. Nothing reads
  // them back by name yet -- there is no catalogue endpoint (inventory-tng-vr8
  // and the read API in docs/architecture.md).
  const result = await page.evaluate(async (csrf) => {
    const volunteers = await (await fetch("/api/volunteers")).json();
    const response = await fetch("/api/stock/transactions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf as string },
      body: JSON.stringify({
        kind: "count",
        actor: volunteers.results[0].id,
        movements: [{ item: 1, quantity: "1", to_location: 1 }],
      }),
    });
    return { status: response.status, body: await response.json() };
  }, token);

  expect(result.status, JSON.stringify(result.body)).toBe(201);
  expect(result.body.movements).toHaveLength(1);
});

test("a write without the CSRF token is refused", async ({ page }) => {
  // The protection is real and must stay real: this is what stops another
  // site posting to the ledger using a volunteer's session.
  await login(page);

  await page.goto("/");
  const result = await page.evaluate(async () => {
    const response = await fetch("/api/stock/transactions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "count", actor: 1, movements: [] }),
    });
    return { status: response.status, body: await response.json() };
  });

  expect(result.status).toBe(403);
  // Named, not merely refused: an unauthenticated request is a 403 too, so a
  // bare status assertion would pass with CSRF enforcement removed entirely.
  expect(result.body.detail).toMatch(/CSRF/i);
});
