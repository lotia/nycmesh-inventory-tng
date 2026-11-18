/**
 * A list's headings and "Show 25 · 50 · 100 · All" work without
 * JavaScript, as links, and a heading's link is a tap target of 44 px
 * (docs/decisions/0019, Lists). Which rows each shows, and in what order,
 * the backend tests check (test_items_routes_list.py).
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";

test.use({ javaScriptEnabled: false });

test("Show and a heading's link work without JavaScript, the heading's link a tap target of 44 px", async ({
  page,
}) => {
  await page.goto("/admin/items");
  const sizes = page.getByRole("navigation", { name: "Rows" });
  await sizes.getByRole("link", { name: "25" }).click();
  await expect(page).toHaveURL(/per=25/);
  await expect(sizes.getByRole("link", { name: "25" })).toHaveAttribute(
    "aria-current",
    "page",
  );

  const heading = page.getByRole("columnheader", { name: "Item" });
  await expect(heading).toHaveAttribute("aria-sort", "ascending");
  const height = await heading
    .getByRole("link")
    .evaluate((link) => link.getBoundingClientRect().height);
  expect(height).toBeGreaterThanOrEqual(44);
  await heading.getByRole("link").click();
  await expect(page).toHaveURL(/sort=name&order=desc&per=25/);
  await expect(heading).toHaveAttribute("aria-sort", "descending");
});
