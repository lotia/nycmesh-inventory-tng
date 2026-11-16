/**
 * A long list's search box suggests as one types, and picking one narrows
 * the list to it (docs/decisions/0019). Searches ignore accents, the scan
 * page's too (docs/decisions/0021), and read units after a number alike,
 * as well as finding what they found before units were read, and match a
 * search mixing spellings group by group.
 */
import { expect, test } from "@playwright/test";
import { addItem, ownCable, unique } from "./own";

test("a suggestion picked with the keyboard narrows the list", async ({
  page,
}) => {
  await page.goto("/admin/items");
  const first = page
    .getByRole("table")
    .getByRole("cell")
    .getByRole("link")
    .first();
  const name = (await first.textContent()) ?? "";
  const box = page.getByRole("combobox", { name: "Find items" });
  // A typo: its first word with a letter dropped.
  const word = name.split(" ")[0] ?? name;
  await box.pressSequentially(word.slice(0, -1));
  const options = page.getByRole("listbox").getByRole("option");
  await expect(options.first()).toBeVisible();
  const height = await options
    .first()
    .evaluate((option) => option.getBoundingClientRect().height);
  expect(height).toBeGreaterThanOrEqual(44);
  await box.press("ArrowDown");
  const picked = (await options.first().textContent()) ?? "";
  await box.press("Enter");
  await expect(page).toHaveURL(/pick=\d+/);
  await expect(
    page.getByRole("table").getByRole("cell").getByRole("link"),
  ).toHaveCount(1);
  await expect(page.getByRole("main")).toContainText("matching");
  expect(picked).toContain(
    (await page
      .getByRole("table")
      .getByRole("cell")
      .getByRole("link")
      .textContent()) ?? "",
  );
  await page.getByRole("link", { name: "Clear search" }).click();
  await expect(page).toHaveURL(/\/admin\/items$/);
});

test("Escape closes the suggestions, and Enter lists every match", async ({
  page,
}) => {
  await page.goto("/admin/items");
  const box = page.getByRole("combobox", { name: "Find items" });
  await box.pressSequentially("a");
  await expect(page.getByRole("listbox")).toBeVisible();
  await box.press("Escape");
  await expect(page.getByRole("listbox")).toBeHidden();
  await expect(box).toHaveAttribute("aria-expanded", "false");
  await box.press("Enter");
  await expect(page).toHaveURL(/q=a/);
});

test("the scan page finds an item without its accents, and shows them", async ({
  page,
}) => {
  // A word only the server's folded text spells as typed.
  const name = await ownCable(page, "Caféclip");
  await page.goto("/scan");
  await page
    .getByLabel(/Find an item/)
    .fill(name.replace("Caféclip", "cafeclip"));
  await expect(page.getByRole("button", { name, exact: true })).toBeVisible();
});

test("a unit after a number is found however it is spelt, on both sides", async ({
  page,
}) => {
  const name = await ownCable(page, "Cap 10μF", "Cap 10\u00a0μF");
  // Shown with its number and unit spaced.
  const shown = name.replace("10μF", "10\u00a0μF");
  await page.goto("/scan");
  await page.getByLabel(/Find an item/).fill(name.replace("10μF", "10uf"));
  await expect(
    page.getByRole("button", { name: shown, exact: true }),
  ).toBeVisible();
  const typed = name.replace("10μF", "10 µF");
  await page.goto(`/admin/items?q=${encodeURIComponent(typed)}`);
  await expect(
    page.getByRole("table").getByRole("link", { name: shown, exact: true }),
  ).toBeVisible();
});

test("a search finds what it found before units were read, and by units too", async ({
  page,
}) => {
  // Saved as typed: a unit's word never spells a name (Ali, 2026-11-15),
  // and is shown spaced as a symbol is (inventory-tng-tze).
  const what = "PoE 100 feet 48 VDC";
  const words = "PoE 100\u00a0feet 48\u00a0VDC";
  const name = await ownCable(page, what, words);
  const shown = name.replace(what, words);
  const find = page.getByLabel(/Find an item/);
  const found = page.getByRole("button", { name: shown, exact: true });
  await page.goto("/scan");
  // A unit typed shorter than the token it begins, as typed.
  await find.fill(name.replace("48 VDC", "48 V"));
  await expect(found).toBeVisible();
  // A unit spelt otherwise.
  await find.fill(name.replace("100 feet", "100 ft"));
  await expect(found).toBeVisible();
  for (const typed of [
    name.replace("PoE 100 ", ""),
    name.replace("PoE 100 feet", "100 ft"),
  ]) {
    await page.goto(`/admin/items?q=${encodeURIComponent(typed)}`);
    await expect(
      page.getByRole("table").getByRole("link", { name: shown, exact: true }),
    ).toBeVisible();
  }
});

test("a search mixing spellings is matched group by group, on both sides", async ({
  page,
}) => {
  const what = "Cable 100 feet, PoE 48 VDC";
  const words = "Cable 100\u00a0feet, PoE 48\u00a0VDC";
  const name = await ownCable(page, what, words);
  const shown = name.replace(what, words);
  // Neither form whole: each group as typed or respelt (Ali, 2026-11-15).
  const typed = name.replace(what, "100 ft 48 V");
  await page.goto("/scan");
  await page.getByLabel(/Find an item/).fill(typed);
  await expect(
    page.getByRole("button", { name: shown, exact: true }),
  ).toBeVisible();
  await page.goto(`/admin/items?q=${encodeURIComponent(typed)}`);
  await expect(
    page.getByRole("table").getByRole("link", { name: shown, exact: true }),
  ).toBeVisible();
});

test("a name is shown with its number and unit spaced, on both sides", async ({
  page,
}) => {
  // Saved as typed, shown corrected (Ali, 2026-11-15, inventory-tng-ist.3).
  const name = unique("Box 10,000ft");
  const shown = name.replace("10,000ft", "10 000 ft");
  await page.goto("/admin/items");
  await page.getByLabel(/^Name/).fill(name);
  await page.getByLabel(/^Category/).selectOption({ label: "Cables" });
  const heading = await addItem(page, shown);
  expect(await heading.textContent()).toBe(shown);
  await expect(page.getByLabel(/^Name/)).toHaveValue(name);
  await expect(page.getByText(`Shown as ${shown}.`)).toBeVisible();
  await page.goto("/scan");
  // Found as typed, and as shown, as copied from a page.
  for (const typed of [name, shown]) {
    await page.getByLabel(/Find an item/).fill(typed);
    const found = page.getByRole("button", { name: shown, exact: true });
    await expect(found).toBeVisible();
    expect(await found.textContent()).toContain(shown);
  }
});
