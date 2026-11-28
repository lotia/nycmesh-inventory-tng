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

/** How far, at least, the point is kept from the edges of the target and
 * the control, so that a pixel's rounding cannot put it outside either. */
const INSIDE_PX = 4;

/** A point on `target`, scrolled onto the screen if it is not, that one of
 * the elements matching `controls` will be under once `gone` and its
 * space have gone and what follows it has moved up in its place: where a
 * double tap's second tap lands on them if nothing stops it. Only those
 * after `gone` in the page's flow count, as only they move up. Found from
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
    (it, { box, controls, inside }) => {
      const next = it.nextElementSibling;
      if (next === null) {
        return "nothing after";
      }
      // How far what follows moves up: its distance from the top of `it`.
      const rise =
        next.getBoundingClientRect().top - it.getBoundingClientRect().top;
      // The middle of the largest overlap, if it is wide and tall enough
      // to keep that middle off the edges.
      let best: { x: number; y: number; area: number } | null = null;
      for (const each of document.querySelectorAll(controls)) {
        const follows =
          it.compareDocumentPosition(each) & Node.DOCUMENT_POSITION_FOLLOWING;
        const position = getComputedStyle(each).position;
        if (
          !follows ||
          it.contains(each) ||
          position === "fixed" ||
          position === "sticky"
        ) {
          continue;
        }
        const moved = each.getBoundingClientRect();
        const top = Math.max(box.y, moved.top - rise);
        const bottom = Math.min(box.y + box.height, moved.bottom - rise);
        const left = Math.max(box.x, moved.left);
        const right = Math.min(box.x + box.width, moved.right);
        const area = (bottom - top) * (right - left);
        if (
          bottom - top >= 2 * inside &&
          right - left >= 2 * inside &&
          area > (best?.area ?? 0)
        ) {
          best = { x: (left + right) / 2, y: (top + bottom) / 2, area };
        }
      }
      return best && { x: best.x, y: best.y };
    },
    { box, controls, inside: INSIDE_PX },
  );
  if (over === "nothing after") {
    throw new Error(`nothing follows ${gone} to move up`);
  }
  if (over === null) {
    throw new Error(`nothing matching ${controls} moves up under ${target}`);
  }
  return over;
}

/** Counts, from now on, the clicks that reach an element matching
 * `controls` inside `within`: a tap that should have been ignored, or
 * kept off them, and got through, though it changed no value or focus. */
export async function clicksReaching(
  within: Locator,
  controls: string,
): Promise<() => Promise<number>> {
  await within.evaluate((it, controls) => {
    it.addEventListener("click", (event) => {
      if ((event.target as Element).closest(controls) !== null) {
        it.dataset.clicksReaching = String(
          Number(it.dataset.clicksReaching ?? 0) + 1,
        );
      }
    });
  }, controls);
  return () => within.evaluate((it) => Number(it.dataset.clicksReaching ?? 0));
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
