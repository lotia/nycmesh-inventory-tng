/**
 * Saves an older build of the scan page left on a phone (frontend/src/
 * older.ts, inventory-tng-kk1, inventory-tng-3c0): one that goes back whole
 * is put in the empty cart without a tap; the rest are listed in words, to
 * add to the cart and save again, on a phone's width. The cable is the
 * test's own (own.ts).
 */
import { expect, type Locator, type Page, test } from "@playwright/test";
import { ownCable } from "./own";

const HEADING = "Checked out items. Add to cart or discard before sending";
const PUT_BACK = "Added back to cart. Review before Record.";

/** What older builds left, as the page lists it. */
const listingOf = (page: Page) =>
  page.locator("section", {
    has: page.getByRole("heading", { name: HEADING }),
  });

/** The scan page's ids for a name and two places, by what it offers. */
/** The Discard of the listed line that says `words`. */
const lineDiscard = (listing: Locator, words: string) =>
  listing
    .getByRole("listitem")
    .filter({ hasText: words })
    .last()
    .getByRole("button", { name: "Discard" });

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

/** Saves the cart, and what it sent. */
async function save(page: Page) {
  const saving = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/transactions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Record" }).click();
  const response = await saving;
  expect(response.status()).toBe(201);
  return response.request().postDataJSON();
}

