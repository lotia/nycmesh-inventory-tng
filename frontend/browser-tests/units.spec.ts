/**
 * How a name's units are read on the item forms (docs/decisions/0021, Ali,
 * 2026-11-15, option D): saved as typed; a number and unit nothing settles
 * asked once, inline, the answer kept as a reading rule; under the name,
 * how each is read, with "Not right?" to correct it; and the seeding page,
 * which proposes rules from every name. Each test's number is its own, as
 * rules are kept by the number and unit as typed, for every test at once.
 */
import { expect, type Page, test } from "@playwright/test";
import { unique } from "./own";

/** A number no other test, or run of this one, likely has. */
const number = (): number => 100 + Math.floor(Math.random() * 900);

/** Fills in a new item named `name`, in Cables, counted each. */
async function newItem(page: Page, name: string): Promise<void> {
  await page.goto("/admin/items");
  await page.getByLabel(/^Name/).fill(name);
  await page.getByLabel("Category").selectOption({ label: "Cables" });
}

/** Adds it, saying it is a different item if a close name is asked about. */
async function add(page: Page, name: string): Promise<void> {
  const button = page.getByRole("button", { name: "Add", exact: true });
  await button.click();
  const heading = page.getByRole("heading", { level: 1, name, exact: true });
  const asked = page.getByText("A different item?");
  await expect(heading.or(asked)).toBeVisible();
  if (await asked.isVisible()) {
    await button.click();
  }
  await expect(heading).toBeVisible();
}

test("a unit nothing settles is asked once, then said under the name", async ({
  page,
}) => {
  const n = number();
  const name = unique(`Uplink SFP+ ${n}G`);
  await newItem(page, name);
  await page.getByRole("button", { name: "Add", exact: true }).click();
  const asked = page.getByRole("group", {
    name: `“${n}G” is new. Is it a unit?`,
  });
  await expect(asked).toBeVisible();
  await expect(asked.getByLabel(`${n}g · mass`)).toBeVisible();
  await expect(asked.getByLabel(`Not a unit: keep “${n}G”`)).toBeVisible();
  await asked.getByLabel(`${n}Gbps · data rate`).check();
  await add(page, name);
  // Saved as typed, read as answered.
  await expect(
    page.getByText(`Read as ${n}Gbps · data rate. Saved as typed.`),
  ).toBeVisible();

  // Not right? A correction, saved with the item, is the rule now.
  await page.getByText("Not right?").first().click();
  await page.getByLabel(`Not a unit: keep “${n}G”`).first().check();
  await page.getByRole("button", { name: "Save", exact: true }).first().click();
  await expect(page.getByText(`“${n}G” is not read as a unit.`)).toBeVisible();
  await page.goto("/admin/unit-readings");
  const row = page.getByRole("row", { name: new RegExp(`^${n}G `) });
  await expect(row).toContainText("Not a unit");
});

test("the seeding page proposes readings, each accepted as a rule", async ({
  page,
}) => {
  // Read loosely by the table, as millifarads: proposed so, to confirm.
  const n = number();
  const name = unique(`Cap ${n}MF`);
  await newItem(page, name);
  await add(page, name);
  await expect(page.getByText(`Read as ${n}mF · capacitance.`)).toBeVisible();

  await page.goto("/admin/unit-readings");
  await page
    .getByRole("link", { name: "Propose readings from every name" })
    .click();
  const read = page.getByLabel(`Read “${n}MF” as`);
  await expect(read).toHaveValue("mF");
  await expect(
    page.getByRole("link", { name: "Review one by one" }),
  ).toBeVisible();
  // Change it, and choose for any other test's that nothing settles.
  await read.selectOption({ label: "Not a unit" });
  for (const choose of await page.getByRole("combobox").all()) {
    if ((await choose.inputValue()) === "") {
      await choose.selectOption({ label: "Not a unit" });
    }
  }
  await page.getByRole("button", { name: "Accept all" }).click();
  await expect(page.getByRole("status")).toContainText("Accepted.");
  await expect(page.getByLabel(`Read “${n}MF” as`)).toHaveCount(0);
  await page.goto("/admin/unit-readings");
  await expect(
    page.getByRole("row", { name: new RegExp(`^${n}MF `) }),
  ).toContainText("Not a unit");
});
