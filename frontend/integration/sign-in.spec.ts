import { expect, type Page, test } from "@playwright/test";
import { CODE_FIELD, PASSWORD_FIELD, seeded, signIn, submit, USERNAME_FIELD } from "./sign-in";

/**
 * The way in, through a real browser.
 *
 * docs/decisions/0013-administrator-sign-in.md is the argument. What only this
 * suite can see is that the sign-in pages are reachable on the same origin as
 * the app -- they are Django's, so a proxy that does not forward them leaves a
 * deployment with no way to sign in at all, and nothing in either unit suite
 * would notice.
 */

/** What the server says the caller is. */
async function whoami(page: Page) {
  return page.evaluate(async () => (await fetch("/api/me")).json());
}

test("the app's own control reaches the sign-in form and brings you back", async ({ page }) => {
  // WHAT ONLY THIS SUITE CAN SEE. The unit tests assert the href this control
  // carries; they cannot follow it. Between the two ends sit Django's own
  // pages, `LOGIN_REDIRECT_URL`, the `next` this app composes, and a proxy
  // that has to forward `/accounts/` at all -- and a break in any of them
  // leaves an administrator on a Django page with no way back to the app,
  // which is the interruption decision 0014 point 5 is trying to keep small.
  await page.goto("/");
  await page.getByRole("link", { name: /^sign in$/i }).click();

  await expect(page.locator(USERNAME_FIELD)).toBeVisible();

  await signIn(page, { alreadyThere: true });

  // Back on the app, and the control now says who you are rather than
  // offering the way in again.
  await expect(page.getByRole("heading", { name: /nyc mesh inventory/i })).toBeVisible();
  // `exact`, because the seeded volunteer is called "Integration Tester" and
  // the account is called "integration": a substring match finds both and
  // Playwright refuses the ambiguity rather than picking one.
  await expect(page.getByText(seeded("username"), { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /^sign in$/i })).toHaveCount(0);
});

test("the local password path signs an administrator in", async ({ page }) => {
  await signIn(page);

  const me = await whoami(page);
  expect(me.authenticated).toBe(true);
  expect(me.username).toBe(seeded("username"));
  expect(me.administrator).toBe(true);
});

test("the password alone does not sign anybody in", async ({ page }) => {
  // Decision 0013 point 3: the local path is not a way round the second
  // factor. The password is accepted and the session is still nobody's.
  await page.goto("/accounts/login/");
  await page.locator(USERNAME_FIELD).fill(seeded("username"));
  await page.locator(PASSWORD_FIELD).fill(seeded("password"));
  await submit(page).click();

  await expect(page.locator(CODE_FIELD)).toBeVisible();
  expect((await whoami(page)).authenticated).toBe(false);
});

test("a wrong code does not get past the second factor", async ({ page }) => {
  await page.goto("/accounts/login/");
  await page.locator(USERNAME_FIELD).fill(seeded("username"));
  await page.locator(PASSWORD_FIELD).fill(seeded("password"));
  await submit(page).click();

  await page.locator(CODE_FIELD).fill("000000");
  await submit(page).click();

  expect((await whoami(page)).authenticated).toBe(false);
});

test("the admin's own login form is the same door", async ({ page }) => {
  // Two surfaces exist and must agree; the backend suite's
  // `test_the_admin_sends_people_to_the_same_door` says why the admin's
  // cannot be the one that answers. Held here too: only a browser follows
  // the redirect chain the way a person meets it.
  await page.goto("/admin/login/?next=/admin/");

  await expect(page).toHaveURL(/\/accounts\/login\/\?next=/);
});
