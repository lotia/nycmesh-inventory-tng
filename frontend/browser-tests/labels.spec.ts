/**
 * Labels print twelve to a page, cut apart or on sticker paper
 * (inventory-tng-8i4.16), on US Letter or A4.
 */
import { expect, type Locator } from "@playwright/test";
import { test } from "./csp-violations";
import { unique } from "./own-rows";
import { prepared } from "./prepared";
import { printFromHere, printLabels } from "./print-labels";

type Box = NonNullable<Awaited<ReturnType<Locator["boundingBox"]>>>;

/** Where it is laid out. */
async function boxOf(shown: Locator): Promise<Box> {
  const box = await shown.boundingBox();
  if (box === null) throw new Error("not laid out");
  return box;
}

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

test("a long name of the place it is part of never hides a place's own", async ({
  page,
}) => {
  const store = unique(
    "Store with a name long enough to fill a label ".repeat(3),
  );
  await page.goto("/admin/locations");
  await page.getByLabel(/^Name/).fill(store);
  await page.getByLabel(/^What it is/).selectOption({ label: "Store" });
  await page.getByRole("button", { name: "Add", exact: true }).click();
  // Its page, before leaving: going to another cancels the add if it is
  // still being sent, and then no place is part of it.
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(store);
  const area = unique("Shelf");
  await page.goto("/admin/locations");
  await page.getByLabel(/^Name/).fill(area);
  await page.getByLabel(/^What it is/).selectOption({ label: "Area" });
  await page.getByLabel(/^Part of/).selectOption({ label: store });
  await page.getByRole("button", { name: "Add", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(area);
  await printFromHere(page, 1);
  // As wide as US Letter prints, less its margins: at a phone's width a
  // label is a third as wide as on paper, too narrow for some unique names
  // of the place in one line, and then the other rightly has no line.
  await page.setViewportSize({ width: 7.5 * 96, height: 10 * 96 });
  await page.emulateMedia({ media: "print" });
  const label = page.locator(".print-label");
  const within = label.locator(".print-within");
  const own = label.locator(".print-words > :last-child");
  await expect(own).toHaveText(area);
  const [card, parent, name] = await Promise.all([
    boxOf(label),
    boxOf(within),
    boxOf(own),
  ]);
  // The place's own name whole, on the label; the other in one line.
  expect(name.y).toBeGreaterThanOrEqual(card.y);
  expect(name.y + name.height).toBeLessThanOrEqual(card.y + card.height);
  expect(parent.height).toBe(name.height);
  expect(await own.evaluate((p) => p.scrollHeight <= p.clientHeight)).toBe(
    true,
  );
});
