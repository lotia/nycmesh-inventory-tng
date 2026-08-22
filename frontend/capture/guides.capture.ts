import { mkdirSync } from "node:fs";
import { expect, type Locator, type Page, test } from "@playwright/test";
import { seeded, signIn } from "../integration/sign-in";
import { DESK, PHONE, saveButton, scan, WALKING_THE_SCENE_MAY_TAKE } from "./drive";
import { type Box, endingBefore, union } from "./frame";
import {
  dressTheScene,
  ITEM_CODE,
  ITEM_LABEL_QUANTITY,
  MEASURED_CODE,
  MEASURED_ITEM,
  MEASURED_TAKEN,
  MEASURED_UNIT,
  MORE_THAN_IS_THERE,
  NEAR_MISS,
  ON_HAND,
  WALL_CODE,
} from "./scene";
import { imagePath, REPO_ROOT, SHOTS } from "./shots";

/**
 * Every picture in guides/, taken from the running app.
 *
 * Not a test, and deliberately not in the directory the test command reads: a
 * run of this writes a folder of PNGs into the repository, which is not
 * something a suite may do. It has its own config and its own npm script, listed in
 * DEVELOPERS.md.
 *
 * It asserts as it goes all the same. A screenshot of the wrong screen is
 * worse than no screenshot, because nothing about it looks wrong until a
 * volunteer at a shelf follows it -- so every step waits for the thing it is
 * about to photograph and fails if it is not there.
 *
 * Re-runnable, and it has to be: the images are regenerated rather than
 * retaken. Every code, quantity and name the pictures show is pinned by
 * scene.ts before anything is photographed, so a run that changes nothing
 * about the app comes back with almost every picture identical.
 *
 * Almost. One of them cannot be: a run posts to the ledger and the ledger is
 * append-only, so the picture of it grows by a pair of rows each time. That is
 * a picture of something that genuinely moves.
 */

/** Room around a cropped control, so it is not cut flush against its border. */
const MARGIN = 8;

/** How much of the ledger the picture of it shows. See the shot itself. */
const LEDGER_ROWS = 6;

test.describe.configure({ timeout: WALKING_THE_SCENE_MAY_TAKE });

/** Every shot the driver takes, so the run can say it took all of them. */
const taken: string[] = [];

async function shoot(page: Page, name: string, clip?: Box): Promise<void> {
  await page.screenshot({
    path: `${REPO_ROOT}${imagePath(name)}`,
    clip,
    // Whole page, then cut down to the clip. Without it a rectangle reaching
    // past the fold comes back cropped at the bottom of the window, and a
    // refusal is exactly what pushes the Save button past it.
    fullPage: true,
    animations: "disabled",
  });
  taken.push(name);
}

/** One element's own rectangle, once it is on screen. */
async function boxOf(locator: Locator): Promise<Box> {
  await expect(locator).toBeVisible();
  const box = await locator.boundingBox();
  if (box === null) {
    throw new Error("An element that is visible has a bounding box; this one had none.");
  }
  return box;
}

/**
 * The rectangle holding all of these, with room around it.
 *
 * Measured from the top of the document, and put there first. `fullPage`
 * clips in the document's coordinates while `boundingBox` answers in the
 * viewport's, so the two agree only while nothing has scrolled -- and clicking
 * Save scrolls it into view. Left alone, every rectangle taken after the first
 * refusal is shifted up the page by the scroll offset, which is a picture of
 * the wrong part of the screen and looks like a picture of the right one.
 */
async function around(...locators: Locator[]): Promise<Box> {
  const first = locators[0];
  if (first === undefined) {
    throw new Error("A shot needs at least one element to be drawn around.");
  }
  await first.page().evaluate(() => window.scrollTo(0, 0));
  const boxes = [];
  for (const locator of locators) {
    boxes.push(await boxOf(locator));
  }
  return union(boxes, MARGIN);
}

