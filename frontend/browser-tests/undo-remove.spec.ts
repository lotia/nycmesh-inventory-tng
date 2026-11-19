/**
 * Undo after Remove on the scan page's cart (Ali, inventory-tng-5uy,
 * 2026-11-13), by keyboard: the line goes back where it was, as it was,
 * and is recorded so. The cables are the test's own (own-rows.ts).
 */
import { expect, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { ownCable, record } from "./own-rows";

async function pick(page: Page, cable: string): Promise<void> {
  await page.getByLabel(/Find an item/).fill(cable);
  await page.getByRole("button", { name: cable, exact: true }).click();
}

test("a line removed is put back where it was by Undo, from the keyboard", async ({
  page,
}) => {
  const first = await ownCable(page);
  const second = await ownCable(page);
  await page.goto("/scan");
  await page.getByLabel("Your name").selectOption({ label: "Sam Example" });
  await page.getByLabel("Moving stock").check();
  await page
    .getByLabel("From", { exact: true })
    .selectOption({ label: "Supernode 1 › Shelf A" });
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Van" });
  await pick(page, first);
  await pick(page, second);
  const lines = page.locator("li", { hasText: "in all" });
  await lines
    .first()
    .getByLabel(/^Loose/)
    .fill("7");

  await lines.first().getByRole("button", { name: "Remove" }).click();
  const notice = page.getByRole("status");
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
