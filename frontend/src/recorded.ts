/**
 * A save refused because another tab recorded its cart first, under the
 * same key, as it was then (outbox.ts, reason recorded_elsewhere): what
 * that tab recorded, as the server said (outbox.Recorded), and what this
 * save holds otherwise, line by line, in words (Ali, inventory-tng-0gg).
 * What it holds more of, or new, can go in an empty cart, under a new key
 * (differenceToCart); what was taken from an open package, or said of one,
 * cannot, as the open package may have changed since. What it holds less
 * of was recorded, and only an administrator can correct the ledger
 * (docs/decisions/0003): the server keeps that for one (over_recorded.py),
 * as it refused the save.
 */
import { v4 as uuid } from "uuid";
import { type Cart, type Catalogue, type Item, indexOf } from "./cart";
import { asSave, type Gaps, RESCAN, toCart, withRetired } from "./older";
import {
  type Body,
  type Draft,
  type Note,
  REPORTS,
  type Recorded,
  type ReportKind,
  type Said,
} from "./outbox";
import { decimal, thousandths } from "./quantity";

type Line = Body["lines"][number];

/** One line of the save that differs from what was recorded: its item,
 * in its form (a package, an open package, moved whole, or loose), and how
 * much of it each holds, in thousandths. */
export type Difference = {
  line: Line;
  recorded: number;
  mine: number;
};

/** Under a line the save holds less of than was recorded, in Ali's words
 * (inventory-tng-0gg, 3): `was` recorded, `now` what the save holds. */
export function loweredWords(was: Said, now: Said): Said {
  return [
    "Recorded ",
    ...was,
    ", you changed it to ",
    ...now,
    ". An administrator will correct it.",
  ];
}

/** The heading over what the save holds otherwise (Ali, inventory-tng-0gg,
 * 1). */
export const YOUR_CHANGES = "Your changes, not recorded";

/** The button that puts what the save adds in the cart (Ali,
 * inventory-tng-0gg, 2). */
export const ADD_DIFFERENCE = "Add the difference to cart";

/** A line's form, to tell lines of one item apart. */
function formOf(line: Line, kind: Body["kind"]): string {
  const open = line.open_package_key ?? null;
  return JSON.stringify([
    line.item_id,
    line.package_id ?? null,
    // An opening's open package is made from the cart's key, so the same
    // package opened is the same line; anyone else's is taken from.
    open,
    kind !== "open" && open !== null && line.whole === true,
  ]);
}

/** The place and kind stock moved by: what else differs moves nothing. */
const movedBy = (body: Body) =>
  JSON.stringify([body.kind, body.from_location_id, body.to_location_id]);

/**
 * Each line `mine` holds otherwise than `recorded`, by item and form: in
 * mine's order, then those only recorded. Moved by another kind, or
 * between other places, every line differs: none moved as recorded.
 */
export function differences(recorded: Body, mine: Body): Difference[] {
  const alike = movedBy(recorded) === movedBy(mine);
  const found = new Map<string, Difference>();
  const tally = (body: Body, side: "recorded" | "mine") => {
    for (const line of body.lines) {
      const key = `${alike ? "" : side} ${formOf(line, body.kind)}`;
      const each = found.get(key) ?? { line, recorded: 0, mine: 0 };
      each[side] += thousandths(String(line.quantity)) ?? 0;
      found.set(key, each);
    }
  };
  tally(mine, "mine");
  tally(recorded, "recorded");
  return [...found.values()].filter((each) => each.recorded !== each.mine);
}

/** The reports `mine` makes that `recorded` did not: said of an open
 * package, they never go back. */
export function reportsAdded(recorded: Body, mine: Body): ReportKind[] {
  const usedUp = new Set(recorded.used_up ?? []);
  const lengths = new Set(
    (recorded.lengths ?? []).map((each) => JSON.stringify(each)),
  );
  return [
    ...(mine.used_up ?? [])
      .filter((key) => !usedUp.has(key))
      .map(() => "used_up" as const),
    ...(mine.lengths ?? [])
      .filter((each) => !lengths.has(JSON.stringify(each)))
      .map(() => "lengths" as const),
  ];
}

/** Whether a line of a batch of `kind` was taken from an open package,
 * cut or moved whole: it never goes back in a cart. */
function fromOpen(line: Line, kind: Body["kind"]): boolean {
  return kind !== "open" && (line.open_package_key ?? null) !== null;
}

/** Words for a batch's lines, named from a catalogue. */
class Namer {
  private readonly items: Map<number, Item>;

  constructor(catalogue: Catalogue) {
    this.items = indexOf(withRetired(catalogue)).items;
  }

  /** An item's name, as it is, never in italic. */
  name(line: Line): Said {
    return [
      {
        quoted:
          this.items.get(line.item_id)?.name ??
          "An item no longer in the catalogue",
      },
    ];
  }

