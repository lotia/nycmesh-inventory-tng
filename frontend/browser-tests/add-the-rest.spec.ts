/**
 * A save the inventory refuses for one of its lines: Add the rest to cart
 * puts its other lines back in the cart, saying the line taken off and
 * why, and Record records them (Ali, 2026-11-20, inventory-tng-clvh).
 * The cables are the test's own (own-rows.ts).
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";
import { itemIdOf, ownCable, record } from "./own-rows";
import { fillMove, newestCart, pick } from "./scan-page";

const BOX = "Box of 305 m";
const ADD_THE_REST = "Add the rest to cart";
const NONE_LEFT =
  "This package is retired, and the inventory has none left sealed: take this line off, record the rest again, and ask an administrator to count it";

test("a save refused for a line goes back without it, saying why, and the rest is recorded", async ({
  page,
}) => {
  const boxed = await ownCable(page, "Boxed cable");
  const boxedPage = page.url();
  const loose = await ownCable(page, "Loose cable");
  const looseId = itemIdOf(page);
  await page.goto("/scan");
  await fillMove(page, boxed);
  await page.getByLabel(/^Box of 305\sm/).fill("1");
  await page.getByLabel(/^Loose/).fill("");
  await pick(page, loose);
  // Its box retired in another tab, after this page loaded, with none
  // sealed anywhere: the inventory refuses the save for that line.
  const admin = await page.context().newPage();
  await admin.goto(boxedPage);
  await admin.getByRole("button", { name: `Retire ${BOX}` }).click();
  await expect(
    admin.getByRole("button", { name: `Bring it back ${BOX}` }),
  ).toBeVisible();
  await admin.close();
  await page.getByRole("button", { name: "Record" }).click();
  const entry = newestCart(page);
  await expect(entry.getByText(`Line 1: ${NONE_LEFT}`)).toBeVisible();

  await entry.getByRole("button", { name: ADD_THE_REST }).click();
  await expect(
    page.getByRole("status").getByText(`${boxed}: taken off. ${NONE_LEFT}`),
  ).toBeVisible();
  await expect(
    entry.getByText(
      "The other lines are in your cart, waiting to be recorded.",
    ),
  ).toBeVisible();
  await expect(entry.getByRole("button", { name: ADD_THE_REST })).toHaveCount(
    0,
  );
  const recorded = await record(page);
  expect(recorded.request().postDataJSON().lines).toEqual([
    expect.objectContaining({ item_id: looseId, quantity: "1" }),
  ]);
});
