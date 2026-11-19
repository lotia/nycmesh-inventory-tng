/**
 * Saves an older build of the scan page left on a phone (frontend/src/scan/
 * older-builds.ts, inventory-tng-kk1, inventory-tng-3c0): one that goes back whole
 * is put in the empty cart without a tap; the rest are listed in words, to
 * add to the cart and save again, on a phone's width. The cable is the
 * test's own (own-rows.ts).
 */
import { expect, type Locator, type Page } from "@playwright/test";
import { test } from "./csp-violations";
import { holdSaves } from "./held-saves";
import { ownCable, record } from "./own-rows";

const HEADING = "Checked out items. Add to cart or discard before sending";
const PUT_BACK = "Added back to cart. Review before Record.";

/** What older builds left, as the page lists it. */
const listingOf = (page: Page) =>
  page.locator("section", {
    has: page.getByRole("heading", { name: HEADING }),
  });

/** The id of the item whose page is open. */
const itemIdOf = (page: Page) =>
  Number(new URL(page.url()).pathname.split("/").at(-1));

/** The Discard of the listed line that says `words`. */
const lineDiscard = (listing: Locator, words: string) =>
  listing
    .getByRole("listitem")
    .filter({ hasText: words })
    .last()
    .getByRole("button", { name: "Discard" });

/** The scan page's ids for a name and two places, by what it offers. */
async function choices(page: Page) {
  const value = async (label: string, option: string) =>
    Number(
      await page
        .getByLabel(label, { exact: true })
        .locator("option", { hasText: option })
        .first()
        .getAttribute("value"),
    );
  return {
    volunteerId: await value("Your name", "Sam Example"),
    fromId: await value("From", "Supernode 1 › Shelf A"),
    toId: await value("To", "Van"),
  };
}

/** Saves as a build keeping the outbox under v3 left them: waiting, never
 * sent, each a move of `lines`, queued in this order. */
async function leave(page: Page, ...saves: unknown[][]) {
  const { volunteerId, fromId, toId } = await choices(page);
  await page.evaluate(
    (entries) =>
      localStorage.setItem("inventory.outbox.v3", JSON.stringify(entries)),
    saves.map((lines, at) => ({
      body: {
        key: crypto.randomUUID(),
        kind: "move",
        volunteer_id: volunteerId,
        from_location_id: fromId,
        to_location_id: toId,
        lines,
      },
      what: "Move: 1 item",
      queuedAt: Date.now() - 60_000 + at,
      state: "waiting",
      waitingFor: "network",
      note: null,
      transactionId: null,
      failures: 0,
    })),
  );
  return { volunteerId, fromId, toId };
}

/** Saves the cart anew, and what it sent. */
async function save(page: Page) {
  const response = await record(page);
  expect(response.status()).toBe(201);
  return response.request().postDataJSON();
}

test("a save an older build left goes back in the cart, whole without a tap, and is saved", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = itemIdOf(page);
  await page.goto("/scan");
  const { volunteerId, fromId, toId } = await leave(
    page,
    [{ item_id: itemId, quantity: "2" }],
    // A line no build wrote: not whole, so it waits for a tap.
    [{ item_id: itemId, quantity: "3" }, { quantity: "1" }],
  );
  await page.reload();

  // The first, whole, is in the cart already; the other is listed.
  await expect(page.getByText(PUT_BACK)).toBeVisible();
  await expect(page.getByLabel("To", { exact: true })).toHaveValue(
    String(toId),
  );
  const listing = listingOf(page);
  await expect(listing).toContainText(
    "Moving stock from Supernode 1 › Shelf A to Van, by Sam Example",
  );
  await expect(listing.getByText(`${cable}: 3 m`)).toBeVisible();
  await expect(
    listing.getByText("Could not read 1 previously added item, scan it again."),
  ).toBeVisible();
  // It fits a phone, buttons and all.
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);
  expect(await save(page)).toMatchObject({
    kind: "move",
    volunteer_id: volunteerId,
    from_location_id: fromId,
    to_location_id: toId,
    lines: [{ item_id: itemId, quantity: "2" }],
  });

  await listing.getByRole("button", { name: "Add to cart" }).click();
  await expect(listing).toHaveCount(0);
  await expect(page.getByText(PUT_BACK)).toBeVisible();
  expect(await save(page)).toMatchObject({
    lines: [{ item_id: itemId, quantity: "3" }],
  });
  expect(
    await page.evaluate(() => localStorage.getItem("inventory.outbox.v3")),
  ).toBeNull();
});

