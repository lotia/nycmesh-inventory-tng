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

/** The unit each spelling reads as: the first unit spelt so. */
function first(pairs: Pairs): Map<string, string> {
  const found = new Map<string, string>();
  for (const [spelling, symbol] of pairs) {
    if (!found.has(spelling)) found.set(spelling, symbol);
  }
  return found;
}

const lowered = (pairs: Pairs): Pairs =>
  pairs.map(([spelling, symbol]) => [spelling.toLowerCase(), symbol]);

const whole = first([...prefixed, ...alone]);
const wholeLoose = first(lowered([...prefixed, ...alone]));
const takesPrefix = first(prefixed);
const takesPrefixLoose = first(lowered(prefixed));
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
      const unit =
        takesPrefix.get(rest) ?? takesPrefixLoose.get(rest.toLowerCase());
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

// A digit, perhaps a space, then what may be a unit, up to the next letter
// or digit: units.AFTER_A_NUMBER's, character for character.
const AFTER_A_NUMBER =
  /([0-9]) ?([Dd][Ee][Gg] [CFcf]|(?:[A-Za-z°µμΜΩω]|\[[A-Za-z_]+\])+[0-9]?(?:\/[A-Za-z]+)?)(?![\p{L}\p{N}])/gu;

/** `text`, decomposed (NFKD), with each unit read right after a number
 * spelt by its symbol, joined to the number. */
export function respell(text: string): string {
  return text.replace(AFTER_A_NUMBER, (match, digit: string, token: string) => {
    const unit = read(token);
    return unit === undefined ? match : digit + unit;
  });
}
