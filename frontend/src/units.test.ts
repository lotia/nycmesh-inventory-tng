import { describe, expect, test } from "vitest";
import { bare, bares, search } from "./cart";
import { CATALOGUE, cable } from "./test-catalogue";
import { read, respell } from "./units";
import shared from "./units-vectors.json";

describe("units, as the server reads them", () => {
  test.each(
    shared.vectors as {
      text: string;
      rules?: Record<string, string | null>;
      respelt: string;
      searched?: string;
      typed?: string;
    }[],
  )("$text", ({ text, rules: given, respelt, searched, typed }) => {
    const rules = new Map(Object.entries(given ?? {}));
    expect(respell(text.normalize("NFKD"), rules)).toBe(respelt);
    if (searched !== undefined) expect(bare(text, rules)).toBe(searched);
    if (typed !== undefined) {
      expect(bares(text, rules)).toEqual(
        typed === searched ? [typed] : [typed, searched],
      );
    }
  });

  test("a unit is read in any case, its prefix as typed if it can be", () => {
    expect(read("MHz")).toBe("MHz");
    expect(read("MHZ")).toBe("MHz");
    expect(read("mhz")).toBe("mHz");
    expect(read("Kbps")).toBe("kbit/s");
    expect(read("DAM")).toBe("dam");
    expect(read("Cell")).toBeUndefined();
  });

  test("Greek letters are kept as they are, but in a unit", () => {
    expect(bare("Μέρα μ 10 μF")).toBe("μερα μ 10uf");
    expect(bare("ωμέγα Ω")).toBe("ωμεγα ohm");
  });

  test("a search finds a unit however it is spelt, after a number", () => {
    const named = (name: string) => ({
      ...CATALOGUE,
      // With no folded text from the server, so only the page's own
      // folding finds it.
      items: [{ ...cable, id: 103, name, search_text: "" }],
    });
    const finds = (name: string, typed: string) =>
      search(named(name), typed).map((item) => item.name);
    for (const typed of ["10uF", "10 µF", "10μF", "10 UF"]) {
      expect(finds("Capacitor 10μF", typed), typed).toEqual(["Capacitor 10μF"]);
    }
    for (const typed of ["85 degC", "85°C", "85 Cel", "85℃"]) {
      expect(finds("Sensor 85 °C", typed), typed).toEqual(["Sensor 85 °C"]);
    }
    for (const typed of ["4.7 kΩ", "4.7kohm"]) {
      expect(finds("Resistor 4.7kΩ", typed), typed).toEqual(["Resistor 4.7kΩ"]);
    }
    // Without a number in front, a unit is read only if spelt with a sign
    // no word is: "μF" finds it, "Cel" does not find "85 °C".
    for (const typed of ["μF", "µF", "uF"]) {
      expect(finds("Capacitor 10μF", typed), typed).toEqual(["Capacitor 10μF"]);
    }
    for (const typed of ["°C", "℃"]) {
      expect(finds("Sensor 85 °C", typed), typed).toEqual(["Sensor 85 °C"]);
    }
    expect(finds("Resistor 4.7kΩ", "Ω")).toEqual(["Resistor 4.7kΩ"]);
    expect(finds("Sensor 85 °C", "Cel")).toEqual([]);
  });

  test("the catalogue's reading rules read a unit before the table", () => {
    const ruled = (reading: string | null) => ({
      ...CATALOGUE,
      items: [{ ...cable, id: 104, name: "SFP+ 10G", search_text: "" }],
      reading_rules: [{ token: "10G", reading }],
    });
    const finds = (reading: string | null, typed: string) =>
      search(ruled(reading), typed).map((item) => item.id);
    // Read as gigabits a second, not grams, as the server reads it.
    expect(finds("Gbit/s", "10 Gbps")).toEqual([104]);
    expect(finds("Gbit/s", "10G")).toEqual([104]);
    // No unit: found only as typed.
    expect(finds(null, "10 Gbps")).toEqual([]);
    expect(finds(null, "10G")).toEqual([104]);
  });

  test("what was found before units were read still is (Ali, 2026-11-15)", () => {
    const named = (name: string, aliases: string[] = []) => ({
      ...CATALOGUE,
      items: [{ ...cable, id: 105, name, aliases, search_text: "" }],
    });
    const finds = (name: string, typed: string, aliases?: string[]) =>
      search(named(name, aliases), typed).map((item) => item.id);
    // A unit's word alone, a unit shorter than a token not one, and a unit
    // half typed, as one types on the scan page.
    for (const [name, typed] of [
      ["Cable 100 feet", "feet"],
      ["Cable 100 feet", "100 ft"],
      ["PoE injector 48 VDC", "48 V"],
      ["Wire 2.5 mm² red", "2.5 mm"],
      ["Load 50 Ω/m", "50 Ω"],
      ["Capacitor 10μF", "10 μ"],
      ["Sensor 85 °C", "85 °"],
      ["Cable 100 feet", "100 fe"],
    ]) {
      expect(finds(name ?? "", typed ?? ""), typed).toEqual([105]);
    }
    // An alias too, either way.
    for (const typed of ["feet", "3 ft"]) {
      expect(finds("Patch", typed, ["Patch 3 feet"]), typed).toEqual([105]);
    }
    expect(bares("Cable 100 feet")).toEqual(["cable 100 feet", "cable 100ft"]);
    expect(bares("Spool 305m")).toEqual(["spool 305m"]);
  });

  test("in is no unit, an inch is one by its words", () => {
    expect(read("in")).toBeUndefined();
    expect(respell("2 in 1")).toBe("2 in 1");
    expect(respell("6 inches")).toBe("6[in_i]");
  });

  test("each field is read alone, as the server folds each", () => {
    // Its old code's number, then its category: never "B-12V".
    const catalogue = {
      ...CATALOGUE,
      items: [
        {
          ...cable,
          id: 104,
          name: "Cable",
          old_code: "B-12",
          category: "Volts",
          search_text: "Cable B-12 Volts",
        },
      ],
    };
    expect(search(catalogue, "volts").map((item) => item.id)).toEqual([104]);
  });

  test("a unit beside an accent is part of a word, not read", () => {
    expect(bare("2 gâteaux")).toBe("2 gateaux");
    expect(respell("5 ώρα".normalize("NFKD"))).toBe("5 ώρα".normalize("NFKD"));
    expect(respell("ώρα Ω".normalize("NFKD"))).toBe(
      `${"ώρα".normalize("NFKD")} Ohm`,
    );
  });
});
