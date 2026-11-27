/**
 * Packages on the scan page (docs/decisions/0020): a package counted is
 * saved as sealed packages of it, so stock keeps them sealed where they
 * arrive (inventory-tng-8i4.133.2). Each test has a cable of its own
 * (own-rows.ts), so none sees another's open packages.
 */
import { expect } from "@playwright/test";
import { test } from "./csp-violations";
import { ownCable, record } from "./own-rows";
import { fillMove, fillOpening, pick, SAM, SHELF, settled } from "./scan-page";

test("a package counted on the scan page is saved sealed", async ({ page }) => {
  const cable = await ownCable(page);
  await page.goto("/scan");
  await fillMove(page, cable);
  // Picking it adds a metre; a box instead.
  await page.getByLabel(/^Box of 305\sm/).fill("1");
  await page.getByLabel(/^Loose/).fill("");
  const response = await record(page);
  expect(response.status()).toBe(201);
  expect(response.request().postDataJSON().lines).toEqual([
    {
      item_id: expect.any(Number),
      quantity: "305",
      package_id: expect.any(Number),
      package_size: "305",
      unit: "m",
    },
  ]);
});

test("the same opening recorded twice is one transaction, said by no note (inventory-tng-e2d)", async ({
  page,
}) => {
  const cable = await ownCable(page);
  await page.goto("/scan");
  await fillOpening(page, cable);
  await page.getByLabel(/^Box of 305\sm/).fill("2");
  await page.getByLabel(/^Loose/).fill("");
  const left = await page.evaluate(
    () => localStorage.getItem("inventory.cart.v2") ?? "",
  );
  const first = await record(page);
  expect(first.status()).toBe(201);
  const { transaction_id: recorded } = await first.json();
  // The same cart again, from a tab whose outbox never held it: its
  // emptying not kept, and its outbox lost.
  await page.evaluate((cart) => {
    localStorage.setItem("inventory.cart.v2", cart);
    localStorage.removeItem("inventory.outbox.v5");
  }, left);
  await page.reload();
  const again = await record(page);
  expect(again.request().postDataJSON()).toEqual(
    first.request().postDataJSON(),
  );
  expect(again.status()).toBe(200);
  expect(await again.json()).toEqual({ transaction_id: recorded });
  await expect(page.getByText(/Scan again/)).toHaveCount(0);
});

test("a package opened on the scan page is saved as an opening", async ({
  page,
}) => {
  const cable = await ownCable(page);
  await page.goto("/scan");
  await fillOpening(page, cable);
  await page.getByLabel(/^Box of 305\sm/).fill("1");
  await page.getByLabel(/^Loose/).fill("");
  const response = await record(page);
  expect(response.status()).toBe(201);
  const body = response.request().postDataJSON();
  expect(body.kind).toBe("open");
  expect(body.from_location_id).toBe(body.to_location_id);
  // The cable is followed once opened: the box is an open one now,
  // to cut from (inventory-tng-8i4.133.4).
  const key = body.lines[0].open_package_key;
  expect(key).toEqual(expect.any(String));
  await page.goto("/scan");
  await fillMove(page, cable);
  await page.getByLabel(/^Box of 305\sm/).fill("");
  await page.getByLabel(/^Loose/).fill("");
  // A cut of all it shows ticks the box, and says why (inventory-tng-ck0).
  const cutBox = page.getByLabel(/^Cut or take from the Box of 305\sm opened/);
  const nothingLeft = page.getByLabel("Nothing usable left on it");
  await cutBox.fill("305");
  await expect(nothingLeft).toBeChecked();
  await expect(
    page.getByText("Your cut uses up the Box of 305 m"),
  ).toBeVisible();
  await cutBox.fill("20");
  await expect(nothingLeft).not.toBeChecked();
  const cut = await record(page);
  expect(cut.status()).toBe(201);
  expect(cut.request().postDataJSON().lines).toEqual([
    {
      item_id: expect.any(Number),
      quantity: "20",
      open_package_key: key,
      unit: "m",
    },
  ]);
  // Said to be used up by its button, alone: a report (8i4.133.5).
  await page.goto("/scan");
  await page.getByLabel("From", { exact: true }).selectOption({ label: SHELF });
  await pick(page, cable);
  await page.getByLabel(/^Box of 305\sm/).fill("");
  await page.getByLabel(/^Loose/).fill("");
  await page.getByLabel("Nothing usable left on it").check();
  const report = await record(page);
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
  await page.getByLabel("Your name").selectOption({ label: SAM });
  await page.getByLabel("Receiving stock").check();
  await page.getByLabel("To", { exact: true }).selectOption({ label: SHELF });
  await pick(page, cable);
  await page.getByLabel(/^Box of 305\sm/).fill("1");
  await page.getByLabel(/^Loose/).fill("");
  expect((await record(page)).status()).toBe(201);
  // Loaded again, the page knows it is sealed there, and nowhere else
  // (inventory-tng-f9h).
  await page.goto("/scan");
  await page.getByLabel("Opening packages").check();
  const where = page.getByLabel("Where", { exact: true });
  const box = page.getByLabel(/^Box of 305\sm/);
  await where.selectOption({ label: "Van" });
  await pick(page, cable);
  await expect(box).toHaveValue("");
  // Picking the place fills a line not typed into (inventory-tng-prp).
  await where.selectOption({ label: SHELF });
  await expect(box).toHaveValue("1");
  // With no place, its one package, then none where none is sealed.
  // The page moves focus after painting; a move landing between pick's
  // fill focusing the search box and typing loses the cable's name, so
  // each is waited for (inventory-tng-7bq4). Remove focuses Undo; 'Pick a
  // place' takes Undo away, so the Cart heading.
  await page.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByRole("button", { name: "Undo" })).toBeFocused();
  await where.selectOption({ label: "Pick a place" });
  await expect(
    page.getByRole("heading", { name: "Cart", exact: true }),
  ).toBeFocused();
  // A moment later, as the page ignores taps once one moved what was
  // below (docs/decisions/0019, Double taps).
  await settled(page);
  await pick(page, cable);
  await expect(box).toHaveValue("1");
  await where.selectOption({ label: "Van" });
  await expect(box).toHaveValue("");
});

test("what is on an open package, said, is set by an administrator", async ({
  page,
}) => {
  const cable = await ownCable(page);
  await page.goto("/scan");
  await fillOpening(page, cable);
  await page.getByLabel(/^Box of 305\sm/).fill("1");
  expect((await record(page)).status()).toBe(201);
  // Said to show the wrong length (inventory-tng-wy7).
  await page.goto("/scan");
  await page.getByLabel("Moving stock").check();
  await pick(page, cable);
  await page.getByLabel(/^Box of 305\sm/).fill("");
  await page.getByLabel(/^Loose/).fill("");
  await page.getByRole("button", { name: "Shows the wrong length?" }).click();
  await page
    .getByLabel("What's on the Box of 305 m now (m), if you can tell")
    .fill("250");
  const report = await record(page);
  expect(report.status()).toBe(201);
  expect(report.request().postDataJSON()).toMatchObject({
    kind: "report",
    lengths: [{ left: "250" }],
  });
  await page.goto("/admin");
  await page
    .getByRole("link", { name: `${cable}: 1 \u00d7 Box of 305 m` })
    .click();
  await expect(page.getByText("said 250 m is on it")).toBeVisible();
  await page.getByRole("button", { name: `Set to 250 m ${cable}` }).click();
  await expect(
    page.getByText(
      "Set: a count of each writes the difference from what it showed then; cuts since stay.",
    ),
  ).toBeVisible();
});
