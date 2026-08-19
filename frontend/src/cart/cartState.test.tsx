import { describe, expect, it } from "vitest";
import {
  type CartItem,
  type CartState,
  cartReducer,
  createCart,
  mintIdempotencyKey,
  SCAN_DEBOUNCE_MS,
  type ScannedLabel,
} from "./cartState";

const zipTies: CartItem = { id: 1, name: "Zip Ties Reusable", unitOfMeasure: "each" };
const cable: CartItem = { id: 2, name: "Cat6 Outdoor", unitOfMeasure: "metre" };

const packet: ScannedLabel = { code: "7QK3M2XV9A", item: zipTies, quantity: 100 };
const single: ScannedLabel = { code: "ZZZ111ABCD", item: zipTies, quantity: 1 };
const box: ScannedLabel = { code: "4NP8R7T2WQ", item: cable, quantity: 305 };

function cart(): CartState {
  return createCart("key-1", "2026-08-19T10:00:00.000Z");
}

function scan(state: CartState, label: ScannedLabel, at: number, quantity?: number): CartState {
  return cartReducer(state, { type: "scan", label, at, quantity });
}

describe("cartReducer scanning", () => {
  it("spells the scanned quantity out in the item's own unit", () => {
    const state = scan(cart(), packet, 0);

    expect(state.lines).toEqual([
      {
        itemId: 1,
        name: "Zip Ties Reusable",
        unitOfMeasure: "each",
        quantity: 100,
        lastScan: { code: "7QK3M2XV9A", at: 0 },
      },
    ]);
  });

  it("increments the existing line when the same label is scanned again", () => {
    const state = scan(scan(cart(), packet, 0), packet, SCAN_DEBOUNCE_MS);

    expect(state.lines).toHaveLength(1);
    expect(state.lines[0].quantity).toBe(200);
  });

  it("ignores the repeat decodes a camera produces within the debounce window", () => {
    const first = scan(cart(), packet, 1_000);
    const burst = scan(scan(first, packet, 1_100), packet, 1_200);

    expect(burst).toBe(first);
    expect(burst.lines[0].quantity).toBe(100);
  });

  it("counts a different label for the same item inside the window", () => {
    const state = scan(scan(cart(), packet, 0), single, 10);

    expect(state.lines).toHaveLength(1);
    expect(state.lines[0].quantity).toBe(101);
    expect(state.lines[0].lastScan).toEqual({ code: "ZZZ111ABCD", at: 10 });
  });

  it("keeps one line per item and appends a line for a new item", () => {
    const state = scan(scan(cart(), packet, 0), box, 10);

    expect(state.lines.map((line) => line.itemId)).toEqual([1, 2]);
    expect(state.lines[1].quantity).toBe(305);
  });

  it("takes a keypad quantity in place of the label's own", () => {
    const state = scan(cart(), box, 0, 12.5);

    expect(state.lines[0].quantity).toBe(12.5);
  });
});

describe("cartReducer lines", () => {
  it("merges a browsed item into the line a scan created", () => {
    const scanned = scan(cart(), packet, 0);
    const state = cartReducer(scanned, { type: "add", item: zipTies, quantity: 1 });

    expect(state.lines).toHaveLength(1);
    expect(state.lines[0].quantity).toBe(101);
    expect(state.lines[0].lastScan).toEqual({ code: "7QK3M2XV9A", at: 0 });
  });

  it("adds an item that has not been scanned", () => {
    const state = cartReducer(cart(), { type: "add", item: cable, quantity: 3 });

    expect(state.lines[0]).toMatchObject({ itemId: 2, quantity: 3, lastScan: null });
  });

  it("edits one line of a full cart and leaves the rest alone", () => {
    const two = cartReducer(scan(scan(cart(), packet, 0), box, 10), {
      type: "setQuantity",
      itemId: 1,
      quantity: 7,
    });
    const state = scan(two, box, 1_000);

    expect(state.lines).toEqual([
      expect.objectContaining({ itemId: 1, quantity: 7 }),
      expect.objectContaining({ itemId: 2, quantity: 610 }),
    ]);
  });

  it("drops the line when a stepper reaches zero", () => {
    const two = cartReducer(scan(cart(), packet, 0), { type: "add", item: cable, quantity: 3 });
    const state = cartReducer(two, { type: "setQuantity", itemId: 1, quantity: 0 });

    expect(state.lines.map((line) => line.itemId)).toEqual([2]);
  });

  it("removes a line", () => {
    const state = cartReducer(scan(cart(), packet, 0), { type: "remove", itemId: 1 });

    expect(state.lines).toEqual([]);
  });
});

describe("cartReducer batch fields", () => {
  it("mints a fresh idempotency key when the actor changes", () => {
    const state = cartReducer(cart(), { type: "setActor", actorId: 4, idempotencyKey: "key-2" });

    expect(state.actorId).toBe(4);
    expect(state.idempotencyKey).toBe("key-2");
  });

  it("keeps the key when the actor is reselected unchanged", () => {
    const picked = cartReducer(cart(), { type: "setActor", actorId: 4, idempotencyKey: "key-2" });
    const again = cartReducer(picked, { type: "setActor", actorId: 4, idempotencyKey: "key-3" });

    expect(again).toBe(picked);
    expect(again.idempotencyKey).toBe("key-2");
  });

  it("keeps the key across scans, edits and everything else in the cart's life", () => {
    const state = cartReducer(
      cartReducer(scan(cart(), packet, 0), { type: "setKind", kind: "checkin" }),
      { type: "setJobReference", jobReference: "NN217" },
    );

    expect(state.idempotencyKey).toBe("key-1");
    expect(state.kind).toBe("checkin");
    expect(state.jobReference).toBe("NN217");
  });

  it("preselects the location a wall code resolves to", () => {
    const state = cartReducer(cart(), { type: "setLocation", locationId: 9 });

    expect(state.locationId).toBe(9);
  });

  it("clears to an empty cart with a new key, keeping the volunteer", () => {
    const filled = cartReducer(scan(cart(), packet, 0), {
      type: "setActor",
      actorId: 4,
      idempotencyKey: "key-2",
    });
    const state = cartReducer(filled, {
      type: "clear",
      idempotencyKey: "key-9",
      createdAt: "2026-08-19T11:00:00.000Z",
    });

    expect(state).toEqual({
      idempotencyKey: "key-9",
      createdAt: "2026-08-19T11:00:00.000Z",
      actorId: 4,
      kind: "checkout",
      locationId: null,
      jobReference: "",
      lines: [],
    });
  });
});

describe("createCart", () => {
  it("mints its own key and creation time", () => {
    const state = createCart();

    expect(state.idempotencyKey).toMatch(/^[0-9a-f]{32}$/);
    expect(Date.parse(state.createdAt)).not.toBeNaN();
  });

  it("mints a different key every time", () => {
    expect(mintIdempotencyKey()).not.toBe(mintIdempotencyKey());
  });
});
