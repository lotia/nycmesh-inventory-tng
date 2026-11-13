import { describe, expect, test } from "vitest";
import { hasStored, olderKeys, removeStored } from "./stored";

describe("olderKeys", () => {
  test("names each older version, oldest first", () => {
    expect(olderKeys("thing.v4")).toEqual(["thing.v1", "thing.v2", "thing.v3"]);
  });

  test.each(["thing", "thing.v1"])("%s has none older", (key) => {
    expect(olderKeys(key)).toEqual([]);
  });
});

describe("a browser that refuses", () => {
  const refusing = {
    getItem: () => {
      throw new DOMException("refused", "SecurityError");
    },
    removeItem: () => {
      throw new DOMException("refused", "SecurityError");
    },
  } as unknown as Storage;

  test("to say what it holds holds nothing, as far as the pages know", () => {
    localStorage.setItem("thing", "not even JSON");
    expect(hasStored(localStorage, "thing")).toBe(true);
    expect(hasStored(localStorage, "other")).toBe(false);
    expect(hasStored(refusing, "thing")).toBe(false);
  });

  test("to forget says so", () => {
    localStorage.setItem("thing", "1");
    expect(removeStored(refusing, "thing")).toBe(false);
    expect(removeStored(localStorage, "thing")).toBe(true);
    expect(localStorage.getItem("thing")).toBeNull();
  });
});