  /** The package a line counts, and its size in thousandths, if whole
   * ones of it are `amount`. */
  private packageOf(line: Line, amount: number) {
    const pack = this.items
      .get(line.item_id)
      ?.packages.find((each) => each.id === line.package_id);
    const size = thousandths(String(line.package_size ?? pack?.size ?? ""));
    return pack !== undefined &&
      size !== null &&
      size > 0 &&
      amount % size === 0
      ? { name: pack.name, n: amount / size }
      : null;
  }

  /** `amount` of a line's item: "2 × Bag of 50", or so much in its unit,
   * and, taken from an open package, so. */
  amount(line: Line, kind: Body["kind"], amount: number): Said {
    const pack = this.packageOf(line, amount);
    const said: Said =
      pack === null
        ? [this.inUnit(line, amount)]
        : [`${pack.n} × `, { quoted: pack.name }];
    return fromOpen(line, kind) ? [...said, " from an open package"] : said;
  }

  /** `amount` as the volunteer changed a line to: how many packages, as
   * recorded, else so much in its unit (Ali, inventory-tng-0gg, 3). */
  changedTo(line: Line, recorded: number, amount: number): Said {
    const pack = this.packageOf(line, recorded);
    const size = pack === null ? null : recorded / pack.n;
    return size !== null && amount % size === 0
      ? [String(amount / size)]
      : [this.inUnit(line, amount)];
  }

  private inUnit(line: Line, amount: number): string {
    const unit = this.items.get(line.item_id)?.unit;
    return `${decimal(amount)}${unit === undefined || unit === "each" ? "" : ` ${unit}`}`;
  }
}

/** A line of either list: its words, and a note under it, if any. */
export type Shown = { words: Said; note: Note | null };

/** What was recorded, line by line, then its reports. */
export function recordedLines(
  recorded: Recorded,
  catalogue: Catalogue,
): Said[] {
  const named = new Namer(catalogue);
  const { batch } = recorded;
  return [
    ...batch.lines.map((line) => [
      ...named.name(line),
      ": ",
      ...named.amount(
        line,
        batch.kind,
        thousandths(String(line.quantity)) ?? 0,
      ),
    ]),
    ...reportsOf(batch).map((kind): Said => [REPORTS[kind]]),
  ];
}

function reportsOf(body: Body): ReportKind[] {
  return [
    ...(body.used_up ?? []).map(() => "used_up" as const),
    ...(body.lengths ?? []).map(() => "lengths" as const),
  ];
}

/**
 * What `mine` holds otherwise than was recorded, in Ali's words
 * (inventory-tng-0gg, 1): "LiteBeam 5AC: 1 more", "Cat6 cable: 20 m, new",
 * "RJ45 connectors: 1 × Bag of 50 fewer"; under one taken from an open
 * package, or a report, that it cannot go back (RESCAN); under one it holds
 * less of, that an administrator will correct it.
 */
export function changesShown(
  recorded: Recorded,
  mine: Body,
  catalogue: Catalogue,
): Shown[] {
  const named = new Namer(catalogue);
  const kind = mine.kind;
  const rescan: Note = { bold: [RESCAN] };
  const lines = differences(recorded.batch, mine).map(
    ({ line, recorded: was, mine: now }): Shown => {
      const name = [...named.name(line), ": "];
      if (now < was) {
        return {
          words: [...name, ...named.amount(line, kind, was - now), " fewer"],
          note: {
            bold: loweredWords(
              named.amount(line, kind, was),
              named.changedTo(line, was, now),
            ),
          },
        };
      }
      const words: Said =
        was === 0
          ? [...name, ...named.amount(line, kind, now), ", new"]
          : [...name, ...named.amount(line, kind, now - was), " more"];
      return { words, note: fromOpen(line, kind) ? rescan : null };
    },
  );
  return [
    ...lines,
    ...reportsAdded(recorded.batch, mine).map(
      (each): Shown => ({ words: [REPORTS[each], ", new"], note: rescan }),
    ),
  ];
}

/**
 * What `mine` adds to what was recorded, as a batch: each line it holds
 * more of, or new, by as much, of the same kind, between the same places,
 * by the same volunteer, under a new key; none taken from an open package,
 * nor any report. Null if it adds nothing that can go back.
 */
export function differenceOf(recorded: Body, mine: Body): Body | null {
  const lines = differences(recorded, mine).flatMap(
    ({ line, recorded: was, mine: now }) =>
      now > was && !fromOpen(line, mine.kind)
        ? [
            {
              item_id: line.item_id,
              quantity: decimal(now - was),
              package_id: line.package_id ?? null,
              package_size: line.package_size ?? null,
            },
          ]
        : [],
  );
  if (lines.length === 0) {
    return null;
  }
  const { used_up: _, lengths: __, key: ___, ...header } = mine;
  return { ...header, key: uuid(), lines } satisfies Draft & { key: string };
}

/** `difference` (differenceOf) as a cart to check and record, keeping
 * `last`'s choices where it has none, as a save put back is (older.toCart),
 * under its key; and what it could not hold, in words. */
export function differenceToCart(
  difference: Body,
  catalogue: Catalogue,
  last: Cart,
): { cart: Cart; gaps: Gaps } {
  const { cart, gaps } = toCart(asSave(difference), catalogue, last);
  return { cart, gaps };
}
