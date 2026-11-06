/**
 * Once for every test: signs in as the administrator the backend seeded,
 * and registers the browser as a device, then keeps both cookies
 * (playwright.config.ts, ADMIN_STATE).
 */
import { expect, test } from "@playwright/test";
import { adminState, prepared } from "./prepared";

test("sign in and register", async ({ page }) => {
  const { username, password, phrase } = prepared();
  await page.goto("/admin/sign-in");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Signed in, with nothing more to do first: the home page.
  await expect(page).toHaveURL(/\/admin$/);
  await page.goto(`/register?code=${phrase}`);
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page.getByText("This browser is registered.")).toBeVisible();
  await page.context().storageState({ path: adminState() });
});
