/**
 * A finger's taps, as a phone's double tap lands, and the wait while a
 * page ignores taps after one that changed what was under the finger
 * (docs/decisions/0019, Double taps).
 */
import type { Locator, Page } from "@playwright/test";
import { SETTLE_MS } from "../src/shared/settling";

/** A point on the page, as a finger touches it. */
export type Point = { x: number; y: number };

/** Waits till the page takes taps again. A page's wait as it loads starts
 * when its script runs, which is before DOMContentLoaded but may be after
 * its words show (src/admin/settle-on-load.ts). */
export async function settled(page: Page): Promise<void> {
  await page.waitForLoadState("domcontentloaded");
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

/** A point on `target`, scrolled onto the screen if it is not, that one of
 * the elements matching `controls` will be under once `gone` and its
 * space have gone and what follows it has moved up in its place: where a
 * double tap's second tap lands on them if nothing stops it. Found from
 * the page as drawn, so it holds whatever the fonts make its heights. */
export async function middleOverWhatMovesUp(
  target: Locator,
  gone: Locator,
  controls: string,
): Promise<Point> {
  await target.scrollIntoViewIfNeeded();
  const box = await target.boundingBox();
  if (box === null) {
    throw new Error(`${target} is not shown`);
  }
  const over = await gone.evaluate(
    (it, { box, controls }) => {
      const next = it.nextElementSibling;
      if (next === null) {
        return null;
      }
      // How far what follows moves up: its distance from the top of `it`.
      const rise =
        next.getBoundingClientRect().top - it.getBoundingClientRect().top;
      // The middle of the largest overlap, away from any edge.
      let best: { x: number; y: number; area: number } | null = null;
      for (const each of document.querySelectorAll(controls)) {
        if (it.contains(each)) {
          continue;
        }
        const moved = each.getBoundingClientRect();
        const top = Math.max(box.y, moved.top - rise);
        const bottom = Math.min(box.y + box.height, moved.bottom - rise);
        const left = Math.max(box.x, moved.left);
        const right = Math.min(box.x + box.width, moved.right);
        const area = Math.max(0, bottom - top) * Math.max(0, right - left);
        if (area > (best?.area ?? 0)) {
          best = { x: (left + right) / 2, y: (top + bottom) / 2, area };
        }
      }
      return best && { x: best.x, y: best.y };
    },
    { box, controls },
  );
  if (over === null) {
    throw new Error(`nothing matching ${controls} moves up under ${target}`);
  }
  return over;
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
