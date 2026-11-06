/**
 * A registration code prints cleanly (inventory-tng-8i4.61): only the sheet,
 * as a poster or four cards, on US Letter or A4.
 */
import { expect, type Page, test } from "@playwright/test";

/** Makes a code, confirming with the password if asked. */
async function makeCode(page: Page, label = "Hub open day"): Promise<void> {
  await page.goto("/admin/devices");
  await page.getByLabel("What it's for").fill(label);
  await page.getByRole("button", { name: "Make a code" }).click();
  const made = page.getByRole("heading", { name: "A new registration code" });
  const password = page.getByLabel("Your password");
  await expect(made.or(password)).toBeVisible();
  if (await password.isVisible()) {
    await password.fill(process.env.ADMIN_PASSWORD ?? "");
    await page.getByRole("button", { name: "Confirm" }).click();
  }
  await expect(made).toBeVisible();
}

const cards = (page: Page) =>
  page.locator(".print-card").filter({ visible: true });

async function qrWidth(page: Page): Promise<number | undefined> {
  return (await cards(page).first().locator("svg").boundingBox())?.width;
}

test("printing shows only the sheet: a poster, or four cards", async ({
  page,
}) => {
  await makeCode(page);
  await expect(cards(page)).toHaveCount(0);
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("banner")).toBeHidden();
  await expect(page.getByRole("button", { name: "Print" })).toBeHidden();
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page)).toContainText("Hub open day");
  // A poster's code is large, a card's smaller: 96 pixels to the inch.
  expect(await qrWidth(page)).toBe(5 * 96);
  await page.emulateMedia({ media: "screen" });
  await page.getByLabel("Cards, four to a page").check();
  await page.emulateMedia({ media: "print" });
  await expect(cards(page)).toHaveCount(4);
  expect(await qrWidth(page)).toBe(2 * 96);
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

test("each layout fits one page of each paper", async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== "chromium", "only Chromium prints to PDF");
  // As long a label as an administrator might type.
  await makeCode(page, "A label ".repeat(40));
  const sizes = { "US Letter": "612x792", A4: "595x842" };
  for (const [paper, size] of Object.entries(sizes)) {
    for (const layout of ["A poster, one to a page", "Cards, four to a page"]) {
      await page.getByLabel(paper).check();
      await page.getByLabel(layout).check();
      const pdf = await page.pdf({ preferCSSPageSize: true });
      expect(pages(pdf), `${layout}, on ${paper}`).toEqual([size]);
    }
  }
});

test("a page with nothing to print prints as it is", async ({ page }) => {
  await page.goto("/admin/items");
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("banner")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Items" })).toBeVisible();
});
