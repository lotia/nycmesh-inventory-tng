import { afterEach, describe, expect, it, vi } from "vitest";
import { cartReducer, createCart } from "./cartState";
import { loadCart, STORAGE_KEY, saveCart } from "./cartStorage";

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

/** A cart as this version writes one, so a case can damage exactly one part. */
const whole = cartReducer(createCart("key-1"), {
  type: "add",
  item: { id: 1, name: "Zip Ties Reusable", unitOfMeasure: "each" },
  quantity: 100,
});

describe("loadCart", () => {
  it("restores a saved cart", () => {
    saveCart(whole);

    expect(loadCart()).toEqual(whole);
  });

  it("starts a new cart when nothing is stored", () => {
    expect(loadCart().lines).toEqual([]);
  });

  it("starts a new cart when the stored value is not JSON", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not json");

    expect(loadCart().idempotencyKey).toMatch(/^[0-9a-f]{32}$/);
  });

  it.each([
    ["a cart without a key", { ...whole, idempotencyKey: undefined }],
    ["a cart written before it carried a kind", { ...whole, kind: undefined }],
    ["a volunteer id that is not an id", { ...whole, actorId: "4" }],
    ["a cart whose lines are not a list", { ...whole, lines: "one" }],
    ["a line that is not an object", { ...whole, lines: [null] }],
    ["a line from an older shape", { ...whole, lines: [{ itemId: 1, quantity: 100 }] }],
    ["a value that is not an object", 42],
  ])("starts a new cart when storage holds %s", (_case, stored) => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(stored));

    expect(loadCart().lines).toEqual([]);
  });

  it("starts a new cart when the browser denies storage", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });

    expect(loadCart().lines).toEqual([]);
  });
});

describe("saveCart", () => {
  it("keeps working when the browser refuses to write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });

    expect(() => saveCart(createCart())).not.toThrow();
  });

  it("ignores a stored kind this app cannot express", () => {
    // A transfer is one of the kinds `KINDS` leaves out, for the reason it
    // gives -- but a cart written before that was true, or by hand, would
    // restore, be sent, and be refused with a 409 nothing here renders, under
    // a Retry that cannot succeed.
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ ...createCart("k"), kind: "transfer" }),
    );

    expect(loadCart().kind).toBe("checkout");
  });
});
