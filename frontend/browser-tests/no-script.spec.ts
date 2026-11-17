/**
 * Without scripts, the buttons that need them stay hidden, as the page's
 * script shows them (frontend/src/buttons.ts), and what they act on is
 * still shown: a code to print by hand, a link to copy by hand
 * (inventory-tng-vxxc). Hidden by the `hidden` attribute the script would
 * remove, not by a style. No script runs, so the policy watch (policy.ts)
 * sees nothing here.
 */
import { expect, type Page } from "@playwright/test";
import { makeCode } from "./codes";
import { invite } from "./own";
import { test } from "./policy";

test.use({ javaScriptEnabled: false });

/** Fails unless `page` has the button `name`, hidden by its attribute. */
async function expectHiddenButton(page: Page, name: string): Promise<void> {
  const button = page.getByRole("button", {
    name,
    exact: true,
    includeHidden: true,
  });
  await expect(button).toHaveAttribute("hidden");
  await expect(button).toBeHidden();
}

test("a code's page offers no Print, and shows the code", async ({ page }) => {
  // Made without scripts too: a code is shown only once.
  await makeCode(page);
  await expectHiddenButton(page, "Print");
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
  await expectHiddenButton(page, "Copy the link");
  const shown = page.locator("#link");
  await expect(shown).toBeVisible();
  await expect(shown).toHaveText(link);
});
