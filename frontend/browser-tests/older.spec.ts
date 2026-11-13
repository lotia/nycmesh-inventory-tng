/**
 * Saves an older build of the scan page left on a phone (frontend/src/
 * older.ts, inventory-tng-kk1): listed in words, put back in the cart and
 * saved again, on a phone's width. The cable is the test's own (own.ts).
 */
import { expect, test } from "@playwright/test";
import { ownCable } from "./own";

test("a save an older build left is listed, put back in the cart, and saved", async ({
  page,
}) => {
  const cable = await ownCable(page);
  const itemId = Number(new URL(page.url()).pathname.split("/").at(-1));
  await page.goto("/scan");
  const value = (label: string, option: string) =>
    page
      .getByLabel(label, { exact: true })
      .locator("option", { hasText: option })
      .first()
      .getAttribute("value");
  const volunteerId = Number(await value("Your name", "Sam Example"));
  const fromId = Number(await value("From", "Supernode 1 › Shelf A"));
  const toId = Number(await value("To", "Van"));
  // As a build keeping the outbox under v3 left it: waiting, never sent.
  await page.evaluate(
    (save) =>
      localStorage.setItem("inventory.outbox.v3", JSON.stringify([save])),
    {
      body: {
        key: crypto.randomUUID(),
        kind: "move",
        volunteer_id: volunteerId,
        from_location_id: fromId,
        to_location_id: toId,
        lines: [{ item_id: itemId, quantity: "2" }],
      },
      what: "Move: 1 item",
      queuedAt: Date.now(),
      state: "waiting",
      waitingFor: "network",
      note: null,
      transactionId: null,
      failures: 0,
    },
  );
  await page.reload();
  const listing = page.locator("section", {
    has: page.getByRole("heading", {
      name: "Left by an older version of this page",
    }),
  });
  await expect(listing).toContainText(
    "Moving stock from Supernode 1 › Shelf A to Van, by Sam Example",
  );
  await expect(listing.getByText(`${cable}: 2 m`)).toBeVisible();
  // It fits a phone, buttons and all.
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth -
        document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(0);

  await listing.getByRole("button", { name: "Put it in the cart" }).click();
  await expect(listing).toHaveCount(0);
  await expect(page.getByLabel("To", { exact: true })).toHaveValue(
    String(toId),
  );
  const saving = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/transactions") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Save" }).click();
  const response = await saving;
  expect(response.status()).toBe(201);
  expect(response.request().postDataJSON()).toMatchObject({
    kind: "move",
    volunteer_id: volunteerId,
    from_location_id: fromId,
    to_location_id: toId,
    lines: [{ item_id: itemId, quantity: "2" }],
  });
  expect(
    await page.evaluate(() => localStorage.getItem("inventory.outbox.v3")),
  ).toBeNull();
});