test("every picture in the guides", async ({ page }) => {
  const item = Number(seeded("item"));
  const itemName = seeded("item_name");
  dressTheScene(item, Number(seeded("location")), Number(seeded("volunteer")));
  mkdirSync(`${REPO_ROOT}guides/images`, { recursive: true });

  await page.setViewportSize(PHONE);
  await signIn(page);

  // ---- The volunteer's guide -------------------------------------------

  const whoAreYou = page.getByLabel("Who are you?");
  await whoAreYou.fill(seeded("volunteer_name"));
  const volunteers = page.getByRole("list", { name: "Volunteers" });
  await expect(volunteers.getByText(seeded("volunteer_name"))).toBeVisible();
  await shoot(page, "volunteer-who-you-are", await around(whoAreYou, volunteers));

  await volunteers.getByText(seeded("volunteer_name")).click();
  await expect(page.getByText("Working as")).toBeVisible();

  await shoot(
    page,
    "volunteer-scan-box",
    await around(page.locator('form[aria-label="Scan a code"]')),
  );

  // The wall sticker first, because that is the gesture the guide describes:
  // say where you are, then scan what you are holding.
  await scan(page, WALL_CODE);
  await expect(page.getByRole("alert").filter({ hasText: "Location set" })).toBeVisible();

  await scan(page, ITEM_CODE);
  const added = page.getByRole("alert").filter({ hasText: "Added" });
  await expect(added).toContainText(`${ITEM_LABEL_QUANTITY} × ${itemName}`);
  await shoot(page, "volunteer-added", await around(added));

  // The one modal the volunteer's app has, and the only scan that does not
  // put anything in the batch by itself. Its answer is also the second line of
  // the batch below, which is what makes "the rest untouched" visible in the
  // picture of a refusal.
  await scan(page, MEASURED_CODE);
  const howMuch = page.getByRole("dialog");
  await expect(howMuch).toContainText(MEASURED_ITEM);
  await shoot(page, "volunteer-measured-amount", await around(howMuch));
  await howMuch.getByLabel(`Amount in ${MEASURED_UNIT}`).fill(String(MEASURED_TAKEN));
  await howMuch.getByRole("button", { name: "Add" }).click();
  await expect(page.getByRole("alert").filter({ hasText: MEASURED_ITEM })).toBeVisible();

  // By the heading rather than by any text in the row: `seed_demo_data` puts
  // "LiteBeam AC Gen2" in the same catalogue, and the new setup path runs it
  // against the same database, so a substring match finds two rows and
  // Playwright refuses to measure either.
  const row = page
    .getByRole("list", { name: "Items" })
    .getByRole("listitem")
    .filter({ has: page.getByRole("heading", { name: itemName, exact: true }) });
  await expect(row).toContainText(`${ON_HAND} on hand`);
  // Cropped before the Edit button, which is an administrator's and is drawn
  // here only because the capture run is signed in as one. See frame.ts.
  const edit = row.getByRole("button", { name: `Edit ${itemName}` });
  await shoot(
    page,
    "volunteer-catalogue-row",
    endingBefore(await around(row), (await boxOf(edit)).x),
  );

  const kind = page.getByLabel("What is happening");
  const batch = page.getByRole("list", { name: "This batch" });
  await expect(page.getByLabel("Where the stock is")).toContainText("131 Broome");
  await shoot(page, "volunteer-batch", await around(kind, batch, saveButton(page)));

  // A quantity with more digits than the ledger's column takes: one bad line
  // in a batch of two that is otherwise fine, which is what the guide is
  // about. Two lines and not one, so that the untouched half of the promise --
  // "fix that line and leave the rest alone" -- is in the picture.
  const quantity = page.getByLabel(`Quantity of ${itemName} in the batch`);
  await quantity.fill("1234567890");
  await saveButton(page).click();
  await expect(batch).toContainText("digits");
  await shoot(page, "volunteer-line-refused", await around(kind, batch, saveButton(page)));

  // The same batch, that one line put right, taking more than the shelf holds.
  await quantity.fill(String(MORE_THAN_IS_THERE));
  await saveButton(page).click();
  const saved = page.getByRole("alert").filter({ hasText: "Worth a stock count" });
  await expect(saved).toContainText(itemName);
  await shoot(page, "volunteer-worth-a-count", await around(saved));

  // Nothing at the other end. Aborted rather than answered and thrown away,
  // so the ledger never sees this batch and the queue is the only copy.
  await page.route("**/api/stock/transactions", (route) => route.abort());
  await scan(page, WALL_CODE);
  await scan(page, ITEM_CODE);
  await saveButton(page).click();
  // Two shots and not one. The notice is at the bottom of the screen where
  // Save was pressed and the queue is at the top, and a rectangle holding
  // both is a picture of the whole app rather than of either.
  //
  // The notice by its first words rather than by its heading: the queued row
  // above says "waiting to send" too.
  await shoot(
    page,
    "volunteer-held",
    await around(page.getByRole("alert").filter({ hasText: "Nothing answered" })),
  );
  const outbox = page.getByRole("region", { name: "Batches not yet sent" });
  await expect(outbox).toContainText("waiting to send");
  await shoot(
    page,
    "volunteer-outbox",
    await around(outbox, page.getByRole("button", { name: "Send now" })),
  );
  await page.unroute("**/api/stock/transactions");

  // ---- The administrator's guide ---------------------------------------

  await page.setViewportSize(DESK);

  await page.goto(`/admin/inventory/item/${item}/change/`);
  await shoot(page, "administrator-identifiers", await around(page.locator("#identifiers-group")));
  await shoot(page, "administrator-item-flag", await around(page.locator(".field-sheet_flag")));

  await page.goto("/admin/inventory/volunteer/?sheet_flag__isempty=0");
  await expect(page.locator("#result_list")).toContainText("Possibly the same person");
  await shoot(page, "administrator-volunteers-flagged", await around(page.locator("#changelist")));

  await page.getByRole("link", { name: NEAR_MISS[0], exact: true }).click();
  await shoot(
    page,
    "administrator-merge",
    await around(page.locator(".field-active"), page.locator(".field-merged_into")),
  );

  await page.goto("/admin/inventory/location/");
  await shoot(page, "administrator-locations", await around(page.locator("#changelist")));

  await page.goto(`/admin/inventory/label/?q=${ITEM_CODE}`);
  await page.getByRole("link", { name: ITEM_CODE, exact: true }).click();
  await expect(page.locator(".field-code")).toContainText(ITEM_CODE);
  await shoot(page, "administrator-label", await around(page.locator("fieldset").first()));

  await page.goto(`/api/labels/sheet?code=${ITEM_CODE},${WALL_CODE}`);
  await expect(page.getByText(ITEM_CODE)).toBeVisible();
  // The stickers, not the page they are laid out on: `.sheet` is as wide as
  // the paper, and the line above it that says how many there are and when
  // they were generated is not printed at all.
  const stickers = page.locator(".sheet .label");
  await shoot(page, "administrator-label-sheet", await around(stickers.first(), stickers.last()));

  await page.goto("/admin/inventory/stockmovement/");
  await expect(page.locator("#result_list")).toContainText(itemName);
  // Bounded rather than the whole list. Every run of this appends to the
  // ledger and nothing may ever take a row out again, so a picture of all of
  // it would grow by two rows and a few kilobytes every time it is taken.
  //
  // Wide enough to take the filter sidebar in with it. The guide sends a
  // reader here to answer "why does this shelf disagree", and the only control
  // that makes a ledger of thousands answerable is the one this picture used
  // to crop off.
  const rows = page.locator("#result_list tbody tr");
  const shown = Math.min(await rows.count(), LEDGER_ROWS);
  await shoot(
    page,
    "administrator-ledger",
    await around(
      page.locator("#result_list thead"),
      rows.nth(shown - 1),
      page.locator("#changelist-filter"),
    ),
  );

  // The list in shots.ts is what the guides and their test are written
  // against, so a run that quietly took all but one of them is a failure here
  // rather than a broken image somebody meets later.
  expect(taken).toEqual(SHOTS.map((shot) => shot.name));
});
