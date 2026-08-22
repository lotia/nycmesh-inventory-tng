import { randomUUID } from "node:crypto";
import { expect, type Page, test } from "@playwright/test";
import { STORAGE_KEY } from "../src/cart/cartStorage";
import { seeded, signIn } from "./sign-in";

/**
 * The one claim the offline queue makes that only a real ledger can settle: a
 * batch whose answer never came back is recorded once, not twice.
 *
 * Both halves are covered without a browser. `src/batch/outbox.test.ts` asserts
 * that a queued batch is replayed with the body it was queued with, and the
 * backend's `test_batch_endpoint.py` asserts that a second arrival under the
 * same `(actor, idempotency_key)` is answered with the first transaction
 * rather than recorded again. What neither can see is the join: the client
 * sending a key the server matches on, through a real session and a real CSRF
 * token, against a ledger that would show the double if it happened.
 *
 * The failure filmed here is the dangerous one, and not the obvious one.
 * Losing the request is harmless -- nothing was written. This loses the
 * *answer*: the server records the batch and the browser is told the request
 * failed, so the client queues a batch the ledger already has. A queue that
 * replayed that as a new write would double every batch saved at the edge of
 * a signal, silently, in an append-only ledger that cannot be corrected.
 *
 * A fresh idempotency key per run, because these tests share a development
 * database: a key left over from the last run would be answered as a replay
 * and the balance would not move, which would pass this test for the worst
 * possible reason.
 */

/** Not 1, so an off-by-one and a doubling look different. */
const QUANTITY = 3;

/** How much of the seeded item the seeded location holds right now. */
async function heldAt(page: Page, item: number, location: number): Promise<number> {
  const balances = await page.evaluate(async (id: number) => {
    const response = await fetch(`/api/items/${id}`);
    const read = (await response.json()) as {
      balances: { location: number; quantity: string }[];
    };
    return read.balances;
  }, item);
  return Number(balances.find((balance) => balance.location === location)?.quantity ?? 0);
}

/** A batch ready to save, put where the app restores one from. */
async function fillTheCart(page: Page, key: string): Promise<void> {
  await page.evaluate(([storageKey, cart]) => window.localStorage.setItem(storageKey, cart), [
    STORAGE_KEY,
    JSON.stringify({
      idempotencyKey: key,
      actorId: Number(seeded("volunteer")),
      // Bringing stock back, so the balance goes up rather than negative:
      // a negative one is recorded with a warning and the warning is not
      // what is being read here.
      kind: "checkin",
      locationId: Number(seeded("location")),
      jobReference: "",
      lines: [
        {
          itemId: Number(seeded("item")),
          name: seeded("item_name"),
          unitOfMeasure: "each",
          quantity: QUANTITY,
          lastScan: null,
        },
      ],
    }),
  ] as const);
}

test("a batch whose answer was lost is recorded once, not twice", async ({ page }) => {
  await signIn(page);
  const item = Number(seeded("item"));
  const location = Number(seeded("location"));
  const before = await heldAt(page, item, location);

  await fillTheCart(page, randomUUID());

  // The server gets the batch; the browser gets a failed request. This is a
  // basement, a walk out of range, or a proxy that hung up -- and from the
  // page there is no way to tell any of them from a request nobody received.
  const batchEndpoint = "**/api/stock/transactions";
  await page.route(batchEndpoint, async (route) => {
    await route.fetch();
    await route.abort();
  });
  await page.reload();
  await page.getByRole("button", { name: "Save", exact: true }).click();

  const outbox = page.getByRole("region", { name: "Batches not yet sent" });
  await expect(outbox).toContainText(seeded("item_name"));
  await expect(outbox).toContainText("waiting to send");

  // Read here, while the queue still says the batch has not been sent: this is
  // what makes the run the dangerous scenario rather than the harmless one.
  // Without it, a test that never lost the answer -- a request the server
  // simply never received -- would pass exactly the same way, and the replay
  // below would be the only write there had ever been.
  expect(await heldAt(page, item, location)).toBe(before + QUANTITY);

  // Everything the volunteer did is on the device now, so the tab can go.
  await page.unroute(batchEndpoint);
  await page.reload();

  await expect(outbox).toContainText("Recorded.");
  expect(await heldAt(page, item, location)).toBe(before + QUANTITY);
});
