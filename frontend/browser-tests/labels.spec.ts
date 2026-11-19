/**
 * Labels print twelve to a page, cut apart or on sticker paper
 * (inventory-tng-8i4.16), on US Letter or A4.
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";
import { prepared } from "./prepared";
import { printLabels } from "./print-labels";

/** How many pages a PDF has. */
function pageCount(pdf: Buffer): number {
  return [...pdf.toString("latin1").matchAll(/\/Type\s*\/Page\b/g)].length;
}

test("printing shows only the labels, with their codes", async ({ page }) => {
  await printLabels(page, 3);
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("banner")).toBeHidden();
  const labels = page.locator(".print-label").filter({ visible: true });
  await expect(labels).toHaveCount(3);
  await expect(labels.first()).toContainText(prepared().labelled);
  // An inch and a half across: 96 pixels to the inch.
  expect((await labels.first().locator("svg").boundingBox())?.width).toBe(
    1.5 * 96,
  );
});

test("twelve fit a page of each paper", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "only Chromium prints to PDF");
  await printLabels(page, 13);
  for (const paper of ["US Letter", "A4"]) {
    await page.getByLabel(paper).check();
    const pdf = await page.pdf({ preferCSSPageSize: true });
    expect(pageCount(pdf), paper).toBe(2);
  }
});
