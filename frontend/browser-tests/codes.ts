/** Registration codes, made from the devices page as an administrator. */
import { expect, type Locator, type Page } from "@playwright/test";
import { prepared } from "./prepared";

/**
 * Makes a code, confirming with the password if asked, each button pressed
 * by `press`. Whether it asked.
 */
export async function makeCode(
  page: Page,
  label = "Hub open day",
  press: (button: Locator) => Promise<void> = (button) => button.click(),
): Promise<boolean> {
  await page.goto("/admin/devices");
  await page.getByLabel("What it's for").fill(label);
  await press(page.getByRole("button", { name: "Make a code" }));
  const made = page.getByRole("heading", { name: "A new registration code" });
  const password = page.getByLabel("Your password");
  await expect(made.or(password)).toBeVisible();
  const asked = await password.isVisible();
  if (asked) {
    await password.fill(prepared().password);
    await press(page.getByRole("button", { name: "Confirm" }));
  }
  await expect(made).toBeVisible();
  return asked;
}
