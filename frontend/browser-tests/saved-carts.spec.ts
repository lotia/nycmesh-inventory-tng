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
import { fillMove, newestCart } from "./scan-page";
import { doubleTap, middleOf, settled } from "./taps";

test("a save's entry keeps its height, Dismiss's room kept unseen, as it is sent and recorded", async ({
  page,
}) => {
  const cable = await ownCable(page);
  await page.goto("/scan");
  await fillMove(page, cable);
  // Its save held until its entry is measured as being sent.
  const release = await holdSaves(page);
  await page.getByRole("button", { name: "Record" }).click();
  const entry = newestCart(page);
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

test("a double tap on a refused save's Dismiss dismisses that one only, and focuses the entry that moved up (inventory-tng-34ns)", async ({
  page,
}) => {
  const cable = await ownCable(page);
  // Each save refused for its key, so all stay listed alike, to dismiss;
  // three, so the page is long enough not to scroll back as one goes.
  await page.route("**/api/transactions", (route) =>
    route.fulfill({ status: 409, json: { reason: "key_reused" } }),
  );
  await page.goto("/scan");
  const list = page.locator("section", {
    has: page.getByRole("heading", { name: "Carts from this device" }),
  });
  for (const count of [1, 2, 3]) {
    await fillMove(page, cable);
    await page.getByRole("button", { name: "Record" }).click();
    await expect(list.getByRole("button", { name: "Dismiss" })).toHaveCount(
      count,
    );
    // A moment later, as the page ignores taps once one moved what was
    // below (docs/decisions/0019, Double taps).
    await settled(page);
  }
  const dismiss = newestCart(page).getByRole("button", { name: "Dismiss" });
  await dismiss.scrollIntoViewIfNeeded();
  const at = await middleOf(dismiss);
  await doubleTap(page, at);
  // The one that moved up under the finger is still listed, focused.
  await expect(list.getByRole("button", { name: "Dismiss" })).toHaveCount(2);
  await expect(list.locator("#outbox-0")).toBeFocused();
});
