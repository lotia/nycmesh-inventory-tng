/**
 * An amount's box says what it is counted in, right after it and to a
 * screen reader (inventory-tng-mdvz): on the adjustment form, the unit of
 * the item picked, which follows the pick (admin/hints.ts, followUnits);
 * on the count form, the item's unit. What each box says for each unit,
 * and for sealed packages, the backend tests check
 * (test_count_routes_box_units.py).
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";
import { ownCable, ownPlace } from "./own-rows";

test("the adjustment and count forms say the unit beside the box", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const store = await ownPlace(page, "Store", "Store");
  const storeId = new URL(page.url()).pathname.split("/").at(-1);
  await page.goto("/admin/counts");
  const adjust = page.locator("#adjust");
  const howMuch = adjust.getByRole("textbox", { name: /^How much/ });
  await expect(howMuch).toHaveAccessibleName("How much");
  await adjust.getByLabel(/^Place/).selectOption({ label: store });
  await adjust.getByLabel(/^Item/).selectOption({ label: `${cable} (m)` });
  await expect(howMuch).toHaveAccessibleName("How much m");
  await howMuch.fill("20");
  await adjust.getByLabel("found there").check();
  await adjust.getByLabel(/^Why/).selectOption({ index: 1 });
  await adjust.getByRole("button", { name: "Record it" }).click();
  // Saved before leaving: going to another page first cancels the save if
  // it is still being sent.
  await expect(adjust.getByRole("status")).toHaveText("Recorded.");

  await page.goto(`/admin/counts/${storeId}`);
  const counted = page.getByRole("textbox", { name: `${cable} counted` });
  await expect(counted).toHaveValue("20");
  await expect(counted).toHaveAccessibleDescription("m");
  await expect(page.locator(".with-unit", { has: counted })).toHaveText("m");
});
