/**
 * Once for every test: signs in as the administrator global-setup.ts
 * seeded, and registers the browser as a device, then keeps both cookies
 * (playwright.config.ts, ADMIN_STATE).
 */
import { expect, test } from "@playwright/test";

test("sign in and register", async ({ page }) => {
  await page.goto("/admin/sign-in");
  await page.getByLabel("Username").fill(process.env.ADMIN_USERNAME ?? "");
  await page.getByLabel("Password").fill(process.env.ADMIN_PASSWORD ?? "");
  await page.getByRole("button", { name: "Sign in" }).click();
  // Signed in, with nothing more to do first: the home page.
  await expect(page).toHaveURL(/\/admin$/);
  await page.goto(`/register?code=${process.env.REGISTRATION_PHRASE ?? ""}`);
  await page.getByRole("button", { name: "Register" }).click();
  await expect(page.getByText("This browser is registered.")).toBeVisible();
  await page.context().storageState({ path: process.env.ADMIN_STATE ?? "" });
});
