import { afterEach, describe, expect, test } from "vitest";
import { olderVersions } from "./stored";

afterEach(() => localStorage.clear());

describe("olderVersions", () => {
  test("reads each older version in its place, undefined where none is", () => {
    localStorage.setItem("thing.v1", '"first"');
    localStorage.setItem("thing.v3", "not json");
    expect(olderVersions(localStorage, "thing.v4")).toEqual([
      "first",
      undefined,
      undefined,
    ]);
  });

  test.each(["thing", "thing.v1"])("%s has none older", (key) => {
    expect(olderVersions(localStorage, key)).toEqual([]);
  });
});
