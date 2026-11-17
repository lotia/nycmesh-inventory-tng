/**
 * Without scripts, the buttons that need them stay hidden, as the page's
 * script shows them (frontend/src/buttons.ts), and what they act on is
 * still shown: a code to print by hand, a link to copy by hand
 * (inventory-tng-vxxc, in place of the manual test inventory-tng-f8kw).
 */
import { expect } from "@playwright/test";
import { makeCode } from "./codes";
import { invite } from "./own";
import { test } from "./policy";

test.use({ javaScriptEnabled: false });

test("a code's page offers no Print, and shows the code", async ({ page }) => {
  // Made without scripts too: a code is shown only once.
  await makeCode(page);
  const print = page.getByRole("button", {
    name: "Print",
    exact: true,
    includeHidden: true,
  });
  await expect(print).toBeAttached();
  await expect(print).toBeHidden();
  // The first of each is the screen's; the print sheet has its own.
  await expect(page.locator(".qr svg").first()).toBeVisible();
  const phrase = page.locator(".key").first();
  await expect(phrase).toBeVisible();
  await expect(phrase).not.toBeEmpty();
  await expect(page.getByLabel("US Letter")).toBeVisible();
});

test("a made link's page offers no Copy the link, and shows the link", async ({
  page,
}) => {
  const { link } = await invite(page);
  const copy = page.getByRole("button", {
    name: "Copy the link",
    includeHidden: true,
  });
  await expect(copy).toBeAttached();
  await expect(copy).toBeHidden();
  const shown = page.locator("#link");
  await expect(shown).toBeVisible();
  await expect(shown).toHaveText(link);
});
