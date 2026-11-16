/**
 * Units read right after a number, as the server reads them
 * (backend/src/inventory_tng/units.py, docs/decisions/0021, 2026-11-14):
 * the same table, which the server writes to units.json, read the same
 * way, so a search spells "10 µF", "10μF" and "10uf" all "10uF" here as
 * there, and the reading rules the catalogue sends first (units.Rule),
 * as the server does, and the words near a unit and the profile as it
 * does too. units-vectors.json holds what both must make of some text.
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

/** The unit `token` spells, by its symbol, if any: as typed first, then
 * with its prefix as typed and the unit in any case, then all in any case
 * (units.read's order). */
export function read(token: string): string | undefined {
  const found = whole.get(token);
  if (found !== undefined) return found;
  for (const [spelling, [prefix]] of exponents) {
    if (token.startsWith(spelling)) {
      const rest = token.slice(spelling.length);
      const unit = takesPrefixUnits.get(rest.toLowerCase());
      if (unit !== undefined) return prefix + unit.symbol;
    }
  }
  const lower = token.toLowerCase();
  const loose = wholeLoose.get(lower);
  if (loose !== undefined) return loose;
  for (const [spelling, [prefix]] of exponents) {
    if (lower.startsWith(spelling.toLowerCase())) {
      const unit = takesPrefixUnits.get(lower.slice(spelling.length));
      if (unit !== undefined) return prefix + unit.symbol;
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

/** Reading rules, by a unit as typed after a number, case kept
 * (units.rule_key): the unit each reads as, by its symbol, or null, no
 * unit. */
export type Rules = ReadonlyMap<string, string | null>;

// The number a unit follows, ending at its last digit (units._NUMBER).
const NUMBER = /(?:(?<![\p{L}\p{N}])[-−])?[0-9][0-9,]*(?:[.][0-9]+)?$/u;

type Unit = (typeof table.units)[number];
/** A unit as read (units.Reading): its prefix's symbol and power of ten,
 * if any, and the unit; `symbol` as str(Reading) spells it. */
type Reading = { prefix: string; exponent: number; unit: Unit; symbol: string };

const reading = (prefix: string, exponent: number, unit: Unit): Reading => ({
  prefix,
  exponent,
  unit,
  symbol: prefix + unit.symbol,
});
const exponents = new Map(
  table.prefixes.flatMap(({ spellings, exponent }) =>
    spellings.map((spelling): [string, [string, number]] => [
      spelling,
      [spellings[0] ?? "", exponent],
    ]),
  ),
);
// Every symbol and spelling, not words, in lower case (units._SPELT_LOOSE).
const speltLoose = new Map(
  table.units.flatMap((unit) =>
    unit.symbols.map((spelling): [string, Unit] => [
      spelling.toLowerCase(),
      unit,
    ]),
  ),
);
const takesPrefixUnits = new Map(
  table.units.flatMap((unit) =>
    unit.prefixed.map((spelling): [string, Unit] => [
      spelling.toLowerCase(),
      unit,
    ]),
  ),
);
// What a bare rate is a prefix of (units.BARE_RATES).
const bitRate = table.units.find((unit) => unit.symbol === "bit/s") as Unit;
const rank = (each: Reading): number => table.profile.indexOf(each.unit.kind);
const bareRates: Record<string, string> = table.bare_rates;
const clues: Record<string, string[]> = table.clues;
const kinds: Record<string, number[]> = table.kinds;
const products = new Set(table.products);

/** Each unit `token` spells in any case, a bare rate too, and whether it
 * is spelt so as typed: by the profile, those first in each kind
 * (units.readings). */
function readings(token: string): [Reading, boolean][] {
  const lower = token.toLowerCase();
  const found: [Reading, boolean][] = [];
  const unit = speltLoose.get(lower);
  if (unit !== undefined) {
    found.push([reading("", 0, unit), unit.symbols.includes(token)]);
  }
  for (const [spelling, [prefix, exponent]] of exponents) {
    if (lower.startsWith(spelling.toLowerCase())) {
      const rest = token.slice(spelling.length);
      const prefixed = takesPrefixUnits.get(rest.toLowerCase());
      if (prefixed !== undefined) {
        const exact =
          token.startsWith(spelling) && prefixed.symbols.includes(rest);
        found.push([reading(prefix, exponent, prefixed), exact]);
      }
    }
  }
  const rate = bareRates[token];
  if (rate !== undefined) {
    // Every bare rate is a prefix's symbol.
    const [prefix, exponent] = exponents.get(rate) as [string, number];
    found.push([reading(prefix, exponent, bitRate), false]);
  }
  return found.sort(
    ([a, exactA], [b, exactB]) =>
      rank(a) - rank(b) || Number(!exactA) - Number(!exactB),
  );
}

/** Whether `number` of it is a size this inventory may hold
 * (units.Reading.plausible). */
function plausible(each: Reading, number: number): boolean {
  const [low, high] = kinds[each.unit.kind] as [number, number];
  const { factor, offset, power } = each.unit;
  if (factor === null) return low <= number && number <= high;
  const size = number * 10 ** (each.exponent * power) * factor + offset;
  const compared = low > 0 ? Math.abs(size) : size;
  return low <= compared && compared <= high;
}

const WORD = /[A-Za-z0-9+]+(?:-[A-Za-z0-9+]+)*/g;
// A word, as clues have them; a part of one between hyphens, as products.
const PART = /[A-Za-z0-9+]+/g;
const BEFORE = /([A-Za-z0-9+]+)[ -]?$/;

/** The match of `pattern` in `text` holding the character at `at`. */
const holding = (pattern: RegExp, text: string, at: number) =>
  Array.from(text.matchAll(pattern)).find(
    (each) => each.index <= at && at < each.index + each[0].length,
  );

/** Whether the word the digit at `at` is in, or its part between hyphens,
 * alone or joined to the word or part before it, is a product's
 * (units._product). */
function product(text: string, at: number): boolean {
  // The digit is in a word, and a part of one.
  const said = [WORD, PART].flatMap((pattern) => {
    const each = holding(pattern, text, at) as RegExpExecArray;
    const before = BEFORE.exec(text.slice(0, each.index))?.[1] ?? "";
    return [each[0], before + each[0]];
  });
  return said.some((each) => products.has(each.toLowerCase()));
}

/** The reading the words in `text` say, if they say one kind
 * (units._clue). */
function clue(text: string, readable: Reading[]): Reading | undefined {
  const words = new Set(
    Array.from(text.matchAll(PART), ([word]) => word.toLowerCase()),
  );
  const said = readable.map((each): [Reading, number] => [
    each,
    (clues[each.unit.kind] ?? []).filter((word) => words.has(word)).length,
  ]);
  const most = Math.max(...said.map(([, count]) => count));
  const best = said.filter(([, count]) => count === most);
  return most > 0 && best.length === 1 ? best[0]?.[0] : undefined;
}

/** The one plausible reading, else the one of a likely kind, else, none
 * being likely, the one spelt as typed (units._likeliest). */
function likeliest(found: [Reading, boolean][]): Reading | undefined {
  if (found.length === 1) return found[0]?.[0];
  const likely = found.filter(([each]) =>
    table.likely.includes(each.unit.kind),
  );
  if (likely.length === 1) return likely[0]?.[0];
  const exact = found.filter(([, spelt]) => spelt);
  return likely.length === 0 && exact.length === 1 ? exact[0]?.[0] : undefined;
}

/** What the unit after `number` in `text` reads as (units._found): its
 * symbol, null for no unit by its rule, or undefined, nothing settling it. */
function settled(
  text: string,
  number: string,
  token: string,
  rules: Rules | undefined,
): string | null | undefined {
  const rule = rules?.get(token.normalize("NFC"));
  // A rule's reading the table no longer reads is none (units.reading_of).
  if (rule !== undefined)
    return rule !== null && read(rule) === rule ? rule : null;
  const value = Number(number.replaceAll(",", "").replace("−", "-"));
  const found = readings(token).filter(([each]) => plausible(each, value));
  const readable = found.map(([each]) => each);
  const clued = readable.length > 1 ? clue(text, readable) : undefined;
  if (clued !== undefined) return clued.symbol;
  if (table.asked.includes(token) && found.length > 1) return undefined;
  return likeliest(found)?.symbol;
}

// A number grouped in thousands by commas or by spaces, ending at its
// last digit, as search keeps it, before a unit it reads, without them
// (units._GROUPED).
const GROUPED =
  /(?<![\p{L}\p{N}])(?:(?<![0-9][.,])(?!0,)[0-9]{1,3}(?:,[0-9]{3})+|(?<![0-9][., ])[0-9]{1,3}(?: [0-9]{3})+)(?:[.][0-9]+)?$/u;
// A lone nought, then a comma and digits, ending at its last digit, with
// its minus as NUMBER's: the comma is a decimal point to search
// (units._NOUGHT).
const NOUGHT =
  /(?:(?<![\p{L}\p{N}])[-−])?(?<![\p{L}\p{N}])(?<![0-9][.,])0,[0-9]+$/u;

/** Where respell changes a text, and what to (units.Edit). */
type Edit = [start: number, end: number, spelling: string];

/** What respell changes in `text`, in order (units._edits). */
function edits(text: string, rules: Rules | undefined): Edit[] {
  const found: Edit[] = [];
  for (const match of text.matchAll(AFTER_A_NUMBER)) {
    const [whole, digit, token] = match as unknown as [string, string, string];
    const at = match.index;
    const end = at + whole.length;
    if (marked(text, end) || product(text, at)) continue;
    const before = text.slice(0, at + 1);
    const nought = NOUGHT.exec(before);
    let grouped = nought ?? GROUPED.exec(before);
    // `Cat 6 305 m`: a product's number, then another, not thousands.
    if (!nought && grouped && product(text, grouped.index)) grouped = null;
    // It ends at the digit the match starts with, so it is found.
    const number = nought
      ? nought[0].replace(",", ".")
      : grouped
        ? grouped[0].replaceAll(/[, ]/g, "")
        : (NUMBER.exec(before) as RegExpExecArray)[0];
    const reading = settled(text, number, token, rules);
    // Null: no unit by its rule.
    const spelt = reading === undefined ? read(token) : reading;
    if (spelt === null || spelt === undefined) continue;
    found.push(
      grouped ? [grouped.index, end, number + spelt] : [at, end, digit + spelt],
    );
  }
  const alone: Edit[] = [];
  for (const match of text.matchAll(ALONE)) {
    const [whole, token] = match as unknown as [string, string];
    const at = match.index;
    const end = at + whole.length;
    if (!/\P{ASCII}/u.test(token) || marked(text, at - 1, end)) continue;
    if (found.some(([first, last]) => first < end && at < last)) continue;
    const unit = read(token);
    if (unit !== undefined) alone.push([at, end, unit]);
  }
  return [...found, ...alone].sort(([a], [b]) => a - b);
}

/** `text` from `start` to `end` with each of `edits` within made
 * (units._applied). */
function applied(
  text: string,
  made: Edit[],
  start = 0,
  end = text.length,
): string {
  let spelt = "";
  let at = start;
  for (const [first, last, spelling] of made) {
    if (start <= first && last <= end) {
      spelt += text.slice(at, first) + spelling;
      at = last;
    }
  }
  return spelt + text.slice(at, end);
}

/** `text`, decomposed (NFKD), with each unit read right after a number
 * spelt by its symbol, joined to the number, the number without thousands
 * separators, as the forms read it (its reading rule in `rules`, the words
 * near it, the profile), else as the table reads it; a product's name as
 * it is; and each unit alone spelt with a sign no word is (not ASCII) by
 * its symbol (units.respell). */
export function respell(text: string, rules?: Rules): string {
  return applied(text, edits(text, rules));
}

/** A word of a search, between spaces, control characters and U+FEFF
 * (units._SEARCH_WORD). */
const SEARCH_WORD = /[^\s\p{Cc}\ufeff]+/gu;

/** What a search for `text`, decomposed (NFKD), is matched by group by
 * group: each number with the unit respell reads after it, its space kept,
 * and every other word alone, each as typed and respelt, cut from
 * respelling the whole; none past the most matched (units.groups). */
export function groups(
  text: string,
  rules?: Rules,
): [typed: string, respelt: string][] {
  const made = edits(text, rules);
  const spans: [number, number][] = [];
  for (const word of text.matchAll(SEARCH_WORD)) {
    const start = word.index;
    const end = start + word[0].length;
    const last = spans.at(-1);
    // Joined to the word before where respell reads across the space.
    if (last && made.some(([first, to]) => first < last[1] && start < to)) {
      last[1] = end;
    } else {
      spans.push([start, end]);
    }
  }
  if (spans.length > table.groups_max) return [];
  return spans.map(([start, end]) => [
    text.slice(start, end),
    applied(text, made, start, end),
  ]);
}
