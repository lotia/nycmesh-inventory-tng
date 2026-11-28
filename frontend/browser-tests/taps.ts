/**
 * A finger's taps, as a phone's double tap lands, and the wait while a
 * page ignores taps after one that changed what was under the finger
 * (docs/decisions/0019, Double taps).
 */
import type { Locator, Page } from "@playwright/test";
import { SETTLE_MS } from "../src/shared/settling";

/** A point on the page, as a finger touches it. */
export type Point = { x: number; y: number };

/** Waits till the page takes taps again. */
export async function settled(page: Page): Promise<void> {
  await page.waitForTimeout(SETTLE_MS);
}

/** The middle of `target`, scrolled onto the screen if it is not, where
 * a finger can reach it. */
export async function middleOf(target: Locator): Promise<Point> {
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  if (box === null) {
    throw new Error(`${target} is not shown`);
  }
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** Two taps of a finger at `at`, 150 ms apart, as a double tap lands: the
 * page redrawn, or the next one come, after the first. */
export async function doubleTap(page: Page, at: Point): Promise<void> {
  await page.touchscreen.tap(at.x, at.y);
  await page.waitForTimeout(150);
  await page.touchscreen.tap(at.x, at.y);
}

/** Two taps of a finger at `at`, the second as soon as the page the first
 * sent for has come, its address matching `arrived`: a double tap whose
 * second tap lands on the next page. */
export async function doubleTapOnto(
  page: Page,
  at: Point,
  arrived: RegExp,
): Promise<void> {
  await page.touchscreen.tap(at.x, at.y);
  await page.waitForURL(arrived);
  await page.touchscreen.tap(at.x, at.y);
}
