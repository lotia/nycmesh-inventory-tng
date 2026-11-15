/**
 * Units read right after a number, as the server reads them
 * (backend/src/inventory_tng/units.py, docs/decisions/0021, 2026-11-14):
 * the same table, which the server writes to units.json, read the same
 * way, so a search spells "10 µF", "10μF" and "10uf" all "10uF" here as
 * there. units-vectors.json holds what both must make of some text.
 */
import table from "./units.json";

type Pairs = [spelling: string, symbol: string][];

const prefixed: Pairs = table.units.flatMap((unit) =>
  unit.prefixed.map((spelling): [string, string] => [spelling, unit.symbol]),
);
const alone: Pairs = table.units.flatMap((unit) =>
  unit.alone.map((spelling): [string, string] => [spelling, unit.symbol]),
);

/** The unit each spelling reads as in any case (units._lowered): one, as
 * no two units share a spelling in any case. */
const lowered = (pairs: Pairs): Map<string, string> =>
  new Map(pairs.map(([spelling, symbol]) => [spelling.toLowerCase(), symbol]));

const whole = new Map([...prefixed, ...alone]);
const wholeLoose = lowered([...prefixed, ...alone]);
const takesPrefixLoose = lowered(prefixed);
const prefixSpellings = table.prefixes.flatMap(([symbol = "", ...others]) =>
  [symbol, ...others].map((spelling): [string, string] => [spelling, symbol]),
);

/** The unit `token` spells, by its symbol, if any: as typed first, then
 * with its prefix as typed and the unit in any case, then all in any case
 * (units.read's order). */
export function read(token: string): string | undefined {
  const found = whole.get(token);
  if (found !== undefined) return found;
  for (const [spelling, prefix] of prefixSpellings) {
    if (token.startsWith(spelling)) {
      const rest = token.slice(spelling.length);
      const unit = takesPrefixLoose.get(rest.toLowerCase());
      if (unit !== undefined) return prefix + unit;
    }
  }
  const lower = token.toLowerCase();
  const loose = wholeLoose.get(lower);
  if (loose !== undefined) return loose;
  for (const [spelling, prefix] of prefixSpellings) {
    if (lower.startsWith(spelling.toLowerCase())) {
      const unit = takesPrefixLoose.get(lower.slice(spelling.length));
      if (unit !== undefined) return prefix + unit;
    }
  }
  return undefined;
}

// What may be a unit, and that it ends where a letter or digit does not
// follow: units._TOKEN and units._ENDS, character for character.
const TOKEN = String.raw`(?:[A-Za-z°µμΜΩω]|\[[A-Za-z_]+\])+[0-9]?(?:\/[A-Za-z]+)?`;
const ENDS = String.raw`(?![\p{L}\p{N}])`;
// A digit, perhaps a space, then what may be a unit.
const AFTER_A_NUMBER = new RegExp(
  `([0-9]) ?([Dd][Ee][Gg] [CFcf]|${TOKEN})${ENDS}`,
  "gu",
);
// What may be a unit with no number before it: read only if spelt with a
// sign no word is (units.ALONE).
const ALONE = new RegExp(String.raw`(?<![\p{L}\p{N}])(${TOKEN})${ENDS}`, "gu");

/** Whether a combining mark is at any of `at` in `text` (units._marked):
 * decomposed, a letter's accent is one, so a token beside it is part of a
 * word, as ω in ώρα is. */
const marked = (text: string, ...at: number[]): boolean =>
  at.some((i) => /\p{M}/u.test(text.charAt(i)));

/** `text`, decomposed (NFKD), with each unit read right after a number
 * spelt by its symbol, joined to the number, and each unit alone spelt with
 * a sign no word is (not ASCII) by its symbol. */
export function respell(text: string): string {
  return text
    .replace(
      AFTER_A_NUMBER,
      (match, digit: string, token: string, at: number, whole: string) => {
        if (marked(whole, at + match.length)) return match;
        const unit = read(token);
        return unit === undefined ? match : digit + unit;
      },
    )
    .replace(ALONE, (match, token: string, at: number, whole: string) => {
      if (
        !/\P{ASCII}/u.test(token) ||
        marked(whole, at - 1, at + match.length)
      ) {
        return match;
      }
      return read(token) ?? match;
    });
}