test("a save's package retired since is named, never offered, and goes by its line's Discard", async ({
  page,
}) => {
  const cable = await ownCable(page, "Retired cable");
  const itemId = itemIdOf(page);
  const listing = listingOf(page);
  const box = "Box of 305 m";
  const retireBox = page.getByRole("button", { name: `Retire ${box}` });
  const packageId = Number(
    (
      (await page.locator("form", { has: retireBox }).getAttribute("action")) ??
      ""
    )
      .split("/")
      .at(-2),
  );
  await retireBox.click();
  await expect(
    page.getByRole("button", { name: `Bring it back ${box}` }),
  ).toBeVisible();
  await page.goto("/scan");
  await leave(page, [
    { item_id: itemId, quantity: "610", package_id: packageId },
    { item_id: itemId, quantity: "3" },
  ]);
  await page.reload();

  // Named from the catalogue (retired_packages), in bold, without "Sorry."
  await expect(listing.getByText(`${cable}: 2 × ${box}`)).toBeVisible();
  const note = listing.locator(".alert", {
    hasText: "This item has been retired.",
  });
  await expect(note.locator("strong")).toHaveText(
    "This item has been retired.",
  );
  await lineDiscard(listing, `${cable}: 2 × ${box}`).click();
  // What is left goes back whole, without a tap: its only count, the box
  // not offered. What such a cart saves ScanPage.older-build.test.tsx
  // checks.
  await expect(listing).toHaveCount(0);
  await expect(page.getByText(PUT_BACK)).toBeVisible();
  await expect(page.getByLabel("How many m")).toHaveValue("3");
  await expect(page.getByLabel(new RegExp(`^${box}`))).toHaveCount(0);
});

/** Two saves of a new cable, of 2 m and of 3 m, left on the scan page after
 * the cable was retired with none left. The cable's name. */
async function leaveOfRetiredItem(page: Page) {
  const cable = await ownCable(page, "Retired cable");
  const itemId = itemIdOf(page);
  await page.getByRole("button", { name: "Retire", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Retired.");
  await page.goto("/scan");
  await leave(
    page,
    [{ item_id: itemId, quantity: "2" }],
    [{ item_id: itemId, quantity: "3" }],
  );
  await page.reload();
  return cable;
}

test("saves of an item retired since name it and are never offered", async ({
  page,
}) => {
  const cable = await leaveOfRetiredItem(page);
  const listing = listingOf(page);
  await expect(listing.getByText("Item retired")).toHaveCount(2);
  await expect(listing.getByText(`${cable}: 2 m`)).toBeVisible();
  await expect(
    listing.getByRole("button", { name: "Cannot Add to Cart" }).first(),
  ).toBeDisabled();
  await expect(page.getByText(PUT_BACK)).toHaveCount(0);
});

test("saves of an item retired since go by their line's Discard, or by their own once confirmed", async ({
  page,
}) => {
  const cable = await leaveOfRetiredItem(page);
  const listing = listingOf(page);
  // The line's own Discard, without asking: the save goes with its only
  // line.
  await lineDiscard(listing, `${cable}: 2 m`).click();
  await expect(listing.getByText(`${cable}: 2 m`)).toHaveCount(0);
  await expect(listing.getByText(`${cable}: 3 m`)).toBeVisible();
  // The save's own asks first.
  await listing.getByRole("button", { name: "Discard" }).last().click();
  await expect(listing.getByText("Confirm discard")).toBeVisible();
  await listing.getByRole("button", { name: "Discard" }).last().click();
  await expect(listing).toHaveCount(0);
  expect(
    await page.evaluate(() => localStorage.getItem("inventory.outbox.v3")),
  ).toBeNull();
});

/** One left, which the older build's tab then saves, as its request in
 * flight would; then the page loaded again, which puts it back. The
 * transaction the older tab saved. */
async function leaveSaved(page: Page, itemId: number) {
  await leave(page, [{ item_id: itemId, quantity: "2" }]);
  const [left] = JSON.parse(
    (await page.evaluate(() => localStorage.getItem("inventory.outbox.v3"))) ??
      "[]",
  );
  const first = await page.request.post("/api/transactions", {
    data: left.body,
  });
  expect(first.status()).toBe(201);
  await page.reload();
  await expect(page.getByText(PUT_BACK)).toBeVisible();
  return (await first.json()).transaction_id as number;
}

/** The batch endpoint's next answer of `status`. */
const answered = (page: Page, status: number) =>
  page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/transactions") &&
      response.status() === status,
  );

/** What the page says first of a save another tab recorded first, and the
 * heading over what was not recorded (Ali, inventory-tng-0gg, 2026-11-15). */
const NOT_RECORDED = "Some items were not recorded. Details below.";
const MAKE_CHANGES = "Not recorded. Make any changes and record again.";
const IN_CART = "These are in your cart, waiting to be recorded.";
const ADD_THESE = "Add these to cart";

/** Its entry in the outbox's list. */
const refusedEntry = (page: Page) =>
  page.getByRole("listitem").filter({ hasText: NOT_RECORDED });

test("a save an older build's tab saved meanwhile is saved once, under its key; changed after it went back, it is not recorded, the stock is refreshed, it shows what differs until dismissed, and what it adds goes in the empty cart by itself", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = itemIdOf(page);
  await page.goto("/scan");

  const saved = await leaveSaved(page, itemId);
  const replayed = await record(page);
  expect(replayed.status()).toBe(200);
  expect(await replayed.json()).toEqual({ transaction_id: saved });

  const first = await leaveSaved(page, itemId);
  await page.getByLabel(/^Loose/).fill("3");
  const refused = answered(page, 409);
  // Then the page asks for the stock again at once, which shows what the
  // other tab recorded (Ali, inventory-tng-g0e, d).
  const refreshed = page.waitForResponse(
    (response) =>
      response.url().includes("/api/catalogue") && response.status() === 200,
  );
  await page.getByRole("button", { name: "Record" }).click();
  await refused;
  await refreshed;
  // One note, bold whole, then what the other tab recorded and what was
  // not, with Dismiss at once and no Reload.
  const entry = refusedEntry(page);
  await expect(entry.locator("p.alert strong").first()).toHaveText(
    NOT_RECORDED,
  );
  await expect(
    entry.getByText(`Recorded in another tab in transaction ${first}`),
  ).toBeVisible();
  await expect(entry.getByText(`${cable}: 2 m`, { exact: true })).toBeVisible();
  await expect(entry.getByText(MAKE_CHANGES)).toBeVisible();
  await expect(entry.getByText(`${cable}: 1 m more`)).toBeVisible();
  await expect(entry.getByText(/Scan again/)).toHaveCount(0);
  await expect(entry.getByRole("button", { name: "Reload" })).toHaveCount(0);
  // The difference goes in the empty cart by itself, under a new key,
  // once, and is said to be there until recorded.
  await expect(page.getByLabel(/^Loose/)).toHaveValue("1");
  await expect(entry.getByText(IN_CART)).toBeVisible();
  await expect(entry.getByRole("button", { name: ADD_THESE })).toHaveCount(0);
  const before = await entry.boundingBox();
  const difference = await save(page);
  expect(difference.lines).toEqual([
    expect.objectContaining({ item_id: itemId, quantity: "1" }),
  ]);
  // Recorded, its words are hidden, keeping their room, so Dismiss below
  // them does not move (Ali, 2026-11-19, inventory-tng-2lad).
  await expect(entry.getByText(IN_CART)).toBeHidden();
  expect((await entry.boundingBox())?.height).toBe(before?.height);
  await entry.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText(NOT_RECORDED)).toHaveCount(0);
});

