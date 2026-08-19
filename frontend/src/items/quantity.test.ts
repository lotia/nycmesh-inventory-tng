import { describe, expect, it } from "vitest";
import { describeQuantity, formatQuantity, toNumber } from "./quantity";

describe("toNumber", () => {
  it("reads the decimal strings the API sends", () => {
    expect(toNumber("100.000")).toBe(100);
  });

  it("reads something that is not a number as none of it", () => {
    expect(toNumber("")).toBe(0);
  });
});

describe("formatQuantity", () => {
  it("drops the trailing zeros a decimal column carries", () => {
    expect(formatQuantity(12)).toBe("12");
  });

  it("keeps a fraction, because cable is not counted in whole metres", () => {
    expect(formatQuantity(1.5)).toBe("1.5");
  });
});

describe("describeQuantity", () => {
  it("spells the quantity out in the item's own unit", () => {
    expect(describeQuantity(3, "each", [])).toBe("3 each");
  });

  it("says a packet when the quantity is a whole number of one", () => {
    expect(describeQuantity(100, "each", [100])).toBe("100 each (1 packet of 100)");
  });

  it("counts the packets", () => {
    expect(describeQuantity(200, "each", [100])).toBe("200 each (2 packets of 100)");
  });

  it("says nothing about packets for a quantity that is not a whole number of one", () => {
    expect(describeQuantity(150, "each", [100])).toBe("150 each");
  });

  it("prefers the largest packet the quantity is a whole number of", () => {
    expect(describeQuantity(100, "each", [10, 100])).toBe("100 each (1 packet of 100)");
  });

  it("does not call a label meaning one a packet", () => {
    expect(describeQuantity(2, "each", [1])).toBe("2 each");
  });

  it("pluralises a unit that has a plural", () => {
    expect(describeQuantity(2, "metre", [])).toBe("2 metres");
    expect(describeQuantity(1, "metre", [])).toBe("1 metre");
    expect(describeQuantity(2, "foot", [])).toBe("2 feet");
  });

  it("shows a unit it has never heard of rather than swallowing it", () => {
    expect(describeQuantity(2, "furlong", [])).toBe("2 furlong");
  });
});
