/**
 * A registration code prints cleanly (inventory-tng-8i4.61, 8i4.60.9): only
 * the code, one to a page, on US Letter or A4; up to four made together
 * share one page, two by two (8i4.73).
 */
import { expect, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { makeCode } from "./registration-codes";

const sheet = (page: Page) =>
  page.locator(".print-sheet").filter({ visible: true });

async function qrWidth(page: Page): Promise<number | undefined> {
  return (await sheet(page).locator("svg").boundingBox())?.width;
}

test("printing shows only the sheet: the code, large", async ({ page }) => {
  await makeCode(page);
  await expect(sheet(page)).toHaveCount(0);
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("banner")).toBeHidden();
  await expect(page.getByRole("button", { name: "Print" })).toBeHidden();
  await expect(sheet(page)).toHaveCount(1);
  await expect(sheet(page)).toContainText("Hub open day");
  // Five inches across: 96 pixels to the inch.
  expect(await qrWidth(page)).toBe(5 * 96);
});

/** The size of each page of a PDF, in points: one entry for each page. */
function pages(pdf: Buffer): string[] {
  return [...pdf.toString("latin1").matchAll(/\/MediaBox\s*\[([^\]]+)\]/g)].map(
    ([, box]) =>
      (box ?? "")
        .trim()
        .split(/\s+/)
        .slice(2)
        .map((n) => Math.round(Number(n)))
        .join("x"),
  );
}

test("the code fits one page of each paper", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "only Chromium prints to PDF");
  // As long a label as the server takes (admin.common.NAME_MAX).
  await makeCode(page, "A label ".repeat(25));
  const sizes = { "US Letter": "612x792", A4: "595x842" };
  for (const [paper, size] of Object.entries(sizes)) {
    await page.getByLabel(paper).check();
    const pdf = await page.pdf({ preferCSSPageSize: true });
    expect(pages(pdf), paper).toEqual([size]);
  }
});

test("four codes made together share a page, two by two", async ({ page }) => {
  await makeCode(page, "A label ".repeat(25), undefined, 4);
  await page.emulateMedia({ media: "print" });
  const cards = sheet(page).locator(".print-card");
  await expect(cards).toHaveCount(4);
  const lefts = await Promise.all(
    (await cards.all()).map(async (card) => (await card.boundingBox())?.x),
  );
  // Two columns: the first and third cards in one, the second and fourth
  // in the other.
  expect(lefts[0]).toBe(lefts[2]);
  expect(lefts[1]).toBe(lefts[3]);
  expect(lefts[0]).not.toBe(lefts[1]);
});

test("four codes fit one page of each paper", async ({ page, browserName }) => {
  test.skip(browserName !== "chromium", "only Chromium prints to PDF");
  await makeCode(page, "A label ".repeat(25), undefined, 4);
  const sizes = { "US Letter": "612x792", A4: "595x842" };
  for (const [paper, size] of Object.entries(sizes)) {
    await page.getByLabel(paper).check();
    const pdf = await page.pdf({ preferCSSPageSize: true });
    expect(pages(pdf), paper).toEqual([size]);
  }
});

test("a long label with no spaces wraps on the page", async ({ page }) => {
  await makeCode(page, "x".repeat(200));
  await page.emulateMedia({ media: "print" });
  const label = sheet(page).getByText("x".repeat(200));
  const wide = await label.evaluate(
    (element) => element.scrollWidth > element.clientWidth,
  );
  expect(wide).toBe(false);
});

test("a page with nothing to print prints as it is", async ({ page }) => {
  await page.goto("/admin/items");
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("banner")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Items" })).toBeVisible();
});
