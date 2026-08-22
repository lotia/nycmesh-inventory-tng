import { expect, type Page, test } from "@playwright/test";
import { controlsIn } from "../capture/controls";
import {
  BATCH_ENDPOINT,
  DESK,
  LOCATION_SET,
  OUTBOX,
  PHONE,
  quantityOf,
  SEND_NOW,
  saveButton,
  scan,
  THIS_BATCH,
  TOO_MANY_DIGITS,
  VOLUNTEERS,
  WAITING_TO_SEND,
  WALKING_THE_SCENE_MAY_TAKE,
  WHAT_IS_HAPPENING,
  WHO_ARE_YOU,
  WORKING_AS,
} from "../capture/drive";
import {
  dressTheScene,
  ITEM_CODE,
  MEASURED_CODE,
  MEASURED_ITEM,
  WALL_CODE,
} from "../capture/scene";
import type { Guide } from "../capture/shots";
import { seeded, signIn } from "./sign-in";

/**
 * Every control the guides name, found in the running app.
 *
 * The pictures in guides/ are regenerated, so a screen that changed shape is
 * seen the next time somebody runs the capture. A *name* is the half that
 * rots in silence: rename a button and the guide goes on telling a volunteer
 * in a basement to press something that is not there, with every other suite
 * green. Nothing but a browser can answer whether it is there.
 *
 * Deliberately not a picture comparison. Regenerating the PNGs and failing on
 * a difference catches more, but it fails on any pixel -- a font, a shadow, a
 * scrollbar -- and a check that cries wolf is turned off. This fails only on
 * the change that would mislead a reader.
 *
 * The scene is the capture run's, from `capture/scene.ts`, and so are the
 * names: `capture/controls.ts` reads them out of the guides. Nothing here
 * lists a control, because a list here is what would fall behind the guide.
 */

// The same walk as the capture run, and so the same budget. See drive.ts.
test.describe.configure({ timeout: WALKING_THE_SCENE_MAY_TAKE });

/**
 * The two halves of the system, kept apart.
 *
 * A single pile of every name both halves offer would let Django's admin
 * answer for the volunteer's app: `Quantity` is on a label's page as well as
 * in the batch, so a guide could name a control the reader's own screen has
 * not had for months and this would still pass.
 */
type Screen = "app" | "admin";

/**
 * Which screens each guide is answerable for.
 *
 * The volunteer's guide is about one place and says so. The administrator's
 * opens by naming two of them, the app and `/admin/`, and hands out work on
 * each -- so both are where its names are looked for, while the volunteer's
 * are held to the app alone.
 */
const SCREENS: Record<Guide, Screen[]> = {
  volunteer: ["app"],
  administrator: ["app", "admin"],
};

/** Both guides, from the mapping above, so neither can be forgotten here. */
const GUIDES = Object.keys(SCREENS) as Guide[];

/**
 * Every name the page is currently offering: what is written on the things
 * that can be pressed, and what every field is labelled.
 *
 * Harvested rather than looked up one at a time. A resolver per control would
 * be a second locator layer to keep in step with the app, and the question
 * here is only whether a name is on the screen at all -- so the page is asked
 * what it offers and the guide's names are checked against that.
 *
 * A menu's choices count as things that can be pressed, and MUI draws them as
 * options rather than as buttons -- and only for as long as the menu is open,
 * which is what the walk below has to arrange.
 *
 * Django's admin writes a colon after every field label and the guides do not,
 * so one is trimmed.
 */
async function namesOnScreen(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const names = new Set<string>();
    const keep = (text: string | null): void => {
      const name = (text ?? "").replace(/\s+/g, " ").trim().replace(/:$/, "");
      if (name !== "") {
        names.add(name);
      }
    };
    for (const element of document.querySelectorAll(
      'button, a, label, legend, th, [role="button"], [role="option"], .MuiChip-label',
    )) {
      keep(element.textContent);
    }
    for (const element of document.querySelectorAll("[aria-label]")) {
      keep(element.getAttribute("aria-label"));
    }
    return [...names];
  });
}

