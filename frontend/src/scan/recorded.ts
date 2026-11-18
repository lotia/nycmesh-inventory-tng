/**
 * A save refused because another tab recorded its cart first, under the
 * same key, as it was then (outbox.ts, reason recorded_elsewhere): what
 * that tab recorded, as the server said (outbox.Recorded), and what this
 * save holds otherwise, line by line, in words (Ali, inventory-tng-0gg).
 * What it holds more of, or new, goes in an empty cart, under a new key
 * (older.toCart); what was taken from an open package, or said of one,
 * cannot, as the open package may have changed since. What it holds less
 * of was recorded, and only an administrator can correct the ledger
 * (docs/decisions/0003): the server keeps that for one
 * (stock/over_recorded.py), as it refused the save.
 */
import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { v5 as uuidFrom } from "uuid";
import { say, sayParts } from "../i18n";
import {
  type Body,
  batchId,
  type Draft,
  fromOpen,
  type Note,
  REPORTS,
  type Recorded,
  type ReportKind,
  type Said,
} from "../outbox";
import { decimal, thousandths } from "../quantity";
import { type Catalogue, ITEM_SAYS, type Item } from "./cart";
import {
  FROM_OPEN,
  inUnit,
  nameOf,
  PACKAGES_OF,
  RESCAN,
  withRetired,
} from "./older";
import { indexOf } from "./search";

type Line = Body["lines"][number];

/** One line the volunteer changed, as the save holds it otherwise than
 * was recorded: its item, in its form (a package, an open package, moved
 * whole, or loose), of the kind of batch it is from, how much of it each
 * holds, and by how much the volunteer changed what was recorded, in
 * thousandths: more, or, below zero, less. */
export type Difference = {
  line: Line;
  kind: Body["kind"];
  recorded: number;
  mine: number;
  changed: number;
};

/** Under a line the save holds less of than was recorded, in Ali's words
 * (inventory-tng-0gg, 3, and 2026-11-15, 7): `was` recorded, `now` what
 * the save holds, or null if it holds none. */
export function loweredWords(was: Said, now: Said | null): Said {
  return now === null
    ? sayParts(REMOVED_IT, { was })
    : sayParts(CHANGED_IT, { was, now });
}

export const REMOVED_IT = msg`Recorded {was}, you removed it. An administrator will correct it.`;
export const CHANGED_IT = msg`Recorded {was}, you changed it to {now}. An administrator will correct it.`;

/** The button that puts what the save adds in a cart with lines (Ali,
 * inventory-tng-0gg, 2026-11-15, 2). */
export const ADD_DIFFERENCE = msg`Add these to cart`;

/** Beside what the save adds, once it is in the cart (Ali,
 * inventory-tng-0gg, 2026-11-15, 2). */
export const IN_CART = msg`These are in your cart, waiting to be recorded.`;

/** What the save holds otherwise than was recorded, line by line (Ali,
 * inventory-tng-0gg, 1, and 2026-11-15, 7). */
export const REMOVED = msg`{name}: removed`;
export const FEWER = msg`{name}: {amount} fewer`;
export const NEW = msg`{name}: {amount}, new`;
export const MORE = msg`{name}: {amount} more`;
/** A report the save makes that was not recorded. */
export const REPORT_NEW: Record<ReportKind, MessageDescriptor> = {
  used_up: msg`Open package said used up, new`,
  lengths: msg`What is on an open package, new`,
};

/** A line's form, to tell lines of one item apart. */
function formOf(line: Line, kind: Body["kind"]): string {
  const open = line.open_package_key ?? null;
  return JSON.stringify([
    line.item_id,
    line.package_id ?? null,
    // An opening's open package is made from the cart's key, so the same
    // package opened is the same line; anyone else's is taken from.
    open,
    fromOpen(line, kind) && line.whole === true,
  ]);
}

/** The place and kind stock moved by: what else differs moves nothing. */
const movedBy = (body: Body) =>
  JSON.stringify([body.kind, body.from_location_id, body.to_location_id]);

/**
 * Each line the volunteer changed in `mine` since its cart was put back
 * (changed_from), as it differs from `recorded`, by item and form: in
 * mine's order, then those only recorded. What differs otherwise is the
 * other tab's change, not the volunteer's: so with nothing it was changed
 * from (a batch from before another tab's change), none. It took off what
 * it holds less of than both; added what it holds more of than both.
 * Moved by another kind, or between other places, every line differs:
 * none moved as recorded. The server keeps what was taken off so
 * (ledger._more_written).
 */
