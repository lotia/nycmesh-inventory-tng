import { afterEach, describe, expect, it, vi } from "vitest";
import { cartReducer, createCart } from "./cartState";
import { loadCart, STORAGE_KEY, saveCart } from "./cartStorage";

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("loadCart", () => {
  it("restores a saved cart", () => {
    const saved = cartReducer(createCart("key-1", "2026-08-19T10:00:00.000Z"), {
      type: "add",
      item: { id: 1, name: "Zip Ties Reusable", unitOfMeasure: "each" },
      quantity: 100,
    });
    saveCart(saved);

    expect(loadCart()).toEqual(saved);
  });

  it("starts a new cart when nothing is stored", () => {
    expect(loadCart().lines).toEqual([]);
  });

  it("starts a new cart when the stored value is not JSON", () => {
    window.localStorage.setItem(STORAGE_KEY, "{not json");

    expect(loadCart().idempotencyKey).toMatch(/^[0-9a-f]{32}$/);
  });

  it.each([
    ["a cart without a key", { lines: [] }],
    ["a cart whose lines are not a list", { idempotencyKey: "k", lines: "one" }],
    ["a line that is not an object", { idempotencyKey: "k", lines: [null] }],
    ["a line from an older shape", { idempotencyKey: "k", lines: [{ itemId: 1 }] }],
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
});
