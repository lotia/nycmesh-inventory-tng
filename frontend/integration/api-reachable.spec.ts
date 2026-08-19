import { type BrowserContext, expect, type Page, test } from "@playwright/test";

/**
 * Can a browser actually use this API?
 *
 * Nothing else in the project can answer that; the reasoning, and the setup
 * these tests need, are in DEVELOPERS.md "Integration tests".
 */

/** One value global setup published. Unset means it did not run. */
function seeded(name: string): string {
  const value = process.env[`SEEDED_${name.toUpperCase()}`];
  if (!value) {
    throw new Error(`SEEDED_${name.toUpperCase()} is unset: global setup did not run.`);
  }
  return value;
}

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
 * Through this server's own origin, not the backend's port, because that is
 * the path a deployment takes and the only one that exercises the proxy.
 *
 * Waits for the session to actually exist. Without that, a test asserting a
 * refusal cannot tell "rejected for a missing CSRF token" from "rejected for
 * not being logged in yet".
 */
async function login(page: Page) {
  await page.goto("/admin/login/");
  await page.getByLabel(/username/i).fill(seeded("username"));
  await page.getByLabel(/password/i).fill(seeded("password"));
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

  const scene = {
    volunteer: Number(seeded("volunteer")),
    item: Number(seeded("item")),
    location: Number(seeded("location")),
  };
  const result = await page.evaluate(
    async ({ csrf, volunteer, item, location }) => {
      const volunteers = await (await fetch("/api/volunteers")).json();
      const response = await fetch("/api/stock/transactions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf as string },
        body: JSON.stringify({
          kind: "count",
          actor: volunteer,
          movements: [{ item, quantity: "1", to_location: location }],
        }),
      });
      return {
        offered: volunteers.results.some((v: { id: number }) => v.id === volunteer),
        status: response.status,
        body: await response.json(),
      };
    },
    { csrf: token, ...scene },
  );

  // The pick-list and the batch endpoint must agree about who may be recorded
  // against; that they share one queryset is the point of Volunteer.selectable.
  expect(result.offered).toBe(true);
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
