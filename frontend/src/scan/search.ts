/** Searching the catalogue, units and all, and its lookups (indexOf). */

import { groups, type Rules, respell } from "../units";
import type { Catalogue, Item } from "./cart";

/** Lookups worked out once per catalogue, not on every keystroke. */
type Index = {
  items: Map<number, Item>;
  /** Each volunteer's name, by id. */
  volunteers: Map<number, string>;
  /** Each place's name with the places it sits in, by id. */
  places: Map<number, string>;
  /** Every place, sorted by that name. */
  placeList: { id: number; name: string }[];
  /** What search looks through, per item, bare: its details as spelt, then
   * the server's folded text. */
  haystacks: Map<number, string>;
  /** The reading rules, by a number and its unit as typed (units.Rules). */
  rules: Rules;
  /** Label codes and items' old codes, upper case, to what they name. */
  codes: Map<string, Named>;
};

/** What a label or an old code names: an item, a package of one, a place. */
export type Named =
  | { kind: "item"; itemId: number; packageId?: number }
  | { kind: "place"; locationId: number };

const indexes = new WeakMap<Catalogue, Index>();

export function indexOf(catalogue: Catalogue): Index {
  const cached = indexes.get(catalogue);
  if (cached !== undefined) {
    return cached;
  }
  const byId = new Map(catalogue.locations.map((x) => [x.id, x]));
  const places = new Map<number, string>();
  for (const location of catalogue.locations) {
    const names: string[] = [];
    for (
      let at: typeof location | undefined = location;
      at !== undefined && names.length < 10;
      at = at.parent_id === null ? undefined : byId.get(at.parent_id)
    ) {
      names.unshift(at.name);
    }
    places.set(location.id, names.join(" › "));
  }
  const rules: Rules = new Map(
    catalogue.reading_rules.map((rule) => [rule.token, rule.reading]),
  );
  // Labels after old codes, so a label wins if the two ever clash.
  const codes: Index["codes"] = new Map();
  for (const item of catalogue.items) {
    if (item.old_code !== null) {
      codes.set(item.old_code.toUpperCase(), { kind: "item", itemId: item.id });
    }
  }
  for (const label of catalogue.labels) {
    const code = label.code.toUpperCase();
    if (label.location_id !== null) {
      codes.set(code, { kind: "place", locationId: label.location_id });
    } else if (label.item_id !== null) {
      codes.set(
        code,
        label.package_id === null
          ? { kind: "item", itemId: label.item_id }
          : {
              kind: "item",
              itemId: label.item_id,
              packageId: label.package_id,
            },
      );
    }
  }
  const index: Index = {
    items: new Map(catalogue.items.map((item) => [item.id, item])),
    volunteers: new Map(
      catalogue.volunteers.map((each) => [each.id, each.name]),
    ),
    places,
    placeList: [...places]
      .map(([placeId, name]) => ({ id: placeId, name }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    haystacks: new Map(
      catalogue.items.map((item) => [
        item.id,
        // Each field bare alone, as typed and respelt, as the server keeps
        // each (models.names.held), so no field's number reads the next's
        // start as a unit; the server's text, kept so already, only in lower
        // case.
        [
          ...[
            item.name,
            item.manufacturer,
            item.part_number,
            item.old_code,
            item.category,
            ...item.aliases,
          ].flatMap((field) => bares(field ?? "", rules)),
          item.search_text.toLowerCase(),
        ]
          .filter(Boolean)
          .join(" "),
      ]),
    ),
    codes,
    rules,
  };
  indexes.set(catalogue, index);
  return index;
}

/** Items whose name, maker, part number, code or category holds every word
 * of what was typed, as typed or respelt (`bares`), or every group of it,
 * each as typed or respelt (units `groups`: `100 feet 48V` finds `Cable
 * 100ft 48 VDC`): in the details as spelt or the server's folded text
 * (docs/decisions/0021, 2026-11-12, and Ali, 2026-11-15). */
export function search(catalogue: Catalogue, text: string, limit = 20): Item[] {
  const { haystacks, rules } = indexOf(catalogue);
  const forms = bares(text, rules)
    .map((form) =>
      // A lone accent is no word: not a search matching everything.
      form.split(/\s+/).filter((word) => word !== ""),
    )
    .filter((words) => words.length > 0);
  if (forms.length === 0) {
    return [];
  }
  const grouped = groups(text.normalize("NFKD"), rules)
    .map((group) => [...new Set(group.map(unmarked))].filter(Boolean))
    .filter((group) => group.length > 0);
  return catalogue.items
    .filter((item) => {
      const haystack = haystacks.get(item.id) ?? "";
      const holds = (form: string) => haystack.includes(form);
      return (
        forms.some((words) => words.every(holds)) ||
        (grouped.length > 1 && grouped.every((group) => group.some(holds)))
      );
    })
    .slice(0, limit);
}

/** `text` bare as typed, its units as they are, then bare with each unit
 * right after a number spelt as the server spells it (`respell`) if that
 * differs, as the server keeps and matches it (models.names.folds): so "feet",
 * "48 v" in "48 vdc" and a half-typed "10 μ" find what they did before
 * units were read; `rules`, the reading rules, read first. Bare is in
 * lower case, decomposed by compatibility, and without combining marks:
 * "é" is "e", fullwidth "ｃａｆｅ" is "cafe", "ﬁ" is "fi" and, respelt,
 * "10 µF" is "10uf"; "ł", "ø", "æ" and "ß", which do not decompose, stay
 * as they are, and so do Greek letters, but in units. */
export function bares(text: string, rules?: Rules): string[] {
  const decomposed = text.normalize("NFKD");
  const typed = unmarked(decomposed);
  const respelling = respell(decomposed, rules);
  if (respelling === decomposed) return [typed];
  const respelt = unmarked(respelling);
  return respelt === typed ? [typed] : [typed, respelt];
}

const unmarked = (decomposed: string): string =>
  decomposed.replace(/\p{M}/gu, "").toLowerCase();
