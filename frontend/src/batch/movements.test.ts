import { describe, expect, it } from "vitest";
import { createCart } from "../cart/cartState";
import { batchBody, KINDS, sideFor, whatIsIn, whatIsMissing } from "./movements";

function cartWith(overrides: Partial<ReturnType<typeof createCart>> = {}) {
  return {
    ...createCart("key-1"),
    actorId: 7,
    locationId: 3,
    lines: [{ itemId: 1, name: "LiteBeam", unitOfMeasure: "each", quantity: 2, lastScan: null }],
    ...overrides,
  };
}

describe("what a batch is missing", () => {
  it("needs somebody to attribute it to", () => {
    expect(whatIsMissing(cartWith({ actorId: null }))).toMatch(/who you are/i);
  });

  it("needs something in it", () => {
    expect(whatIsMissing(cartWith({ lines: [] }))).toMatch(/nothing in this batch/i);
  });

  it("needs the location the stock is moving from or to", () => {
    expect(whatIsMissing(cartWith({ locationId: null }))).toMatch(/code on the wall/i);
  });

  it("is missing nothing once all three are there", () => {
    expect(whatIsMissing(cartWith())).toBeNull();
  });
});

describe("the request a batch becomes", () => {
  it("sends no occurred_at, because the server's clock decides it", () => {
    // Decision 0018: a client time cannot be validated in the past, and the
    // ledger is append-only, so a stale restored cart would write a wrong row
    // that nothing could fix. Sending nothing is what makes the server decide.
    const body = batchBody(cartWith({ kind: "checkout" }));

    expect(body).not.toHaveProperty("occurred_at");
  });

  it("carries the cart's own idempotency key, so a retry is the same batch", () => {
    expect(batchBody(cartWith()).idempotency_key).toBe("key-1");
  });

  it("puts the location on the side the kind means", () => {
    const out = batchBody(cartWith({ kind: "checkout" })).movements as Record<string, number>[];
    expect(out[0]).toEqual({ item: 1, quantity: 2, from_location: 3 });

    const back = batchBody(cartWith({ kind: "checkin" })).movements as Record<string, number>[];
    expect(back[0]).toEqual({ item: 1, quantity: 2, to_location: 3 });
  });

  it("leaves out a job reference nobody typed", () => {
    expect(batchBody(cartWith())).not.toHaveProperty("job_reference");
    expect(batchBody(cartWith({ jobReference: "NYCM-1" }))).toHaveProperty(
      "job_reference",
      "NYCM-1",
    );
  });

  it("offers only the kinds a scanned batch can actually express", () => {
    // A transfer needs a location on both sides and the cart carries one; an
    // adjustment and a count are not per-line movements at all.
    expect(KINDS.map((k) => k.kind)).toEqual(["checkout", "checkin", "receipt", "consumption"]);
    expect(sideFor("transfer")).toBeNull();
  });
});

describe("what a batch is, in words", () => {
  /** A cart of named lines, otherwise ready to go. */
  function of(...names: string[]) {
    return cartWith({
      lines: names.map((name, index) => ({
        itemId: index + 1,
        name,
        unitOfMeasure: "each",
        quantity: 1,
        lastScan: null,
      })),
    });
  }

  it("names both of two", () => {
    expect(whatIsIn(of("LiteBeam", "Cat6 Outdoor"))).toBe("LiteBeam and Cat6 Outdoor");
  });

  it("counts the rest, because the phone is held in one hand", () => {
    expect(whatIsIn(of("LiteBeam", "Cat6 Outdoor", "Zip Ties", "RJ45"))).toBe(
      "LiteBeam, Cat6 Outdoor and 2 more",
    );
  });
});
