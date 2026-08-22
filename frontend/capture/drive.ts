import type { Locator, Page } from "@playwright/test";

/**
 * Driving the app the way both runs over the guides drive it.
 *
 * Two files walk the same scene for different reasons -- one photographs it,
 * the other asks whether the controls the guides name are still on it -- and
 * the steps that get them there are the same steps. They live here so that a
 * gesture the app changes is re-learnt once.
 *
 * Only what both need: the two window sizes, the way a code arrives, and the
 * one button whose name depends on what happened last. Everything about what
 * to do with the screen once it is there stays with whichever run wants it.
 */

/** A phone at a shelf, which is what the volunteer's half is drawn for. */
export const PHONE = { width: 420, height: 900 };

/** The admin is a desktop page and wraps unreadably below this. */
export const DESK = { width: 1280, height: 900 };

/** Hand one code to the app the way a scanner gun does. */
export async function scan(page: Page, code: string): Promise<void> {
  await page.getByLabel("Scan or type a code").fill(code);
  await page.getByLabel("Scan or type a code").press("Enter");
}

/** The Save button, whatever the last attempt left it saying. */
export function saveButton(page: Page): Locator {
  return page.getByRole("button", { name: /^(Save|Try again)$/ });
}
