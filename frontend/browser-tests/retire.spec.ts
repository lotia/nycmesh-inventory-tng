/**
 * Retiring is one tap, undone from its notice; with changes not yet saved,
 * the page asks first (docs/decisions/0019). Each test retires a volunteer
 * of its own (own-rows.ts), and brings them back.
 */
import { expect } from "@playwright/test";
import { SETTLE_MS } from "../src/shared/settling";
import { test } from "./csp-violations";
import { ownVolunteer } from "./own-rows";

test("a page with unsaved changes asks before retiring", async ({ page }) => {
  const was = await ownVolunteer(page);
  const name = page.getByLabel(/^Name/);
  await name.fill(`${was} (typed)`);
  const retire = page.getByRole("button", { name: "Retire", exact: true });
  await retire.click();
  const asked = page.getByRole("dialog");
  await expect(asked).toContainText("changes you haven't saved");
  // Taps wait a moment as the question comes, and as it goes
  // (docs/decisions/0019, Double taps).
  await page.waitForTimeout(SETTLE_MS);
  await asked.getByRole("button", { name: "Go back" }).click();
  await expect(asked).toHaveCount(0);
  await expect(name).toHaveValue(`${was} (typed)`);

  await page.waitForTimeout(SETTLE_MS);
  await retire.click();
  await page.waitForTimeout(SETTLE_MS);
  await page.getByRole("button", { name: "Retire anyway" }).click();
  await expect(page.getByRole("status")).toContainText("Retired.");
  // The notice's taps wait a moment as it comes.
  await page.waitForTimeout(SETTLE_MS);
  await page.getByRole("button", { name: "Bring them back" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(was);
});

test("a double tap on Go back closes the question and does nothing more (inventory-tng-r3jx)", async ({
  page,
}) => {
  const was = await ownVolunteer(page);
  const name = page.getByLabel(/^Name/);
  await name.fill(`${was} (typed)`);
  const retire = page.getByRole("button", { name: "Retire", exact: true });
  await retire.click();
  await page.waitForTimeout(SETTLE_MS);
  const back = page
    .getByRole("dialog")
    .getByRole("button", { name: "Go back" });
  const box = await back.boundingBox();
  if (box === null) {
    throw new Error("Go back is not shown");
  }
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  const url = page.url();
  await page.touchscreen.tap(x, y);
  await page.waitForTimeout(150);
  await page.touchscreen.tap(x, y);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(retire).toBeFocused();
  // Nothing under Go back was tapped: no form sent, the change still there.
  expect(page.url()).toBe(url);
  await expect(name).toHaveValue(`${was} (typed)`);
});

test("a double tap on Retire brings up the question, and its second tap, on Retire anyway, is ignored (inventory-tng-0c2z)", async ({
  page,
}) => {
  const was = await ownVolunteer(page);
  const name = page.getByLabel(/^Name/);
  await name.fill(`${was} (typed)`);
  const retire = page.getByRole("button", { name: "Retire", exact: true });
  // Where the question puts Retire anyway: Retire is moved there, as a
  // Retire near the middle of the screen would be.
  await retire.click();
  const asked = page.getByRole("dialog");
  const anyway = await asked
    .getByRole("button", { name: "Retire anyway" })
    .boundingBox();
  if (anyway === null) {
    throw new Error("Retire anyway is not shown");
  }
  const x = anyway.x + anyway.width / 2;
  const y = anyway.y + anyway.height / 2;
  await page.waitForTimeout(SETTLE_MS);
  await asked.getByRole("button", { name: "Go back" }).click();
  await page.waitForTimeout(SETTLE_MS);
  await retire.evaluate(
    (button, { x, y }) => {
      const { width, height } = button.getBoundingClientRect();
      Object.assign(button.style, {
        position: "fixed",
        left: `${x - width / 2}px`,
        top: `${y - height / 2}px`,
      });
    },
    { x, y },
  );
  const url = page.url();
  await page.touchscreen.tap(x, y);
  await page.waitForTimeout(150);
  await page.touchscreen.tap(x, y);
  // Still asked, the safe choice focused, nothing sent.
  await expect(asked).toBeVisible();
  await expect(asked.getByRole("button", { name: "Go back" })).toBeFocused();
  expect(page.url()).toBe(url);
  await expect(name).toHaveValue(`${was} (typed)`);
});

test("a double tap on Retire anyway leaves the retire, its second tap on the notice's Bring them back ignored (inventory-tng-0c2z)", async ({
  page,
}) => {
  const was = await ownVolunteer(page);
  await page.getByLabel(/^Name/).fill(`${was} (typed)`);
  await page.getByRole("button", { name: "Retire", exact: true }).click();
  await page.waitForTimeout(SETTLE_MS);
  await page.getByRole("button", { name: "Retire anyway" }).click();
  const notice = page.getByRole("status");
  await expect(notice).toContainText("Retired.");
  // The second tap, landing as the next page comes, on Bring them back.
  const back = page.getByRole("button", { name: "Bring them back" });
  const box = await back.boundingBox();
  if (box === null) {
    throw new Error("Bring them back is not shown");
  }
  await page.touchscreen.tap(box.x + box.width / 2, box.y + box.height / 2);
  await expect(notice.getByText("Retired.")).toBeFocused();
  await expect(back).toBeVisible();
  await page.waitForTimeout(SETTLE_MS);
  await back.click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(was);
});

test("with nothing unsaved, retiring asks nothing", async ({ page }) => {
  const name = await ownVolunteer(page);
  await page.getByRole("button", { name: "Retire", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Retired.");
  // The notice's taps wait a moment as it comes.
  await page.waitForTimeout(SETTLE_MS);
  await page.getByRole("button", { name: "Bring them back" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(name);
});
