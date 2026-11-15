/**
 * How a name's units are read on the item forms (docs/decisions/0021, Ali,
 * 2026-11-15, option D): saved as typed; a number and unit nothing settles
 * asked once, inline, the answer kept as a reading rule; under the name,
 * how each is read, with "Not right?" to correct it; and the seeding page,
 * which proposes rules from every name. Rules are kept by the unit as typed
 * after a number, for every test at once, so each browser's tests read a
 * unit of their own (UNITS), which no other test's name has; each starts by
 * deleting its unit's rule, if a run before left one (forget), and accepts
 * only its own proposal, so none settles another's.
 */
import { expect, type Page, type TestInfo, test } from "@playwright/test";
import { unique } from "./own";

/** A number for a name, so names the tests make differ. */
const number = (): number => 100 + Math.floor(Math.random() * 900);

/**
 * Each browser's units: one the form asks about with no word near it
 * (a data rate, or not), and one the table reads only loosely, as a
 * prefix of another case.
 */
type Units = { asked: [string, string]; loose: [string, string] };
const CHROMIUM: Units = {
  asked: ["g", "Gbps"],
  loose: ["MF", "mF · capacitance"],
};
const UNITS: Record<string, Units> = {
  chromium: CHROMIUM,
  webkit: { asked: ["M", "Mbps"], loose: ["MV", "mV · voltage"] },
};
const unitsOf = (info: TestInfo): Units => UNITS[info.project.name] ?? CHROMIUM;

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

/** Fills in a new item named `name`, in Cables, counted each. */
async function newItem(page: Page, name: string): Promise<void> {
  await page.goto("/admin/items");
  await page.getByLabel(/^Name/).fill(name);
  await page.getByLabel("Category").selectOption({ label: "Cables" });
}

/**
 * Adds it, saying it is a different item if a close name is asked about.
 * Its page is headed by it as shown: `unit` read after its number, spaced
 * from it (docs/decisions/0021, names shown).
 */
async function add(page: Page, name: string, unit: string): Promise<void> {
  const button = page.getByRole("button", { name: "Add", exact: true });
  await button.click();
  const shown = name.replace(new RegExp(`([0-9])${unit}`), `$1 ${unit}`);
  const heading = page.getByRole("heading", {
    level: 1,
    name: shown,
    exact: true,
  });
  const asked = page.getByText("A different item?");
  await expect(heading.or(asked)).toBeVisible();
  if (await asked.isVisible()) {
    await button.click();
  }
  await expect(heading).toBeVisible();
}

test("a unit nothing settles is asked once, then said under the name", async ({
  page,
}, info) => {
  const [unit, rate] = unitsOf(info).asked;
  await forget(page, unit);
  const n = number();
  const name = unique(`Box ${n}${unit}`);
  await newItem(page, name);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const asked = page.getByRole("group", {
    name: `“${n}${unit}” is new. Is it a unit?`,
  });
  await expect(asked).toBeVisible();
  await expect(
    asked.getByLabel(`Not a unit: keep “${n}${unit}”`),
  ).toBeVisible();
  await asked.getByLabel(`${n}${rate} · data rate`).check();
  await add(page, name, unit);
  // Saved as typed, read as answered.
  await expect(
    page.getByText(`Read as ${n}${rate} · data rate. Saved as typed.`),
  ).toBeVisible();

  // Not right? A correction, saved with the item, is the rule now.
  await page.getByText("Not right?").first().click();
  await page.getByLabel(`Not a unit: keep “${n}${unit}”`).first().check();
  await page.getByRole("button", { name: "Save", exact: true }).first().click();
  await expect(
    page.getByText(`“${n}${unit}” is not read as a unit.`),
  ).toBeVisible();
  await page.goto("/admin/unit-readings");
  const row = page.getByRole("row", { name: new RegExp(`^${unit} `) });
  await expect(row).toContainText("Not a unit");
});

test("the seeding page proposes readings, each accepted as a rule", async ({
  page,
}, info) => {
  // Read loosely by the table: proposed so, to confirm.
  const [unit, reading] = unitsOf(info).loose;
  await forget(page, unit);
  const n = number();
  const name = unique(`Cap ${n}${unit}`);
  await newItem(page, name);
  await add(page, name, unit);
  await expect(page.getByText(`Read as ${n}${reading}.`)).toBeVisible();

  await page.goto("/admin/unit-readings");
  await page
    .getByRole("link", { name: "Propose readings from every name" })
    .click();
  await expect(page.getByLabel(`Read “${unit}” as`)).toHaveValue(
    reading.split(" ")[0] ?? "",
  );
  // One by one, to its own, so it accepts no other test's.
  await page.getByRole("link", { name: "Review one by one" }).click();
  const read = page.getByLabel(`Read “${unit}” as`);
  while ((await read.count()) === 0) {
    await page.getByRole("link", { name: "Skip", exact: true }).click();
  }
  await read.selectOption({ label: "Not a unit" });
  await page.getByRole("button", { name: "Accept", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Accepted.");
  await page.goto("/admin/unit-readings/seed");
  await expect(page.getByLabel(`Read “${unit}” as`)).toHaveCount(0);
  await page.goto("/admin/unit-readings");
  await expect(
    page.getByRole("row", { name: new RegExp(`^${unit} `) }),
  ).toContainText("Not a unit");
});
