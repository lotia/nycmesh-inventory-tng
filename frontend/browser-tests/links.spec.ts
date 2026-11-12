/**
 * Resetting another administrator's sign-in: asked for the password first,
 * then a page showing the link, copied in one click, or scanned from a QR
 * code shown only when asked for (inventory-tng-8i4.50.12).
 */
import { expect } from "@playwright/test";
import { test } from "./own";
import { prepared } from "./prepared";

test("resetting another administrator asks for the password, then shows the link to copy or scan", async ({
  ownAdmin: { page, password },
  browserName,
}) => {
  const { other } = prepared();
  await page.goto("/admin/admins");
  await page.getByRole("link", { name: other }).click();
  // Asked first, on a page of its own. The first change to another
  // administrator in a session asks for a code: without TOTP, the password
  // (docs/decisions/0012), though they typed it to sign in.
  await page.getByRole("button", { name: "Reset their sign-in" }).click();
  const reset = page.locator('form[method="post"][action$="/reset"]');
  const asked = reset.getByLabel("Your password");
  await expect(asked).toBeVisible();
  // Refused without it: the server asks, not only the page.
  await asked.fill(`not ${password}`);
  await reset.getByRole("button", { name: "Reset their sign-in" }).click();
  await expect(page.getByText("That isn't your password.")).toBeVisible();
  await page.getByLabel("Your password").fill(password);
  await page.getByRole("button", { name: "Confirm" }).click();
  const link = (await page.locator(".key").textContent()) ?? "";
  expect(link).toContain("/admin/reset?token=");
  const qr = page.locator(".qr");
  await expect(qr).toBeHidden();
  await page.getByText("Show a QR code for their phone").click();
  await expect(qr).toBeVisible();
  // WebKit's clipboard cannot be read back in Playwright.
  if (browserName === "webkit") {
    return;
  }
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "Copy the link" }).click();
  await expect(page.locator("#copied")).toHaveText("Copied.");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(link);
});
