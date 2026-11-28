/**
 * An administrator page's answer to a form, double tapped: the second tap
 * lands as the answer comes, on whatever it put under the finger, and is
 * ignored, focus going to the page's notice or heading (Ali, 2026-11-27,
 * inventory-tng-0c2z; docs/decisions/0019, Double taps). How the page
 * tells it is in src/admin/settle-on-load.ts.
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";
import { unique } from "./own-rows";
import { prepared } from "./prepared";
import { doubleTapOnto, middleOf, settled } from "./taps";

test("a double tap on a reason's Retire retires it, its second tap, on the table the notice pushed down, ignored", async ({
  page,
}) => {
  const reason = unique("Found");
  await page.goto("/admin/settings");
  await page.getByLabel("New reason").fill(reason);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page).toHaveURL(/notice=added/);
  await settled(page);
  const retire = page.getByRole("button", { name: `Retire ${reason}` });
  await doubleTapOnto(page, await middleOf(retire), /notice=reason_retired/);
  const notice = page.getByRole("status");
  await expect(notice).toHaveText("Retired: it is no longer offered.");
  await expect(notice).toBeFocused();
  // Nothing under the finger was tapped: no other form sent.
  await settled(page);
  await expect(page).toHaveURL(/notice=reason_retired/);
});

test("a double tap on a refused Correct leaves it refused, its second tap, on Bring back that came in its place, ignored", async ({
  page,
}) => {
  const waiting = "/admin/stock/recorded-more";
  const row = () =>
    page.getByRole("row").filter({ hasText: prepared().refused });
  // Where the refusal puts Bring back: Correct is moved there, as a
  // Correct near the top of the list would be.
  await page.goto(waiting);
  await row()
    .getByRole("button", { name: /^Correct/ })
    .click();
  const bringBack = page.getByRole("button", { name: /^Bring back/ });
  const at = await middleOf(bringBack);
  await page.goto(waiting);
  await row()
    .getByRole("button", { name: /^Correct/ })
    .evaluate((button, { x, y }) => {
      const { width, height } = button.getBoundingClientRect();
      Object.assign(button.style, {
        position: "fixed",
        left: `${x - width / 2}px`,
        top: `${y - height / 2}px`,
      });
    }, at);
  await doubleTapOnto(page, at, /\/correct/);
  await expect(bringBack).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toBeFocused();
  await settled(page);
  await expect(page).toHaveURL(/\/correct/);
  await expect(bringBack).toBeVisible();
});
