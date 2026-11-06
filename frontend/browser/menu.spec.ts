/**
 * On a phone, the sections fold into a menu (inventory-tng-8i4.60.2). The
 * page holds each link twice, for the menu and for a wide screen's bar:
 * only one shows.
 */
import { expect, type Page, test } from "@playwright/test";

/** In the band at the top of the page, or the menu it opens, as shown now. */
function shown(page: Page, role: "link" | "button", name: string) {
  return page
    .getByRole("banner")
    .getByRole(role, { name, exact: true })
    .filter({ visible: true });
}

test("the menu opens on a phone and leads to each section", async ({
  page,
}) => {
  await page.goto("/admin");
  await expect(shown(page, "link", "Settings")).toHaveCount(0);
  await shown(page, "button", "Menu").click();
  await expect(shown(page, "button", "Sign out")).toHaveCount(1);
  await shown(page, "link", "Settings").click();
  await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
  await expect(shown(page, "button", "Sign out")).toHaveCount(0);
});

test("the open menu fits a phone, however long a name", async ({ page }) => {
  await page.goto("/admin");
  // As long a username as an administrator may have, with nowhere to break.
  await page.locator("#menu strong").evaluate((name) => {
    name.textContent = "a".repeat(64);
  });
  await shown(page, "button", "Menu").click();
  // The menu floats above the page, so it is measured, not the page.
  const overflow = await page
    .locator("#menu")
    .evaluate((menu) => menu.scrollWidth - menu.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.keyboard.press("Escape");
  await expect(shown(page, "button", "Sign out")).toHaveCount(0);
});

test.describe("on a wide screen", () => {
  test.use({ viewport: { width: 1024, height: 768 } });

  test("the sections show, with no menu", async ({ page }) => {
    await page.goto("/admin");
    await expect(shown(page, "button", "Menu")).toHaveCount(0);
    await expect(shown(page, "link", "Settings")).toHaveCount(1);
    await expect(shown(page, "button", "Sign out")).toHaveCount(1);
  });
});
