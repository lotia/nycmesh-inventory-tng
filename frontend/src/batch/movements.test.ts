import { describe, expect, it } from "vitest";
import { createCart } from "../cart/cartState";
import { batchBody, KINDS, sideFor, whatIsMissing } from "./movements";

function cartWith(overrides: Partial<ReturnType<typeof createCart>> = {}) {
  return {
    ...createCart("key-1", "2026-08-19T00:00:00Z"),
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
