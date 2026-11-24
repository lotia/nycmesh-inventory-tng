/**
 * The scan page's status lines, under the camera and under the item
 * search, at a phone's width: each keeps room for two lines of words, so
 * a refusal appearing or clearing once its line is fixed moves nothing
 * below it; empty, it shows no bar (Ali, 2026-11-24, inventory-tng-2l6n).
 */
import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { prepared } from "./prepared";
import { looseBox, pick } from "./scan-page";

const NOT_ADDED = "Not added. Fix the incorrect amount below, then scan again.";

/** Each of `below`'s box, measured from the top of `line`. */
async function placed(line: Locator, below: Locator[]) {
  const top = (await line.boundingBox())?.y ?? Number.NaN;
  return Promise.all(
    below.map(async (each) => {
      const box = await each.boundingBox();
      return box === null ? null : { ...box, y: box.y - top };
    }),
  );
}

/** The item search's status line, just after its field. */
function searchLine(page: Page): Locator {
  return page
    .getByLabel(/Find an item/)
    .locator("xpath=../following-sibling::p[@role='status']");
}

test("the camera's status line keeps its room as a refusal appears and clears", async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== "chromium", "only Chromium has a fake camera");
  await page.goto("/scan");
  const scan = page.getByRole("button", { name: "Scan with the camera" });
  const camera = page.getByRole("region", { name: "Camera" });
  const line = camera.getByRole("status");
  const below = [
    camera.getByRole("button", { name: "Done" }),
    page.getByLabel(/Find an item/),
    page.getByRole("heading", { name: "Cart" }),
  ];
  await scan.click();
  await expect(line).toHaveText(`Added one: ${prepared().labelled}.`, {
    timeout: 15_000,
  });
  const added = await placed(line, below);
  // Read again by a camera opened again, it is refused.
  await camera.getByRole("button", { name: "Done" }).click();
  await looseBox(page, prepared().labelled).fill("abc");
  await scan.click();
  await expect(line).toHaveText(NOT_ADDED, { timeout: 15_000 });
  expect(await placed(line, below)).toEqual(added);
  // Fixed, it clears, and shows no bar.
  await looseBox(page, prepared().labelled).fill("1");
  await expect(line).toHaveText("");
  expect(await placed(line, below)).toEqual(added);
  await expect(line).toHaveCSS("border-left-color", "rgba(0, 0, 0, 0)");
});

test("the item search's status line keeps its room as a refusal appears and clears", async ({
  page,
}) => {
  await page.goto("/scan");
  const line = searchLine(page);
  const below = [page.getByRole("heading", { name: "Cart" })];
  await pick(page, prepared().labelled);
  await looseBox(page, prepared().labelled).fill("abc");
  await expect(line).toHaveText("");
  await expect(line).toHaveCSS("border-left-color", "rgba(0, 0, 0, 0)");
  const empty = await placed(line, below);
  await pick(page, prepared().labelled);
  await expect(line).toHaveText(NOT_ADDED);
  expect(await placed(line, below)).toEqual(empty);
  await looseBox(page, prepared().labelled).fill("1");
  await expect(line).toHaveText("");
  expect(await placed(line, below)).toEqual(empty);
});