test("the app still offers every control the guides name", async ({ page }) => {
  const item = Number(seeded("item"));
  const itemName = seeded("item_name");
  const volunteer = Number(seeded("volunteer"));
  const location = Number(seeded("location"));
  dressTheScene(item, location, volunteer);

  const offered: Record<Screen, Set<string>> = { app: new Set(), admin: new Set() };
  const harvest = async (screen: Screen): Promise<void> => {
    for (const name of await namesOnScreen(page)) {
      offered[screen].add(name);
    }
  };

  await page.setViewportSize(PHONE);
  await signIn(page);

  // ---- The volunteer's half, walked as the guide walks it ---------------

  // Before anybody is picked, because picking somebody is what takes the
  // question away: the app asks it once and then remembers the answer, so
  // every later harvest is of a screen that no longer carries it. Waited for
  // by the list beneath it rather than by the question itself, which is one
  // of the names being checked.
  const volunteers = page.getByRole("list", { name: VOLUNTEERS });
  await expect(volunteers).toBeVisible();
  await harvest("app");

  await page.getByLabel(WHO_ARE_YOU).fill(seeded("volunteer_name"));
  await expect(volunteers.getByText(seeded("volunteer_name"))).toBeVisible();
  await volunteers.getByText(seeded("volunteer_name")).click();
  await expect(page.getByText(WORKING_AS)).toBeVisible();
  await harvest("app");

  // The wall sticker, then the box: several of the controls the guide names
  // are not drawn at all until there is something in the batch.
  await scan(page, WALL_CODE);
  await expect(page.getByRole("alert").filter({ hasText: LOCATION_SET })).toBeVisible();
  await scan(page, ITEM_CODE);
  const batch = page.getByRole("list", { name: THIS_BATCH });
  await expect(batch).toContainText(itemName);
  await harvest("app");

  // The one modal the volunteer's app has. Both of the buttons on it are on
  // no other screen, and it is opened by scanning something measured and by
  // nothing else -- so a walk that never scans one cannot see either.
  await scan(page, MEASURED_CODE);
  const howMuch = page.getByRole("dialog");
  await expect(howMuch).toContainText(MEASURED_ITEM);
  await harvest("app");
  // Dismissed rather than answered, so the batch below is the one line the
  // rest of this walk is written around.
  await page.keyboard.press("Escape");
  await expect(howMuch).toBeHidden();

  // The four things that can be done with a batch are a menu's choices, and a
  // closed menu has none of them in the page. Opened by the control the guide
  // names, and waited for by its role rather than by any choice's name --
  // naming one here is what this file exists not to do.
  await page.getByLabel(WHAT_IS_HAPPENING).click();
  const choices = page.getByRole("listbox");
  await expect(choices).toBeVisible();
  await harvest("app");
  await page.keyboard.press("Escape");
  await expect(choices).toBeHidden();

  // A refusal is the only thing that renames Save, so the guide's word for it
  // cannot be found without provoking one.
  const quantity = page.getByLabel(quantityOf(itemName));
  await quantity.fill("1234567890");
  await saveButton(page).click();
  await expect(batch).toContainText(TOO_MANY_DIGITS);
  await harvest("app");

  // Nothing at the other end: the queue, and what it offers to do about it.
  await quantity.fill("1");
  await page.route(BATCH_ENDPOINT, (route) => route.abort());
  await saveButton(page).click();
  const outbox = page.getByRole("region", { name: OUTBOX });
  await expect(outbox).toContainText(WAITING_TO_SEND);
  await harvest("app");

  // And the same batch getting through, which is the only state that offers
  // the word for clearing it.
  await page.unroute(BATCH_ENDPOINT);
  await page.getByRole("button", { name: SEND_NOW }).click();
  await expect(outbox.getByRole("button", { name: "Dismiss" })).toBeVisible();
  await harvest("app");

  // ---- The administrator's half ----------------------------------------

  await page.setViewportSize(DESK);
  for (const path of [
    `/admin/inventory/item/${item}/change/`,
    `/admin/inventory/volunteer/${volunteer}/change/`,
    `/admin/inventory/location/${location}/change/`,
  ]) {
    await page.goto(path);
    await expect(page.locator("#content")).toBeVisible();
    await harvest("admin");
  }

  await page.goto(`/admin/inventory/label/?q=${ITEM_CODE}`);
  await page.getByRole("link", { name: ITEM_CODE, exact: true }).click();
  await expect(page.locator(".field-code")).toContainText(ITEM_CODE);
  await harvest("admin");

  // ---- What the guides say is there ------------------------------------

  const missing = GUIDES.flatMap((guide) => {
    // A set, because both sides already are: what was harvested is a set per
    // screen, and flattening those into an array only to scan it for each of
    // a guide's names throws that away twice over.
    const reachable = new Set(SCREENS[guide].flatMap((screen) => [...offered[screen]]));
    return controlsIn(guide)
      .filter((control) => !reachable.has(control))
      .map((control) => `guides/${guide}.md names ${JSON.stringify(control)}`);
  });
  expect(missing, "a guide names a control this app no longer offers").toEqual([]);
});
