import type { Locator, Page } from "@playwright/test";

/**
 * Driving the app the way both runs over the guides drive it.
 *
 * Two files walk the same scene for different reasons -- one photographs it,
 * the other asks whether the controls the guides name are still on it -- and
 * the steps that get them there are the same steps. They live here so that a
 * gesture the app changes is re-learnt once.
 *
 * Only what both need: how long the walk takes, the two window sizes, the way
 * a code arrives, and the one button whose name depends on what happened last.
 * Everything about what to do with the screen once it is there stays with
 * whichever run wants it.
 */

/**
 * How long one walk of this scene may take.
 *
 * Far longer than anything else in `integration/`, and it has to be: this is
 * the whole of the volunteer's app and then five pages of Django's admin, with
 * a sign-in, two saves and a refusal on the way. The suite's own per-test
 * budget is written for a spec that signs in and asserts one thing, and a walk
 * held to that reports a timeout naming whichever step happened to be running
 * when the clock ran out -- which is never the step at fault.
 *
 * One number for both runs. They walk the same screens in the same order, so a
 * walk that has grown slower has grown slower for each of them, and two
 * numbers would mean finding out twice.
 */
export const WALKING_THE_SCENE_MAY_TAKE = 300_000;

/** A phone at a shelf, which is what the volunteer's half is drawn for. */
export const PHONE = { width: 420, height: 900 };

/** The admin is a desktop page and wraps unreadably below this. */
export const DESK = { width: 1280, height: 900 };

/**
 * What the app calls the things both runs reach for.
 *
 * The walks stay written twice, because the states they harvest at destroy one
 * another and lifting the shared prologue out would leave a walk taking a
 * callback per step -- a general mechanism for two callers. The *names* are a
 * different matter: they are the app's, not either run's, and a button renamed
 * with them written out twice is a rename that fixes one run and rots the
 * other. Here it is one edit.
 *
 * Only the names both runs use. One that only a screenshot or only the check
 * needs stays with the step that wants it, where a reader of that step sees
 * what it is looking at.
 */
export const WHO_ARE_YOU = "Who are you?";
export const VOLUNTEERS = "Volunteers";
export const WORKING_AS = "Working as";
export const LOCATION_SET = "Location set";
export const THIS_BATCH = "This batch";
export const WHAT_IS_HAPPENING = "What is happening";
export const OUTBOX = "Batches not yet sent";
export const SEND_NOW = "Send now";
export const WAITING_TO_SEND = "waiting to send";

/** What a quantity too long for the ledger's column is refused with. */
export const TOO_MANY_DIGITS = "digits";

/** The box holding one line's amount, which the app names after its item. */
export function quantityOf(item: string): string {
  return `Quantity of ${item} in the batch`;
}

/** Where a batch is posted, and so what either run intercepts to hold one up. */
export const BATCH_ENDPOINT = "**/api/stock/transactions";

/** Hand one code to the app the way a scanner gun does. */
export async function scan(page: Page, code: string): Promise<void> {
  await page.getByLabel("Scan or type a code").fill(code);
  await page.getByLabel("Scan or type a code").press("Enter");
}

/** The Save button, whatever the last attempt left it saying. */
export function saveButton(page: Page): Locator {
  return page.getByRole("button", { name: /^(Save|Try again)$/ });
}
