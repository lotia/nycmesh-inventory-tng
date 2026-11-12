import { describe, expect, test } from "vitest";
import { afterCut, cutOf, usedUpOf } from "./cart";
import { endingNote, packageWord } from "./OpenPackageCard";

describe("an open package's card", () => {
  const open = { left: 120_000, short_below: "15" };

  test("what a cut leaves, and why nothing usable would be", () => {
    expect(afterCut(open, 20_000)).toEqual({ after: 100_000, ending: null });
    expect(afterCut(open, 110_000)).toEqual({ after: 10_000, ending: "short" });
    expect(afterCut(open, 120_000)).toEqual({ after: 0, ending: "used" });
    expect(afterCut(open, 121_000)).toEqual({ after: -1000, ending: "over" });
    expect(afterCut({ ...open, short_below: null }, 110_000).ending).toBe(null);
  });

  test("used up follows the cut until the volunteer says", () => {
    const all = { amount: "120", whole: false };
    expect(usedUpOf(open, all)).toBe(true);
    expect(usedUpOf(open, { ...all, usedUp: false })).toBe(false);
    // What it shows grew: the cut no longer ends it.
    expect(usedUpOf({ ...open, left: 200_000 }, all)).toBe(false);
    // Nothing cut, not yet a number, or all of it moved: no cut.
    for (const taken of [
      { amount: "0", whole: false },
      { amount: "120.", whole: false },
      { amount: "120", whole: true },
      { amount: "", whole: false },
    ]) {
      expect(cutOf(open, taken)).toBe(null);
    }
  });

  test("a package's word keeps a name of its own", () => {
    const word = (name: string) => packageWord({ package_name: name });
    expect(word("Spool")).toBe("spool");
    expect(word("Box of 305 m")).toBe("box of 305 m");
    expect(word("UniFi Box")).toBe("UniFi Box");
    expect(word("RJ45 bag")).toBe("RJ45 bag");
  });

  test("things counted each are not cable", () => {
    expect(endingNote("used", "bag", "each")).toBe(
      "Your cut uses up the bag, so it's ticked. Untick it if any is left. An administrator confirms.",
    );
  });
});
