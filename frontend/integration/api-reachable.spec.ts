import { type BrowserContext, expect, test } from "@playwright/test";
import { seeded, signIn } from "./sign-in";

/**
 * Can a browser actually use this API?
 *
 * Nothing else in the project can answer that; the reasoning, and the setup
 * these tests need, are in DEVELOPERS.md "Integration tests".
 *
 * The session these tests need comes from the application's own sign-in now
 * that it has one -- see sign-in.ts and
 * docs/decisions/0013-administrator-sign-in.md. It used to come from the
 * Django admin's login form, which was a stand-in for a door the app did not
 * yet have; that form no longer signs anybody in.
 *
 * What is being proved here is still the write path rather than the way in.
 * These two tests sign in because the batch endpoint asks for a session
 * today; under decision 0012 point 3 a volunteer appends without one, and
 * when that lands this file loses the sign-in rather than changing it.
 */

/** Read a cookie the browser is holding for this origin. */
async function cookie(context: BrowserContext, name: string) {
  const cookies = await context.cookies();
  return cookies.find((c) => c.name === name)?.value;
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
  await signIn(page);

  await page.goto("/");
  await page.evaluate(() => fetch("/api"));
  const token = await cookie(context, "csrftoken");
  expect(token).toBeTruthy();

  const scene = {
    volunteer: Number(seeded("volunteer")),
    volunteerName: seeded("volunteer_name"),
    item: Number(seeded("item")),
    location: Number(seeded("location")),
  };
  const result = await page.evaluate(
    async ({ csrf, volunteer, volunteerName, item, location }) => {
      // Searched, not listed: the pick-list is paginated at 50, and this suite
      // runs against a development database that already holds other people.
      // An unfiltered first page would drop the seeded volunteer off the end
      // and the assertion below would fail for a reason that is not a bug.
      const volunteers = await (
        await fetch(`/api/volunteers?search=${encodeURIComponent(volunteerName)}`)
      ).json();
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
  await signIn(page);

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
