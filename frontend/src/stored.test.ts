import { describe, expect, test } from "vitest";
import { olderKeys } from "./stored";

describe("olderKeys", () => {
  test("names each older version, oldest first", () => {
    expect(olderKeys("thing.v4")).toEqual(["thing.v1", "thing.v2", "thing.v3"]);
  });

  test.each(["thing", "thing.v1"])("%s has none older", (key) => {
    expect(olderKeys(key)).toEqual([]);
  });
});
