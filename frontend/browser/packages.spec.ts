/**
 * Packages on the scan page (docs/decisions/0020): a package counted is
 * saved as sealed packages of it, so stock keeps them sealed where they
 * arrive (inventory-tng-8i4.133.2). Each test has a cable of its own
 * (own.ts), so none sees another's open packages.
 */
import { expect, type Page, test } from "@playwright/test";
import { ownCable } from "./own";

/** Picks `cable` on the scan page, from a search for it. */
async function pick(page: Page, cable: string): Promise<void> {
  await page.getByLabel(/Find an item/).fill(cable);
  await page.getByRole("button", { name: cable, exact: true }).click();
}

test("a package counted on the scan page is saved sealed", async ({ page }) => {
  const cable = await ownCable(page);
  await page.goto("/scan");
  await page.getByLabel("Your name").selectOption({ label: "Sam Example" });
  await page
    .getByLabel("From", { exact: true })
    .selectOption({ label: "Supernode 1 › Shelf A" });
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Van" });
  await pick(page, cable);
  // Picking it adds a metre; a box instead.
  await page.getByLabel(/^Box of 305 m/).fill("1");
  await page.getByLabel(/^Loose/).fill("");
  const saving = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/transactions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save" }).click();
  const response = await saving;
  expect(response.status()).toBe(201);
  expect(response.request().postDataJSON().lines).toEqual([
    {
      item_id: expect.any(Number),
      quantity: "305",
      package_id: expect.any(Number),
    },
  ]);
});

test("a package opened on the scan page is saved as an opening", async ({
  page,
}) => {
  const cable = await ownCable(page);
  await page.goto("/scan");
  await page.getByLabel("Your name").selectOption({ label: "Sam Example" });
  await page.getByLabel("Opening packages").check();
  await page
    .getByLabel("Where", { exact: true })
    .selectOption({ label: "Supernode 1 › Shelf A" });
  await pick(page, cable);
  await page.getByLabel(/^Box of 305 m/).fill("1");
  await page.getByLabel(/^Loose/).fill("");
  const saving = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/transactions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save" }).click();
  const response = await saving;
  expect(response.status()).toBe(201);
  const body = response.request().postDataJSON();
  expect(body.kind).toBe("open");
  expect(body.from_location_id).toBe(body.to_location_id);
  // The cable is followed once opened: the box is an open one now,
  // to cut from (inventory-tng-8i4.133.4).
  const key = body.lines[0].open_package_key;
  expect(key).toEqual(expect.any(String));
  await page.goto("/scan");
  await page.getByLabel("Moving stock").check();
  await page
    .getByLabel("From", { exact: true })
    .selectOption({ label: "Supernode 1 › Shelf A" });
  await page.getByLabel("To", { exact: true }).selectOption({ label: "Van" });
  await pick(page, cable);
  await page.getByLabel(/^Box of 305 m/).fill("");
  await page.getByLabel(/^Loose/).fill("");
  await page.getByLabel(/^From the open Box of 305 m: 305 m left/).fill("20");
  const cutting = page.waitForResponse(
    (each) =>
      each.url().endsWith("/api/transactions") &&
      each.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save" }).click();
  const cut = await cutting;
  expect(cut.status()).toBe(201);
  expect(cut.request().postDataJSON().lines).toEqual([
    { item_id: expect.any(Number), quantity: "20", open_package_key: key },
  ]);
  // Said to be used up by its button, alone: a report (8i4.133.5).
  await page.goto("/scan");
  await page
    .getByLabel("From", { exact: true })
    .selectOption({ label: "Supernode 1 › Shelf A" });
  await pick(page, cable);
  await page.getByLabel(/^Box of 305 m/).fill("");
  await page.getByLabel(/^Loose/).fill("");
  await page.getByLabel("Used up: what is left is no use").check();
  const reporting = page.waitForResponse(
    (each) =>
      each.url().endsWith("/api/transactions") &&
      each.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save" }).click();
  const report = await reporting;
  expect(report.status()).toBe(201);
  expect(report.request().postDataJSON()).toMatchObject({
    kind: "report",
    lines: [],
    used_up: [key],
  });
});

test("opening, an item fills its package sealed where it is opened", async ({
  page,
}) => {
  const cable = await ownCable(page);
  // A box received at the shelf, so one is sealed there.
  await page.goto("/scan");
  await page.getByLabel("Your name").selectOption({ label: "Sam Example" });
  await page.getByLabel("Receiving stock").check();
  await page
    .getByLabel("To", { exact: true })
    .selectOption({ label: "Supernode 1 › Shelf A" });
  await pick(page, cable);
  await page.getByLabel(/^Box of 305 m/).fill("1");
  await page.getByLabel(/^Loose/).fill("");
  const receiving = page.waitForResponse(
    (each) =>
      each.url().endsWith("/api/transactions") &&
      each.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save" }).click();
  expect((await receiving).status()).toBe(201);
  // Loaded again, the page knows it is sealed there, and nowhere else
  // (inventory-tng-f9h).
  await page.goto("/scan");
  await page.getByLabel("Opening packages").check();
  const where = page.getByLabel("Where", { exact: true });
  const box = page.getByLabel(/^Box of 305 m/);
  await where.selectOption({ label: "Van" });
  await pick(page, cable);
  await expect(box).toHaveValue("");
  // Picking the place fills a line not typed into (inventory-tng-prp).
  await where.selectOption({ label: "Supernode 1 › Shelf A" });
  await expect(box).toHaveValue("1");
  // With no place, its one package, then none where none is sealed.
  await page.getByRole("button", { name: "Remove" }).click();
  await where.selectOption({ label: "Pick a place" });
  await pick(page, cable);
  await expect(box).toHaveValue("1");
  await where.selectOption({ label: "Van" });
  await expect(box).toHaveValue("");
});
