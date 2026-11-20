/**
 * The Search the web link on the new-item form follows what is typed in
 * its manufacturer and part number, with the app's own script under its
 * policy (inventory-tng-8i4.35). Never followed here: it would reach
 * DuckDuckGo. Its address and attributes as the page is sent the backend
 * tests check (test_items_routes_web_search.py).
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";

test("the search link follows the manufacturer and part number as typed", async ({
  page,
}) => {
  await page.goto("/admin/items");
  const link = page.getByRole("link", { name: "Search the web" });
  await expect(link).toBeHidden();
  await page.getByLabel(/^Manufacturer/).fill("Ubiquiti");
  await page.getByLabel(/^Part number/).fill("LBE-5AC/Gen2");
  await expect(link).toBeVisible();
  await expect(link).toHaveAttribute(
    "href",
    "https://duckduckgo.com/?q=Ubiquiti+LBE-5AC%2FGen2",
  );
});
