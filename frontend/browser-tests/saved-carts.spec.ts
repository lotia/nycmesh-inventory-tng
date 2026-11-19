/**
 * The scan page's list of carts saved from this device: an entry keeps
 * its height as its save finishes, so a tap meant for Dismiss on another
 * does not miss (Ali, 2026-11-19, inventory-tng-2lad). The cable is the
 * test's own (own-rows.ts).
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";
import { holdSaves } from "./held-saves";
import { ownCable } from "./own-rows";

test("a save's entry keeps its height, Dismiss's room kept unseen, as it is sent and recorded", async ({
  page,
}) => {
  const cable = await ownCable(page);
  await page.goto("/scan");
  await page.getByLabel("Your name").selectOption({ label: "Sam Example" });
  await page.getByLabel("Moving stock").check();
  await page
    .getByLabel("From", { exact: true })
    .selectOption({ label: "Supernode 1 › Shelf A" });
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Van" });
  await page.getByLabel(/Find an item/).fill(cable);
  await page.getByRole("button", { name: cable, exact: true }).click();
  // Its save held until its entry is measured as being sent.
  const release = await holdSaves(page);
  await page.getByRole("button", { name: "Record" }).click();
  const entry = page
    .locator("section", {
      has: page.getByRole("heading", { name: "Carts from this device" }),
    })
    .getByRole("listitem")
    .first();
  await expect(entry.getByText("Sending…")).toBeVisible();
  await expect(entry.getByRole("button", { name: "Dismiss" })).toHaveCount(0);
  // There, keeping its room, but unseen (visibility: hidden).
  await expect(entry.getByText("Dismiss")).toHaveCount(1);
  await expect(entry.getByText("Dismiss")).toBeHidden();
  const sending = await entry.boundingBox();
  release();
  await expect(
    entry.getByText(/^Recorded \(transaction \d+\)\.$/),
  ).toBeVisible();
  await expect(entry.getByRole("button", { name: "Dismiss" })).toBeVisible();
  expect((await entry.boundingBox())?.height).toBe(sending?.height);
});