test("what a save another tab recorded first adds waits on a button while the cart has lines, and goes in by itself once it is empty (inventory-tng-0gg)", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = itemIdOf(page);
  await page.goto("/scan");
  await leaveSaved(page, itemId);
  await page.getByLabel(/^Loose/).fill("3");
  // Its refusal held until the cart has lines again.
  const release = await holdSaves(page);
  await page.getByRole("button", { name: "Record" }).click();
  await expect(page.getByText("Nothing in the cart yet.")).toBeVisible();
  await page.getByLabel(/Find an item/).fill(cable);
  await page.getByRole("button", { name: cable, exact: true }).click();
  await page.getByLabel(/^Loose/).fill("5");
  const refused = answered(page, 409);
  release();
  await refused;
  await page.unroute("**/api/transactions");

  const entry = refusedEntry(page);
  await expect(entry.getByText(`${cable}: 1 m more`)).toBeVisible();
  await expect(entry.getByText(IN_CART)).toHaveCount(0);
  await entry.getByRole("button", { name: ADD_THESE }).click();
  await expect(
    entry.getByText(
      "Record or discard the current cart before adding more items",
    ),
  ).toBeVisible();
  await expect(page.getByLabel(/^Loose/)).toHaveValue("5");
  await page.getByRole("button", { name: "Discard cart" }).click();
  await page.getByRole("button", { name: "Discard", exact: true }).click();
  await expect(page.getByLabel(/^Loose/)).toHaveValue("1");
  await expect(entry.getByText(IN_CART)).toBeVisible();
  await expect(entry.getByRole("button", { name: ADD_THESE })).toHaveCount(0);
});

