/**
 * A list's headings sort it, pressed again the other way; "Show 25 · 50 ·
 * 100 · All" sets how many rows a page shows; while searching, Best match
 * puts the closest first again. All of it without JavaScript, as links
 * (docs/decisions/0019, Lists).
 */
import { expect, type Page, test } from "@playwright/test";
import { addItem, unique } from "./own";

test.use({ javaScriptEnabled: false });

/** A new item in Cables, named `name`. */
async function ownItem(page: Page, name: string): Promise<void> {
  await page.goto("/admin/items");
  await page.getByLabel(/^Name/).fill(name);
  await page.getByLabel(/^Category/).selectOption({ label: "Cables" });
  await addItem(page, name);
}

/** The items the list shows, in its order. */
function listed(page: Page): Promise<string[]> {
  return page
    .getByRole("table")
    .getByRole("cell")
    .getByRole("link")
    .allTextContents();
}

test("headings sort a search's matches either way, and Best match puts them back", async ({
  page,
}) => {
  const mark = unique("Sorted");
  // The closest match last by name.
  const close = `${mark} Zulu`;
  const far = `Aa ${mark}`;
  await ownItem(page, close);
  await ownItem(page, far);
  await page.goto(`/admin/items?q=${encodeURIComponent(close)}`);
  const best = page.getByRole("link", { name: "Best match" });
  await expect(best).toHaveAttribute("aria-current", "page");
  expect((await listed(page))[0]).toBe(close);

  const heading = page.getByRole("columnheader", { name: "Item" });
  await heading.getByRole("link").click();
  await expect(page).toHaveURL(/sort=name/);
  await expect(heading).toHaveAttribute("aria-sort", "ascending");
  await expect(best).not.toHaveAttribute("aria-current", "page");
  const ascending = (await listed(page)).filter((name) => name.includes(mark));
  expect(ascending).toEqual([far, close]);
  const height = await heading
    .getByRole("link")
    .evaluate((link) => link.getBoundingClientRect().height);
  expect(height).toBeGreaterThanOrEqual(44);

  await heading.getByRole("link").click();
  await expect(page).toHaveURL(/order=desc/);
  await expect(heading).toHaveAttribute("aria-sort", "descending");
  const descending = (await listed(page)).filter((name) => name.includes(mark));
  expect(descending).toEqual([close, far]);

  await best.click();
  await expect(page).not.toHaveURL(/sort=/);
  expect((await listed(page))[0]).toBe(close);
});

test("Show sets how many rows a page shows, kept as it sorts", async ({
  page,
}) => {
  await page.goto("/admin/items");
  const sizes = page.getByRole("navigation", { name: "Rows" });
  await expect(sizes.getByRole("link", { name: "50" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await sizes.getByRole("link", { name: "25" }).click();
  await expect(page).toHaveURL(/per=25/);
  await expect(sizes.getByRole("link", { name: "25" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect((await listed(page)).length).toBeLessThanOrEqual(25);
  await page
    .getByRole("columnheader", { name: "Item" })
    .getByRole("link")
    .click();
  await expect(page).toHaveURL(/sort=name&order=desc&per=25/);
});
