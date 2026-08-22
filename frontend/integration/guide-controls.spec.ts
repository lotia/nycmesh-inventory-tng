import { expect, type Page, test } from "@playwright/test";
import { controlsIn } from "../capture/controls";
import { DESK, PHONE, saveButton, scan } from "../capture/drive";
import { dressTheScene, ITEM_CODE, WALL_CODE } from "../capture/scene";
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

const GUIDES: Guide[] = ["volunteer", "administrator"];

/**
 * Every name the page is currently offering: what is written on the things
 * that can be pressed, and what every field is labelled.
 *
 * Harvested rather than looked up one at a time. A resolver per control would
 * be a second locator layer to keep in step with the app, and the question
 * here is only whether a name is on the screen at all -- so the page is asked
 * what it offers and the guide's names are checked against that.
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
      'button, a, label, legend, th, [role="button"], .MuiChip-label',
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

  const offered = new Set<string>();
  const harvest = async (): Promise<void> => {
    for (const name of await namesOnScreen(page)) {
      offered.add(name);
    }
  };

  await page.setViewportSize(PHONE);
  await signIn(page);

  // ---- The volunteer's half, walked as the guide walks it ---------------

  await page.getByLabel("Who are you?").fill(seeded("volunteer_name"));
  const volunteers = page.getByRole("list", { name: "Volunteers" });
  await expect(volunteers.getByText(seeded("volunteer_name"))).toBeVisible();
  await volunteers.getByText(seeded("volunteer_name")).click();
  await expect(page.getByText("Working as")).toBeVisible();
  await harvest();

  // The wall sticker, then the box: several of the controls the guide names
  // are not drawn at all until there is something in the batch.
  await scan(page, WALL_CODE);
  await expect(page.getByRole("alert").filter({ hasText: "Location set" })).toBeVisible();
  await scan(page, ITEM_CODE);
  const batch = page.getByRole("list", { name: "This batch" });
  await expect(batch).toContainText(itemName);
  await harvest();

  // A refusal is the only thing that renames Save, so the guide's word for it
  // cannot be found without provoking one.
  const quantity = page.getByLabel(`Quantity of ${itemName} in the batch`);
  await quantity.fill("1234567890");
  await saveButton(page).click();
  await expect(batch).toContainText("digits");
  await harvest();

  // Nothing at the other end: the queue, and what it offers to do about it.
  await quantity.fill("1");
  await page.route("**/api/stock/transactions", (route) => route.abort());
  await saveButton(page).click();
  const outbox = page.getByRole("region", { name: "Batches not yet sent" });
  await expect(outbox).toContainText("waiting to send");
  await harvest();

  // And the same batch getting through, which is the only state that offers
  // the word for clearing it.
  await page.unroute("**/api/stock/transactions");
  await page.getByRole("button", { name: "Send now" }).click();
  await expect(outbox.getByRole("button", { name: "Dismiss" })).toBeVisible();
  await harvest();

  // ---- The administrator's half ----------------------------------------

  await page.setViewportSize(DESK);
  for (const path of [
    `/admin/inventory/item/${item}/change/`,
    `/admin/inventory/volunteer/${volunteer}/change/`,
    `/admin/inventory/location/${location}/change/`,
  ]) {
    await page.goto(path);
    await expect(page.locator("#content")).toBeVisible();
    await harvest();
  }

  await page.goto(`/admin/inventory/label/?q=${ITEM_CODE}`);
  await page.getByRole("link", { name: ITEM_CODE, exact: true }).click();
  await expect(page.locator(".field-code")).toContainText(ITEM_CODE);
  await harvest();

  // ---- What the guides say is there ------------------------------------

  const missing = GUIDES.flatMap((guide) =>
    controlsIn(guide)
      .filter((control) => !offered.has(control))
      .map((control) => `guides/${guide}.md names ${JSON.stringify(control)}`),
  );
  expect(missing, "a guide names a control this app no longer offers").toEqual([]);
});
