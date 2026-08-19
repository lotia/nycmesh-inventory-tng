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
  // Two sign-in surfaces exist and must agree. The admin ships a password
  // form that knows nothing about providers or second factors, so what
  // answers to its URL is the one that does.
  await page.goto("/admin/login/?next=/admin/");

  await expect(page).toHaveURL(/\/accounts\/login\/\?next=/);
});
