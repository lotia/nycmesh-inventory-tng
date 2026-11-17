/**
 * A long list's search box suggests as one types, and picking one narrows
 * the list to it (docs/decisions/0019). Searches ignore accents, the scan
 * page's too (docs/decisions/0021), and read units after a number alike on
 * both sides, as the unit tests' shared vectors say.
 */
import { expect } from "@playwright/test";
import { ownCable } from "./own";
import { test } from "./policy";

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

test("a search reads units after a number alike, on both sides", async ({
  page,
}) => {
  // The unit tests check each spelling on both sides (units-vectors.json):
  // this shows that each side's search uses that shared reading, and that
  // the scan page shows the name spaced and finds it as copied from a page.
  const what = "Cable 100 feet, PoE 48 VDC";
  const words = "Cable 100\u00a0feet, PoE 48\u00a0VDC";
  const name = await ownCable(page, what, words);
  const shown = name.replace(what, words);
  // Neither form whole: each group as typed or respelt (Ali, 2026-11-15).
  const typed = name.replace(what, "100 ft 48 V");
  await page.goto("/scan");
  const find = page.getByLabel(/Find an item/);
  const found = page.getByRole("button", { name: shown, exact: true });
  for (const each of [typed, shown]) {
    // Emptied first, so what is found is found by this search.
    await find.fill("");
    await expect(found).toBeHidden();
    await find.fill(each);
    await expect(found).toBeVisible();
    // A role's name is matched with its spaces evened out; its text is not.
    expect(await found.textContent()).toBe(shown);
  }
  await page.goto(`/admin/items?q=${encodeURIComponent(typed)}`);
  await expect(
    page.getByRole("table").getByRole("link", { name: shown, exact: true }),
  ).toBeVisible();
});
