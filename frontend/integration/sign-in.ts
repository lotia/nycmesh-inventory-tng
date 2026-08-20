import { expect, type Page } from "@playwright/test";
import { uv } from "./django";

/**
 * Signing in, for the tests that need somebody signed in.
 *
 * Through the local password path of
 * docs/decisions/0013-administrator-sign-in.md, because it is the only one a
 * test can complete: an OAuth round trip to Google or Slack from CI would be
 * testing their availability. The provider paths are covered in the backend
 * suite instead, where a callback can be completed without dialling anybody.
 *
 * The second factor is not stepped around. The seed publishes the TOTP secret
 * along with the password, and the code below is computed from it the way an
 * authenticator app would, so what this exercises is the real requirement
 * rather than a test account exempted from it.
 */

/**
 * How long signing in may take, worst case.
 *
 * The server accepts each code once for the thirty seconds it is valid, so a
 * test that signs in straight after another one waits for the next code -- see
 * `enterCode` below. Playwright's 30s default would report that wait as a
 * timeout naming the wrong thing.
 *
 * Both the suite's timeout and any test that waits for something after signing
 * in take theirs from here.
 */
export const SIGN_IN_MAY_WAIT_FOR_A_FRESH_CODE = 90_000;

/** One value global setup published. Unset means it did not run. */
export function seeded(name: string): string {
  const value = process.env[`SEEDED_${name.toUpperCase()}`];
  if (!value) {
    throw new Error(`SEEDED_${name.toUpperCase()} is unset: global setup did not run.`);
  }
  return value;
}

/**
 * The code an authenticator app would be showing right now.
 *
 * Computed by pyotp in the backend's environment rather than reimplemented
 * here: an independent implementation of RFC 6238 is what makes this a test
 * of the server's TOTP rather than of one shared piece of arithmetic. It is
 * a subprocess per sign-in, which is cheap next to starting a browser.
 */
export function authenticatorCode(secret: string): string {
  return uv("-c", "import sys, pyotp; print(pyotp.TOTP(sys.argv[1]).now())", secret).trim();
}

/** The form fields, which allauth renders unlabelled, so by name. */
export const USERNAME_FIELD = 'input[name="login"]';
export const PASSWORD_FIELD = 'input[name="password"]';
export const CODE_FIELD = 'input[name="code"]';

/** Exact, because the second-factor page offers a security key as well. */
export function submit(page: Page) {
  return page.getByRole("button", { name: "Sign In", exact: true });
}

/**
 * Sign in as the seeded administrator: password, then the code.
 *
 * Through this server's own origin, not the backend's port, because that is
 * the path a deployment takes and the only one that exercises the proxy.
 *
 * Waits for the session to actually exist. Without that, a test asserting a
 * refusal cannot tell "rejected for a missing CSRF token" from "rejected for
 * not being signed in yet".
 */
export async function signIn(page: Page) {
  await page.goto("/accounts/login/");
  await page.locator(USERNAME_FIELD).fill(seeded("username"));
  await page.locator(PASSWORD_FIELD).fill(seeded("password"));
  await submit(page).click();

  await enterCode(page, seeded("totp_secret"));

  await expect(page).toHaveURL("/");
}

/**
 * Enter the current code, and wait for the next one if it has been spent.
 *
 * The server accepts each code once, for the thirty seconds it is valid --
 * which is the point of a one-time password and is not something to weaken so
 * that a suite runs faster. What it costs is this: a browser suite signs in
 * several times a minute where a person signs in once a day, so the second
 * sign-in inside one period is handed a code the first already used. Waiting
 * for the next one is the honest way through, and it is why the per-test
 * timeout in playwright.config.ts is what it is.
 */
async function enterCode(page: Page, secret: string) {
  const spent = authenticatorCode(secret);
  await page.locator(CODE_FIELD).fill(spent);
  // Waited for, not sampled: `page.url()` immediately after a click races the
  // navigation the click started, and reading it too early would send an
  // accepted code down the retry path -- which then times out looking for a
  // field the next page does not have, blaming the wrong thing.
  await Promise.all([page.waitForLoadState("networkidle"), submit(page).click()]);
  if (!page.url().includes("/accounts/")) {
    return;
  }

  await expect
    .poll(() => authenticatorCode(secret), { timeout: 40_000, intervals: [2_000] })
    .not.toBe(spent);
  await page.locator(CODE_FIELD).fill(authenticatorCode(secret));
  await submit(page).click();
}
