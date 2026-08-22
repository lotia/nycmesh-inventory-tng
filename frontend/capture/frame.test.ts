import { describe, expect, it } from "vitest";
import { type Box, endingBefore, union } from "./frame";

const box = (x: number, y: number, width: number, height: number): Box => ({ x, y, width, height });

describe("union", () => {
  it("holds every box it was given", () => {
    expect(union([box(10, 20, 30, 40), box(50, 5, 10, 10)])).toEqual(box(10, 5, 50, 55));
  });

  it("adds the margin on all four sides", () => {
    expect(union([box(10, 10, 10, 10)], 4)).toEqual(box(6, 6, 18, 18));
  });

  it("does not put the margin off the top or the left of the page", () => {
    // Playwright refuses a negative origin, and a control at the very top of
    // the page is the ordinary case: the outbox is drawn there. The other
    // three sides keep the margin they asked for.
    expect(union([box(2, 0, 10, 10)], 8)).toEqual(box(0, 0, 20, 18));
  });

  it("refuses a shot drawn around nothing", () => {
    expect(() => union([])).toThrow(/at least one element/);
  });
});

describe("endingBefore", () => {
  it("stops the box at the edge", () => {
    expect(endingBefore(box(10, 0, 100, 20), 60)).toEqual(box(10, 0, 50, 20));
  });

  it("leaves a box that already ends before the edge alone", () => {
    expect(endingBefore(box(10, 0, 20, 20), 500)).toEqual(box(10, 0, 20, 20));
  });

  it("refuses to crop everything away", () => {
    // The control being cropped out has moved to the left of the thing being
    // photographed, so the crop would produce a picture of nothing.
    expect(() => endingBefore(box(10, 0, 100, 20), 5)).toThrow(/Nothing is left/);
  });
});