test("a save an older build left goes back in the cart, whole without a tap, and is saved", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = Number(new URL(page.url()).pathname.split("/").at(-1));
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

test("saves holding an item retired since, with none left, name it, and go by its line's Discard or their own", async ({
  page,
}) => {
  const cable = await ownCable(page, "Retired cable");
  const itemId = Number(new URL(page.url()).pathname.split("/").at(-1));
  await page.getByRole("button", { name: "Retire", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Retired.");
  await page.goto("/scan");
  await leave(
    page,
    [{ item_id: itemId, quantity: "2" }],
    [{ item_id: itemId, quantity: "3" }],
  );
  await page.reload();

  const listing = listingOf(page);
  await expect(listing.getByText("Item retired")).toHaveCount(2);
  await expect(listing.getByText(`${cable}: 2 m`)).toBeVisible();
  await expect(
    listing.getByRole("button", { name: "Cannot Add to Cart" }).first(),
  ).toBeDisabled();
  await expect(page.getByText(PUT_BACK)).toHaveCount(0);
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

test("a save's package retired since, with none left, is named, never offered, and goes by its line's Discard", async ({
  page,
}) => {
  const cable = await ownCable(page, "Boxed cable");
  const itemId = Number(new URL(page.url()).pathname.split("/").at(-1));
  const box = "Box of 305 m";
  const retire = page.getByRole("button", { name: `Retire ${box}` });
  const packageId = Number(
    ((await page.locator("form", { has: retire }).getAttribute("action")) ?? "")
      .split("/")
      .at(-2),
  );
  await retire.click();
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
  const listing = listingOf(page);
  await expect(listing.getByText(`${cable}: 2 × ${box}`)).toBeVisible();
  const note = listing.locator(".alert", {
    hasText: "This item has been retired.",
  });
  await expect(note.locator("strong")).toHaveText(
    "This item has been retired.",
  );
  await lineDiscard(listing, `${cable}: 2 × ${box}`).click();
  // What is left goes back whole, without a tap; the box is not offered.
  await expect(listing).toHaveCount(0);
  await expect(page.getByText(PUT_BACK)).toBeVisible();
  await expect(page.getByLabel(new RegExp(`^${box}`))).toHaveCount(0);
  expect(await save(page)).toMatchObject({
    lines: [{ item_id: itemId, quantity: "3" }],
  });
});

test("a save an older build's tab saved meanwhile is saved once, under its key; changed after it went back, it is not recorded, says so, and asks for a reload, after which it is listed until dismissed", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = Number(new URL(page.url()).pathname.split("/").at(-1));
  await page.goto("/scan");
  /** One left, which the older build's tab then saves, as its request in
   * flight would. */
  const leaveSaved = async () => {
    await leave(page, [{ item_id: itemId, quantity: "2" }]);
    const [left] = JSON.parse(
      (await page.evaluate(() =>
        localStorage.getItem("inventory.outbox.v3"),
      )) ?? "[]",
    );
    const first = await page.request.post("/api/transactions", {
      data: left.body,
    });
    expect(first.status()).toBe(201);
    await page.reload();
    await expect(page.getByText(PUT_BACK)).toBeVisible();
    return (await first.json()).transaction_id as number;
  };
  const sent = (status: number) =>
    page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/transactions") &&
        response.status() === status,
    );

  const saved = await leaveSaved();
  const replayed = sent(200);
  await page.getByRole("button", { name: "Record" }).click();
  expect(await (await replayed).json()).toEqual({ transaction_id: saved });
  await expect(
    page.getByText(`Recorded (transaction ${saved}).`),
  ).toBeVisible();

  await leaveSaved();
  await page.getByLabel(/^Loose/).fill("3");
  const refused = sent(409);
  await page.getByRole("button", { name: "Record" }).click();
  await refused;
  const said =
    "Not recorded: another tab recorded this cart before your changes.";
  const reloadWords = `${said} Reload the page to continue.`;
  const entry = page.getByRole("listitem").filter({ hasText: said });
  await expect(entry.locator("strong")).toHaveText(reloadWords);
  // Its lines, numbered as a refusal names them, to compare with what was
  // recorded.
  await expect(entry.getByText(`1. ${cable}: 3 m`)).toBeVisible();
  await expect(entry.getByRole("button", { name: "Dismiss" })).toHaveCount(0);
  const reloaded = page.waitForEvent("load");
  await entry.getByRole("button", { name: "Reload" }).click();
  await reloaded;
  // Reloaded, it no longer asks for a reload (Ali, inventory-tng-93z), and
  // says what is left to do (Ali, inventory-tng-wyq, question c).
  const left =
    "Not recorded: another tab recorded this cart before your changes. Scan again only what you changed.";
  const again = page.getByRole("listitem").filter({ hasText: left });
  await expect(again.locator("strong")).toHaveText(left);
  await expect(again.getByText(`1. ${cable}: 3 m`)).toBeVisible();
  await expect(again.getByRole("button", { name: "Reload" })).toHaveCount(0);
  await again.getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText(left)).toHaveCount(0);
});

test("a double tap on a line's Discard discards that line only; one that could not read a line stays, saying so, until its own Discard", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = Number(new URL(page.url()).pathname.split("/").at(-1));
  await page.goto("/scan");
  const open = crypto.randomUUID();
  await leave(page, [
    { item_id: itemId, quantity: "3", open_package_key: open },
    { item_id: itemId, quantity: "5", open_package_key: open },
    // A line no build wrote.
    { quantity: "1" },
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
  // Once the taps are taken again, its last line goes, and it stays.
  await expect(async () => {
    await lineDiscard(listing, `${cable}: 5 m from an open package`).click();
    await expect(listing.getByText(`${cable}: 5 m`)).toHaveCount(0, {
      timeout: 1000,
    });
  }).toPass();
  await expect(
    listing.getByText("Could not read 1 previously added item, scan it again."),
  ).toBeVisible();
  await expect(listing.getByRole("button")).toHaveText(["Discard"]);
  await expect(async () => {
    await listing.getByRole("button", { name: "Discard" }).click();
    await expect(listing.getByText("Confirm discard")).toBeVisible({
      timeout: 1000,
    });
  }).toPass();
  await listing.getByRole("button", { name: "Discard" }).click();
  await expect(listing).toHaveCount(0);
  expect(
    await page.evaluate(() => localStorage.getItem("inventory.outbox.v3")),
  ).toBeNull();
});