export function differences(recorded: Body, mine: Body): Difference[] {
  const from = mine.changed_from;
  if (from === undefined || from === null) {
    return [];
  }
  const alike = movedBy(recorded) === movedBy(mine);
  const found = new Map<
    string,
    Omit<Difference, "changed"> & { started: number }
  >();
  const tally = (
    lines: Line[],
    kind: Body["kind"],
    side: "recorded" | "mine" | "started",
  ) => {
    for (const line of lines) {
      const at = alike ? "" : side === "mine" ? "mine" : "recorded";
      const key = `${at} ${formOf(line, kind)}`;
      const each = found.get(key) ?? {
        line,
        kind,
        recorded: 0,
        mine: 0,
        started: 0,
      };
      each[side] += thousandths(String(line.quantity)) ?? 0;
      found.set(key, each);
    }
  };
  tally(mine.lines, mine.kind, "mine");
  tally(recorded.lines, recorded.kind, "recorded");
  // As recorded, where it moved otherwise: as the cart went back.
  tally(from, alike ? mine.kind : recorded.kind, "started");
  return [...found.values()].flatMap(({ started, ...each }) => {
    const less = Math.min(each.recorded, started) - each.mine;
    const more = each.mine - Math.max(each.recorded, started);
    const changed = less > 0 ? -less : Math.max(more, 0);
    return changed === 0 ? [] : [{ ...each, changed }];
  });
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

/** Words for a batch's lines, named from a catalogue. */
class Namer {
  private readonly items: Map<number, Item>;

  constructor(catalogue: Catalogue) {
    this.items = indexOf(withRetired(catalogue)).items;
  }

  /** An item's name, as it is, never in italic. */
  name(line: Line): Said {
    return [{ quoted: nameOf(this.items.get(line.item_id)) }];
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
        : sayParts(PACKAGES_OF, {
            count: pack.n,
            package: { quoted: pack.name },
          });
    return fromOpen(line, kind) ? sayParts(FROM_OPEN, { amount: said }) : said;
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
    return inUnit(amount, this.items.get(line.item_id));
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
    ...batch.lines.map((line) =>
      sayParts(ITEM_SAYS, {
        name: named.name(line),
        what: named.amount(
          line,
          batch.kind,
          thousandths(String(line.quantity)) ?? 0,
        ),
      }),
    ),
    ...reportsOf(batch).map((kind): Said => [say(REPORTS[kind])]),
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
 * (inventory-tng-0gg, 1, and 2026-11-15, 7): "LiteBeam 5AC: 1 more",
 * "Cat6 cable: 20 m, new", "RJ45 connectors: 1 × Bag of 50 fewer", or
 * "RJ45 connectors: removed"; under one taken from an open
 * package, or a report, that it cannot go back (RESCAN); under one it holds
 * less of, that an administrator will correct it.
 */
export function changesShown(
  recorded: Recorded,
  mine: Body,
  catalogue: Catalogue,
): Shown[] {
  const named = new Namer(catalogue);
  const rescan: Note = { bold: [say(RESCAN)] };
  const lines = differences(recorded.batch, mine).map(
    ({ line, kind, recorded: was, mine: now, changed }): Shown => {
      const name = named.name(line);
      if (changed < 0) {
        const removed = now === 0;
        return {
          words: removed
            ? sayParts(REMOVED, { name })
            : sayParts(FEWER, {
                name,
                amount: named.amount(line, kind, -changed),
              }),
          note: {
            bold: loweredWords(
              named.amount(line, kind, was),
              removed ? null : named.changedTo(line, was, now),
            ),
          },
        };
      }
      const words: Said =
        changed === now
          ? sayParts(NEW, { name, amount: named.amount(line, kind, now) })
          : sayParts(MORE, { name, amount: named.amount(line, kind, changed) });
      return { words, note: fromOpen(line, kind) ? rescan : null };
    },
  );
  return [
    ...lines,
    ...reportsAdded(recorded.batch, mine).map(
      (each): Shown => ({ words: [say(REPORT_NEW[each])], note: rescan }),
    ),
  ];
}

/**
 * What `mine` adds to what was recorded, as a batch: each line the
 * volunteer added, or holds more of (differences), by as much, of the same
 * kind, between the same places, by the same volunteer, under a key of its
 * own made from mine's batchId, as one key may name two batches
 * (Outbox.queue): so put in the cart and recorded again it is the same
 * batch, taken once, and each of two under one key has its own; none
 * taken from an open package, nor any report. Null if it adds nothing
 * that can go back.
 */
export function differenceOf(recorded: Body, mine: Body): Body | null {
  const lines = differences(recorded, mine).flatMap(
    ({ line, kind, changed }) =>
      changed > 0 && !fromOpen(line, kind)
        ? [
            {
              item_id: line.item_id,
              quantity: decimal(changed),
              package_id: line.package_id ?? null,
              package_size: line.package_size ?? null,
            },
          ]
        : [],
  );
  if (lines.length === 0) {
    return null;
  }
  const { used_up: _, lengths: __, key, changed_from: ___, ...header } = mine;
  return {
    ...header,
    key: uuidFrom(`difference ${batchId(mine)}`, key),
    lines,
  } satisfies Draft & { key: string };
}