test("a save another tab recorded first, taken down since, says an administrator will correct it, and the administrators' home lists it until corrected (inventory-tng-0gg)", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = itemIdOf(page);
  await page.goto("/scan");
  const first = await leaveSaved(page, itemId);
  await page.getByLabel(/^Loose/).fill("1");
  const refused = answered(page, 409);
  await page.getByRole("button", { name: "Record" }).click();
  await refused;
  const entry = refusedEntry(page);
  await expect(entry.getByText(`${cable}: 1 m fewer`)).toBeVisible();
  const lowered =
    "Recorded 2 m, you changed it to 1 m. An administrator will correct it.";
  await expect(entry.locator("p.alert strong").last()).toHaveText(lowered);
  await expect(entry.getByRole("button", { name: ADD_THESE })).toHaveCount(0);

  await page.goto("/admin");
  const needs = page.locator("#needs-action");
  await expect(needs.getByText("Recorded more than moved")).toBeVisible();
  await needs.getByRole("link", { name: `${cable}: 1 m` }).click();
  const row = page.getByRole("row").filter({ hasText: String(first) });
  await expect(row.getByText("1 m", { exact: true })).toBeVisible();
  await row.getByRole("button", { name: /^Correct/ }).click();
  await expect(page.getByText("Corrected.", { exact: true })).toBeVisible();
  await page.goto("/admin");
  await expect(
    page.locator("#needs-action").getByRole("link", { name: `${cable}: 1 m` }),
  ).toHaveCount(0);
});

test("a cart this build filled, which its tab recorded as a newer build took it over, is recorded once, under the cart's key (inventory-tng-e2d)", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = itemIdOf(page);
  await page.goto("/scan");
  const { volunteerId, fromId, toId } = await choices(page);
  await page.getByLabel("Your name").selectOption(String(volunteerId));
  await page.getByLabel("From", { exact: true }).selectOption(String(fromId));
  await page.getByLabel("To", { exact: true }).selectOption(String(toId));
  await page.getByLabel(/Find an item/).fill(cable);
  await page.getByRole("button", { name: cable, exact: true }).click();
  await page.getByLabel(/^Loose/).fill("2");
  // Left under an older key, as a newer build finds it once out.
  const key = await page.evaluate(() => {
    const kept = localStorage.getItem("inventory.cart.v2") ?? "";
    localStorage.removeItem("inventory.cart.v2");
    localStorage.setItem("inventory.cart.v1", kept);
    return JSON.parse(kept).batchKey as string;
  });
  expect(key).toEqual(expect.any(String));
  // Its tab records it, its request in flight as the newer page loads.
  const first = await page.request.post("/api/transactions", {
    data: {
      key,
      kind: "move",
      volunteer_id: volunteerId,
      from_location_id: fromId,
      to_location_id: toId,
      lines: [{ item_id: itemId, quantity: "2" }],
    },
  });
  expect(first.status()).toBe(201);
  const saved = (await first.json()).transaction_id as number;
  await page.reload();
  await expect(page.getByText(PUT_BACK)).toBeVisible();
  const response = await record(page);
  expect(response.request().postDataJSON().key).toBe(key);
  expect(response.status()).toBe(200);
  expect(await response.json()).toEqual({ transaction_id: saved });
});

// What only a real browser shows: a real double click, both its clicks
// landing before the page settles, and the ignored taps taken again on a
// real clock. The rest of how a line's Discard behaves is in
// ScanPage.older-build.test.tsx (inventory-tng-vtda.1).
test("a double tap on a line's Discard discards that line only, and focuses the entry; once settled, a tap is taken again", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = itemIdOf(page);
  await page.goto("/scan");
  const open = crypto.randomUUID();
  await leave(page, [
    { item_id: itemId, quantity: "3", open_package_key: open },
    { item_id: itemId, quantity: "5", open_package_key: open },
    { item_id: itemId, quantity: "7", open_package_key: open },
  ]);
  await page.reload();

  const listing = listingOf(page);
  await lineDiscard(listing, `${cable}: 3 m from an open package`).dblclick();
  await expect(
    listing.getByText(`${cable}: 3 m from an open package`),
  ).toHaveCount(0);
  await expect(
    listing.getByText(`${cable}: 5 m from an open package`),
  ).toBeVisible();
  // Focus on the entry's heading, not the line that moved up.
  await expect(listing.locator("#older-0")).toBeFocused();
  // Once the taps are taken again, the next line goes too, alone.
  await expect(async () => {
    await lineDiscard(listing, `${cable}: 5 m from an open package`).click();
    await expect(listing.getByText(`${cable}: 5 m`)).toHaveCount(0, {
      timeout: 1000,
    });
  }).toPass();
  await expect(
    listing.getByText(`${cable}: 7 m from an open package`),
  ).toBeVisible();
});
