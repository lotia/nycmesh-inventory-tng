import { expect, test } from "vitest";
import { Repeats } from "./repeated-reads";

test("a label held in view counts once, and again once shown again", () => {
  const repeats = new Repeats(2000);
  expect(repeats.fresh("B3ZT9W", 0)).toBe(true);
  // Read many times a second while held there.
  expect(repeats.fresh("B3ZT9W", 150)).toBe(false);
  expect(repeats.fresh("B3ZT9W", 2100)).toBe(false);
  // Taken away for two seconds, then shown again.
  expect(repeats.fresh("B3ZT9W", 4200)).toBe(true);
  // Another label is a scan of its own at once.
  expect(repeats.fresh("K7QX2M", 4300)).toBe(true);
});
