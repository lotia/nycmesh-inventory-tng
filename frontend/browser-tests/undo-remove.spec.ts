/**
 * Undo after Remove on the scan page's cart (Ali, inventory-tng-5uy,
 * 2026-11-13), by keyboard: the line goes back where it was, as it was,
 * and is recorded so. The cables are the test's own (own-rows.ts).
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";
import { ownCable, record } from "./own-rows";
import { fillMove, pick } from "./scan-page";

test("a line removed is put back where it was by Undo, from the keyboard", async ({
  page,
}) => {
  const first = await ownCable(page);
  const second = await ownCable(page);
  await page.goto("/scan");
  await fillMove(page, first);
  await pick(page, second);
  const lines = page.locator("li", { hasText: "in all" });
  await lines
    .first()
    .getByLabel(/^Loose/)
    .fill("7");

  await lines.first().getByRole("button", { name: "Remove" }).click();
  // The page's other status lines stay on it, empty (inventory-tng-2l6n).
  const notice = page.getByRole("status").filter({ hasText: "Removed" });
  await expect(notice).toHaveText(`Removed ${first}. Undo`);
  await expect(lines).toHaveCount(1);
  const undo = notice.getByRole("button", { name: "Undo" });
  await expect(undo).toBeFocused();
  await page.keyboard.press("Enter");

  await expect(notice).toHaveCount(0);
  await expect(lines).toHaveCount(2);
  await expect(lines.first()).toContainText(first);
  await expect(lines.first().getByLabel(/^Loose/)).toHaveValue("7");
  await expect(
    lines.first().getByRole("button", { name: "Remove" }),
  ).toBeFocused();
  const response = await record(page);
  expect(
    response
      .request()
      .postDataJSON()
      .lines.map((line: { quantity: string }) => line.quantity),
  ).toEqual(["7", "1"]);
});
