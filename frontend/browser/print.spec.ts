/**
 * A registration code prints cleanly (inventory-tng-8i4.61, 8i4.60.9): only
 * the code, one to a page, on US Letter or A4.
 */
import { expect, type Page, test } from "@playwright/test";
import { prepared } from "./prepared";

/** Makes a code, confirming with the password if asked. */
async function makeCode(page: Page, label = "Hub open day"): Promise<void> {
  await page.goto("/admin/devices");
  await page.getByLabel("What it's for").fill(label);
  await page.getByRole("button", { name: "Make a code" }).click();
  const made = page.getByRole("heading", { name: "A new registration code" });
  const password = page.getByLabel("Your password");
  await expect(made.or(password)).toBeVisible();
  if (await password.isVisible()) {
    await password.fill(prepared().password);
    await page.getByRole("button", { name: "Confirm" }).click();
  }
  await expect(made).toBeVisible();
}

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
