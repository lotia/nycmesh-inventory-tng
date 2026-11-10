/**
 * The page showing a link made for another administrator: copied in one
 * click, or scanned from a QR code shown only when asked for
 * (inventory-tng-8i4.50.12).
 */
import { expect, test } from "@playwright/test";
import { prepared } from "./prepared";

test("a reset link is copied in one click, its QR code shown when asked", async ({
  page,
  context,
  browserName,
}) => {
  // WebKit's clipboard cannot be read back in Playwright.
  test.skip(
    browserName === "webkit",
    "WebKit's clipboard is not readable here",
  );
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  const { other, password } = prepared();
  await page.goto("/admin/admins");
  await page.getByRole("link", { name: other }).click();
  // The first change to another administrator in a session asks for a
  // code: without TOTP, the password.
  const reset = page.locator('form[action$="/reset"]');
  await reset.getByLabel("Your password").fill(password);
  await reset.getByRole("button", { name: "Reset their sign-in" }).click();
  const link = (await page.locator(".key").textContent()) ?? "";
  expect(link).toContain("/admin/reset?token=");
  const qr = page.locator(".qr");
  await expect(qr).toBeHidden();
  await page.getByText("Show a QR code for their phone").click();
  await expect(qr).toBeVisible();
  await page.getByRole("button", { name: "Copy the link" }).click();
  await expect(page.locator("#copied")).toHaveText("Copied.");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(link);
});
