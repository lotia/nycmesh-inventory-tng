/**
 * The scan page's taps that take away what is above other controls, each
 * tapped twice by a finger, 150 ms apart, as a phone's double tap lands:
 * the second tap does nothing, and focus is where the first put it (Ali,
 * 2026-11-27, inventory-tng-r3jx; docs/decisions/0019, Double taps). How
 * each behaves otherwise is in src/scan/ScanPage.double-taps.test.tsx.
 * The cables are the test's own (own-rows.ts).
 */
import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { ownCable, unique } from "./own-rows";
import { prepared } from "./prepared";
import { fillMove, pick, settled } from "./scan-page";

/** Two taps of a finger on the middle of `target`, 150 ms apart. */
async function doubleTap(page: Page, target: Locator) {
  // On the screen, where a finger can reach it.
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  if (box === null) {
    throw new Error("not shown");
  }
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.touchscreen.tap(x, y);
  await page.waitForTimeout(150);
  await page.touchscreen.tap(x, y);
}

/** The scan page's cart lines. */
const linesOf = (page: Page) => page.locator("li", { hasText: "in all" });

/** A move of two of the test's own cables, the second picked first. */
async function twoLines(page: Page) {
  const first = await ownCable(page);
  const second = await ownCable(page);
  await page.goto("/scan");
  await fillMove(page, first);
  await pick(page, second);
  await expect(linesOf(page)).toHaveCount(2);
}

test("a double tap on Remove removes one line, and Undo has focus", async ({
  page,
}) => {
  await twoLines(page);
  await doubleTap(
    page,
    linesOf(page).first().getByRole("button", { name: "Remove" }),
  );
  await expect(linesOf(page)).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Undo" })).toBeFocused();
});

test("a double tap on Undo puts the line back, and nothing else", async ({
  page,
}) => {
  await twoLines(page);
  await linesOf(page).first().getByRole("button", { name: "Remove" }).click();
  await settled(page);
  await doubleTap(page, page.getByRole("button", { name: "Undo" }));
  await expect(linesOf(page)).toHaveCount(2);
  await expect(
    linesOf(page).first().getByRole("button", { name: "Remove" }),
  ).toBeFocused();
});

test("a double tap on an item in the search adds it once, and taps nothing that moved up", async ({
  page,
}) => {
  // Three matches, so the cart's first line moves up under the last one.
  const three = unique("Cable");
  const first = await ownCable(page, `${three} a`);
  await ownCable(page, `${three} b`);
  await ownCable(page, `${three} c`);
  await page.goto("/scan");
  await fillMove(page, first);
  await page.getByLabel(/Find an item/).fill(three);
  const matches = page.getByRole("listitem").getByRole("button", {
    name: three,
  });
  await expect(matches).toHaveCount(3);
  await doubleTap(page, matches.last());
  await expect(linesOf(page)).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "Cart" })).toBeFocused();
});

test("a double tap on Discard, confirming Discard cart, empties the cart and does nothing more", async ({
  page,
}) => {
  await twoLines(page);
  await page.getByRole("button", { name: "Discard cart" }).click();
  await doubleTap(
    page,
    page.getByRole("button", { name: "Discard", exact: true }),
  );
  await expect(page.getByText("Nothing in the cart yet.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Cart" })).toBeFocused();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("a double tap on the camera's Done closes it, and Scan with the camera has focus", async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== "chromium", "only Chromium has a fake camera");
  await page.goto("/scan");
  await page.getByRole("button", { name: "Scan with the camera" }).click();
  await expect(
    page.getByText(`Added one: ${prepared().labelled}.`, { exact: true }),
  ).toBeVisible({ timeout: 15_000 });
  await settled(page);
  const lines = await linesOf(page).count();
  await doubleTap(page, page.getByRole("button", { name: "Done" }));
  const scan = page.getByRole("button", { name: "Scan with the camera" });
  await expect(scan).toBeFocused();
  await expect(linesOf(page)).toHaveCount(lines);
  await expect(page.getByRole("region", { name: "Camera" })).toHaveCount(0);
});
