import { expect, type Page } from "@playwright/test";

/** Signs in as the administrator global-setup.ts seeded. */
export async function signIn(page: Page): Promise<void> {
  await page.goto("/admin/sign-in");
  await page.getByLabel("Username").fill(process.env.ADMIN_USERNAME ?? "");
  await page.getByLabel("Password").fill(process.env.ADMIN_PASSWORD ?? "");
  await page.getByRole("button", { name: "Sign in" }).click();
  // Signed in, with nothing more to do first: the home page.
  await expect(page).toHaveURL(/\/admin$/);
}
