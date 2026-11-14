import { describe, expect, test } from "vitest";
import { bare, search } from "./cart";
import { CATALOGUE, cable } from "./test-catalogue";
import { read, respell } from "./units";
import shared from "./units-vectors.json";

describe("units, as the server reads them", () => {
  test.each(shared.vectors)("$text", ({ text, respelt, searched }) => {
    expect(respell(text.normalize("NFKD"))).toBe(respelt);
    expect(bare(text)).toBe(searched);
  });

  test("a unit is read in any case, its prefix as typed if it can be", () => {
    expect(read("MHz")).toBe("MHz");
    expect(read("MHZ")).toBe("MHz");
    expect(read("mhz")).toBe("mHz");
    expect(read("Kbps")).toBe("kbit/s");
    expect(read("DAM")).toBe("dam");
    expect(read("Cell")).toBeUndefined();
  });

  test("Greek letters are kept as they are, but in a unit after a number", () => {
    expect(bare("Μέρα μ 10 μF")).toBe("μερα μ 10uf");
    expect(bare("ωμέγα Ω")).toBe("ωμεγα ω");
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
    // Without a number in front, nothing is read: μ stays μ.
    expect(finds("Capacitor 10μF", "μF")).toEqual([]);
  });
});
