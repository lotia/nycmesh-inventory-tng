/**
 * A number and unit nothing settles is asked about once, inline, on the
 * item form, and the answer sent from the browser saves the item, read as
 * answered (docs/decisions/0021, Ali, 2026-11-15, inventory-tng-c0c.3).
 * Here for the page that asks, which no link leads to, so the crawl never
 * checks it against the policy (policy.ts). What is asked and offered,
 * "Not right?", the reading rules' list and the seeding page the backend
 * tests check (test_admin_items_units.py, test_reading_rules.py).
 *
 * Rules are kept by the unit as typed after a number, for every test at
 * once, so each browser's test reads a unit of its own (UNITS), which no
 * other test's name has, and starts by deleting its unit's rule, if a run
 * before left one (forget).
 */
import { expect, type Page, type TestInfo } from "@playwright/test";
import { addItem, unique } from "./own";
import { test } from "./policy";

/** Each browser's unit the form asks about with no word near it (a data
 * rate, or not), and the data rate it is read as. */
const CHROMIUM: [string, string] = ["g", "Gbps"];
const UNITS: Record<string, [string, string]> = {
  chromium: CHROMIUM,
  webkit: ["M", "Mbps"],
};
const unitOf = (info: TestInfo): [string, string] =>
  UNITS[info.project.name] ?? CHROMIUM;

/** Its unit's reading rule deleted, if there is one. */
async function forget(page: Page, unit: string): Promise<void> {
  await page.goto("/admin/unit-readings");
  const rule = page.getByRole("link", { name: unit, exact: true });
  if ((await rule.count()) > 0) {
    await rule.click();
    await page.getByRole("button", { name: "Delete", exact: true }).click();
    await expect(page.getByRole("status")).toContainText("Deleted.");
  }
}

test("a unit nothing settles is asked once, and the answer saves the item, read so", async ({
  page,
}, info) => {
  const [unit, rate] = unitOf(info);
  await forget(page, unit);
  const n = 100 + Math.floor(Math.random() * 900);
  const name = unique(`Box ${n}${unit}`);
  await page.goto("/admin/items");
  await page.getByLabel(/^Name/).fill(name);
  await page.getByLabel(/^Category/).selectOption({ label: "Cables" });
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const asked = page.getByRole("group", {
    name: `“${n}${unit}” is new. Is it a unit?`,
  });
  await asked.getByLabel(`${n}${rate} · data rate`).check();
  // Shown with the unit spaced from its number (docs/decisions/0021).
  await addItem(page, name.replace(`${n}${unit}`, `${n} ${unit}`));
  await expect(
    page.getByText(`Read as ${n}${rate} · data rate. Saved as typed.`),
  ).toBeVisible();
});
