import { describe, expect, test } from "vitest";
import { CATALOGUE, cable, WITH_CAFE } from "../testing/catalogue";
import { indexOf, search } from "./search";

describe("finding", () => {
  test("search matches every word, anywhere in an item's details", () => {
    expect(search(CATALOGUE, "ubiquiti beam").map((i) => i.id)).toEqual([100]);
    expect(search(CATALOGUE, "CABLES").map((i) => i.id)).toEqual([101]);
    expect(search(CATALOGUE, "gen2").map((i) => i.id)).toEqual([100]);
    expect(search(CATALOGUE, "")).toEqual([]);
    expect(search(CATALOGUE, "radio cable")).toEqual([]);
  });

  test("search ignores accents, but shows the name as spelt", () => {
    const found = (text: string) =>
      search(WITH_CAFE, text).map((item) => item.name);
    for (const typed of [
      "cafe",
      "Café",
      "cafè",
      "CAFÉ",
      "cafe\u0301", // as typed decomposed
      "câbles patch", // the category
      "lodz", // the server's folding spells "ł" too
      "łódź", // as spelt
      "Łódź patch",
    ]) {
      expect(found(typed), typed).toEqual(["Café Łódź patch"]);
    }
    // A letter decomposing leaves alone, as spelt, with the accents left off.
    expect(found("łodz")).toEqual(["Café Łódź patch"]);
    // Each word on its own: one as spelt, one folded.
    expect(found("Câbles Cafe")).toEqual(["Café Łódź patch"]);
    // The category folded by the server, as another is spelt.
    expect(found("cables")).toEqual(["Cat6 cable", "Café Łódź patch"]);
  });

  test("search ignores compatibility forms, as an IME or a paste may give them", () => {
    expect(search(WITH_CAFE, "ｃａｆｅ").map((item) => item.name)).toEqual([
      "Café Łódź patch",
    ]);
    // With no folded text from the server, so only the page's stripping
    // finds it.
    const name = "Ｃａｆé ﬁlter 10µF";
    const odd = {
      ...CATALOGUE,
      items: [{ ...cable, id: 103, name, search_text: "" }],
    };
    for (const typed of ["cafe", "filter", "ﬁlter", "10µf", "10μF"]) {
      expect(
        search(odd, typed).map((item) => item.name),
        typed,
      ).toEqual([name]);
    }
  });

  test("search finds an item by its aliases, as spelt or folded", () => {
    // With no folded text from the server, so only its aliases find it.
    const known = {
      ...CATALOGUE,
      items: [
        {
          ...cable,
          id: 103,
          search_text: "",
          aliases: ["Tough Câble", "Ω-wire"],
        },
      ],
    };
    for (const typed of ["tough cable", "câble", "ω-wire", "Ω"]) {
      expect(
        search(known, typed).map((item) => item.id),
        typed,
      ).toEqual([103]);
    }
    expect(search(known, "tough fibre")).toEqual([]);
  });

  test("a lone accent searches for nothing", () => {
    expect(search(CATALOGUE, "\u0301")).toEqual([]);
    expect(search(CATALOGUE, "beam \u0301").map((i) => i.id)).toEqual([100]);
  });

  test("search looks through the lookups as worked out for the catalogue", () => {
    const known = { ...CATALOGUE, items: [...CATALOGUE.items] };
    expect(search(known, "beam").map((i) => i.id)).toEqual([100]);
    // An item the lookups were worked out without has nothing to find.
    known.items.push({ ...cable, id: 103, name: "LiteBeam spare" });
    expect(search(known, "beam").map((i) => i.id)).toEqual([100]);
  });

  test("a place is named with the places it sits in", () => {
    expect(indexOf(CATALOGUE).places.get(11)).toBe("Hub › Shelf A");
    expect(indexOf(CATALOGUE).places.get(12)).toBe("Van");
  });
});
