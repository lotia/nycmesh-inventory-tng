/**
 * What an older build left (older-builds.ts), as a cart to check and save
 * again: what of it the cart can hold, and, line by line in words, what
 * cannot go back and why (toCart); whether it goes back whole, for the
 * scan page to put it in an empty cart itself (goesBackWhole); and a
 * cart's line in words (lineWords).
 */
import { msg } from "@lingui/core/macro";
import * as v from "valibot";
import { say, sayAmount, sayNumber } from "../shared/i18n";
import { BatchKey, fromOpen, REPORTS, type ReportKind } from "../shared/outbox";
import {
  boxAmount,
  count,
  readBox,
  thousandths,
  wholePackages,
} from "../shared/quantity";
import { toBatch } from "./batch";
import {
  type Cart,
  type Catalogue,
  freshCart,
  holdsSome,
  ITEM_SAYS,
  type Item,
  type Kind,
  type Line,
  reconcile,
} from "./cart";
import type { Cut, OldBatchLine, OldCartLine, Older } from "./older-builds";
import { indexOf } from "./search";

/** What putting `older` in the cart leaves to do again, in words: that
 * lines could not be read, then each line left out, as "item: note",
 * each said once. */
export type Gaps = string[];

/** Why something cannot go back, in Ali's words (inventory-tng-3c0,
 * inventory-tng-0su, inventory-tng-93z), shown in the alert's style. Atop
 * the save. */
export const LINES_LOST = msg`Could not read previously added items, scan them again.`;
/** Under a save's line of a package resized since. */
export const RESIZED = msg`The package size has changed since this item was added. Discard and re-add the new package size.`;
/** Under a save's line of an item counted in another unit since. */
export const UNIT_CHANGED = msg`This item's unit has changed since it was added. Discard it and add it again in the new unit.`;
/** Under a sealed count of a package no longer offered: retired, with none
 * sealed left. */
export const ITEM_RETIRED = msg`This item has been retired.`;
/** Under what was taken from, or said of, an open package. */
export const RESCAN = msg`Can't add these items. Please rescan.`;
/** Lines that could not be read, counted. */
export const UNREAD = msg({
  message:
    "{count, plural, one {Could not read # previously added item, scan it again.} other {Could not read # previously added items, scan them again.}}",
});

/** Lines that could not be read, counted. */
export function unreadWords(count: number): string {
  return say(UNREAD, { count });
}

/** Whole packages, counted. */
export const PACKAGES_OF = msg({
  message: "{count} × {package}",
  comment: "How many of a package, the package's name after the sign.",
});
/** After a count of packages, what is loose besides. */
export const LOOSE = msg`{amount} loose`;

/** One line of what an older build left, as listed: an item's lines that
 * go back, or one that cannot, with why. */
type Part = {
  /** Its item; null for a report, which names none. */
  itemId: number | null;
  words: string;
  /** Why it cannot go back; null if it does. */
  note: string | null;
  drop: Cut[];
};

/**
 * `older` as a cart to check and save, keeping `last`'s choices where it
 * has none; what of it the cart cannot hold, in words (Gaps); and it as
 * listed, line by line, each named from `catalogue`, a package no longer
 * offered too where it names it (Item.retired_packages). A batch's package line goes
 * back as packages, if the item still has the package and the amount is
 * whole ones of it; else as loose, for the cart to say what is wrong; one
 * of an item counted in another unit since it was saved, of a package
 * resized since, or of one the catalogue no
 * longer offers, is left to do again, as is an older cart's count of one
 * no longer offered, unless blank or none, or of one counted at a size
 * since changed (v2's sizes). What was taken from an open
 * package, and what was said of one, are left to do again too, as the
 * open packages may have changed. A line left out is not put back as its
 * item's empty line (Ali, inventory-tng-0su), but one that could not be
 * read is, as before. The cart keeps the batch key it is to be sent under
 * (Older.batchKey).
 */
export function toCart(
  older: Older,
  catalogue: Catalogue,
  last: Cart = freshCart(),
): { cart: Cart; gaps: Gaps; lost: string | null; parts: Part[] } {
  const { items } = indexOf(catalogue);
  const retired = new Map(
    catalogue.retired_items.map((each) => [each.id, each]),
  );
  // An item by its id, offered or named only (retired_items).
  const named = (itemId: number) => items.get(itemId) ?? retired.get(itemId);
  const lines = new Map<number, Line>();
  // What each item's line goes back from, to discard it.
  const from = new Map<number, Cut[]>();
  const lineOf = (itemId: number, cut: Cut): Line => {
    const made = lines.get(itemId) ?? {
      itemId,
      packages: {},
      loose: "",
      typed: true,
    };
    lines.set(itemId, made);
    from.set(itemId, [...(from.get(itemId) ?? []), cut]);
    return made;
  };
  const left: (Part & { note: string })[] = [];
  let unread = 0;
  older.lines.forEach((line, at) => {
    if (line === null) {
      unread += 1;
    } else if ("itemId" in line) {
      const item = items.get(line.itemId);
      const before = left.length;
      const leave = (words: string, note: string, cut: Cut) =>
        left.push({ itemId: line.itemId, words, note, drop: [cut] });
      const kept: Record<string, string> = {};
      for (const [packageId, n] of Object.entries(line.packages)) {
        const pack = item?.packages.find(
          (each) => String(each.id) === packageId,
        );
        const size = line.sizes[packageId];
        const countedAt = size === undefined ? null : thousandths(size);
        if (
          pack !== undefined &&
          holdsSome(n) &&
          countedAt !== null &&
          countedAt !== thousandths(pack.size)
        ) {
          // Counted at a size since changed: neither its count of today's
          // packages nor loose says what was moved, as of a save's (row
          // 24), so it is done again.
          const packs = count(n);
          leave(
            say(ITEM_SAYS, {
              name: nameOf(item),
              what:
                packs === null
                  ? say(PACKAGES_OF, { count: n, package: pack.name })
                  : inUnit(packs * countedAt, item?.unit),
            }),
            say(RESIZED),
            { at, packages: [packageId] },
          );
        } else if (item === undefined || pack !== undefined) {
          kept[packageId] = n;
        } else if (holdsSome(n)) {
          // Neither shown nor saved by the cart, so said, not kept: named
          // if the catalogue still names it (Item.retired_packages).
          const gone = retiredPackage(item, packageId);
          leave(
            say(ITEM_SAYS, {
              name: item.name,
              what: say(PACKAGES_OF, {
                count: n,
                package: gone?.name ?? say(GONE),
              }),
            }),
            say(ITEM_RETIRED),
            { at, packages: [packageId] },
          );
        }
      }
      for (const [key, each] of Object.entries(line.open)) {
        if (saidOfOpen(each)) {
          leave(openWords(item, each), say(RESCAN), { at, open: [key] });
        }
      }
      const holds =
        Object.values(kept).some(holdsSome) || line.loose.trim() !== "";
      // All it held left out, it is not put back empty.
      if (holds || left.length === before) {
        const made = lineOf(line.itemId, {
          at,
          packages: Object.keys(kept),
          loose: true,
        });
        made.packages = kept;
        made.loose = line.loose;
      }
    } else {
      const item = items.get(line.item_id);
      const amount = thousandths(String(line.quantity));
      if (amount === null) {
        unread += 1;
        // Its item's line, as before, to count again.
        lineOf(line.item_id, { at });
        return;
      }
      const by = inUnit(amount, line.unit ?? item?.unit);
      const leave = (note: string, what = by) =>
        left.push({
          itemId: line.item_id,
          words: say(ITEM_SAYS, { name: nameOf(item), what }),
          note,
          drop: [{ at }],
        });
      if (fromOpen(line, older.kind)) {
        leave(say(RESCAN), say(FROM_OPEN, { amount: by }));
        return;
      }
      const pack = item?.packages.find((each) => each.id === line.package_id);
      if (
        item !== undefined &&
        line.package_id !== null &&
        pack === undefined
      ) {
        // No longer offered: loose would not say what was moved, sealed,
        // so it is done again. An item gone is named as it is (withRetired);
        // its package, counted as saved, if the catalogue still names it.
        const gone = retiredPackage(item, String(line.package_id));
        const packs =
          gone === undefined
            ? null
            : wholePackages(
                amount,
                thousandths(String(line.package_size ?? gone.size)),
              );
        leave(
          say(ITEM_RETIRED),
          gone !== undefined && packs !== null
            ? say(PACKAGES_OF, {
                count: sayNumber(packs),
                package: gone.name,
              })
            : by,
        );
        return;
      }
      const size = pack === undefined ? null : thousandths(pack.size);
      if (
        line.package_size !== null &&
        size !== null &&
        thousandths(String(line.package_size)) !== size
      ) {
        // Resized since: neither its count of packages nor loose would say
        // what was moved, so it is done again, as the server would ask.
        leave(say(RESIZED));
        return;
      }
      if (line.unit !== null && item !== undefined && line.unit !== item.unit) {
        // Typed in another unit: done again in this one, as the server
        // would ask, never taken as so much of the new (inventory-tng-57u8).
        leave(say(UNIT_CHANGED));
        return;
      }
      const made = lineOf(line.item_id, { at });
      // In the unit it was typed in, which the item still has.
      if (line.unit !== null) {
        made.unit = line.unit;
      }
      const packs = wholePackages(amount, size);
      if (pack !== undefined && packs !== null) {
        // What this loop wrote: a count, or none yet.
        const before = Number(made.packages[pack.id] ?? 0);
        made.packages = {
          ...made.packages,
          [pack.id]: String(before + packs),
        };
      } else {
        // What this loop wrote is always a decimal, so never null here.
        /* v8 ignore next */
        made.loose = boxAmount((readBox(made.loose) ?? 0) + amount);
      }
    }
  });
  const report = (kind: ReportKind, n: number) =>
    Array.from({ length: n }, (_, at) => ({
      itemId: null,
      words: say(REPORTS[kind]),
      note: say(RESCAN),
      drop: [{ report: kind, at }],
    }));
  left.push(
    ...report("used_up", older.reports.usedUp),
    ...report("lengths", older.reports.lengths),
  );
  const lost = older.linesLost
    ? say(LINES_LOST)
    : unread > 0
      ? unreadWords(unread)
      : null;
  const kind: Kind =
    older.kind === null || older.kind === "report" ? last.kind : older.kind;
  // Choices the catalogue no longer has are cleared, for the cart to ask.
  const filled = reconcile(
    {
      ...freshCart(last),
      kind,
      volunteerId: older.volunteerId ?? last.volunteerId,
      fromId: older.fromId,
      toId: older.toId,
      installNumber: older.installNumber,
      lines: [...lines.values()],
      ...(v.is(BatchKey, older.batchKey) ? { batchKey: older.batchKey } : {}),
    },
    catalogue,
  );
  // Under its key, it keeps what it held as it went back: what the
  // volunteer takes off that, once another tab recorded it under the key,
  // is kept for an administrator (inventory-tng-0gg).
  const batch =
    filled.batchKey === undefined ? null : toBatch(filled, catalogue);
  const cart =
    batch === null || "problems" in batch
      ? filled
      : { ...filled, changedFrom: batch.draft.lines };
  const back = cart.lines.flatMap((line) => {
    const words = lineWords(line, items.get(line.itemId));
    return words === null
      ? []
      : [
          {
            itemId: line.itemId,
            words,
            note: null,
            drop: from.get(line.itemId) ?? [],
          },
        ];
  });
  // In the order they were kept, the reports last.
  const first = (part: Part) =>
    part.drop.find((cut) => !("report" in cut))?.at ?? Infinity;
  const parts = [...back, ...left].sort((a, b) => first(a) - first(b));
  // Each note left behind, as "item: problem" (Ali, inventory-tng-0su);
  // a report, which names no item, by what it says. Said once each.
  const gaps: Gaps = [
    ...new Set([
      ...(lost === null ? [] : [lost]),
      ...parts.flatMap(({ itemId, words, note }) =>
        note === null
          ? []
          : [
              say(ITEM_SAYS, {
                name: itemId === null ? words : nameOf(named(itemId)),
                what: note,
              }),
            ],
      ),
    ]),
  ];
  return { cart, gaps, lost, parts };
}

/** A package, or its item, the catalogue no longer names (Ali,
 * inventory-tng-0su). */
const GONE = msg`a removed package or item`;
/** An amount taken from an open package. */
export const FROM_OPEN = msg`{amount} from an open package`;
/** An item gone from the catalogue, in place of its name. */
export const NO_LONGER_IN_CATALOGUE = msg`An item no longer in the catalogue`;

/** `item`'s package `packageId` no longer offered, if the catalogue still
 * names it (Item.retired_packages). */
function retiredPackage(item: Item | undefined, packageId: string) {
  return item?.retired_packages.find((pack) => String(pack.id) === packageId);
}

/** What an older cart's line said of an open package, in words. */
function openWords(item: Item | undefined, said: unknown): string {
  const { amount, usedUp } = said as Record<string, unknown>;
  const taken = typeof amount === "string" ? (readBox(amount) ?? 0) : 0;
  const what =
    taken > 0
      ? say(FROM_OPEN, { amount: inUnit(taken, item?.unit) })
      : say(REPORTS[usedUp === true ? "used_up" : "lengths"]);
  return say(ITEM_SAYS, { name: nameOf(item), what });
}

/** A cart's line in words: its item, then how many of each package, and
 * how much loose; null if it holds none. */
export function lineWords(line: Line, item: Item | undefined): string | null {
  // An item gone: its packages too (Ali, inventory-tng-93z).
  const packages =
    item?.packages ??
    Object.keys(line.packages).map((id) => ({ id, name: say(GONE) }));
  const parts = packages.flatMap((pack) => {
    const packs = count(line.packages[pack.id] ?? "") ?? 0;
    return packs > 0
      ? [say(PACKAGES_OF, { count: sayNumber(packs), package: pack.name })]
      : [];
  });
  const loose = readBox(line.loose) ?? 0;
  if (loose > 0) {
    const amount = inUnit(loose, line.unit ?? item?.unit);
    parts.push(parts.length > 0 ? say(LOOSE, { amount }) : amount);
  }
  if (parts.length === 0) {
    // An item gone is said, as the cart shows it, even holding none.
    return item === undefined ? nameOf(item) : null;
  }
  return say(ITEM_SAYS, { name: nameOf(item), what: parts.join(", ") });
}

/**
 * Whether `older` goes back in the cart whole, for the page to put it
 * there without a tap: something to put back, nothing left out (no line
 * unread, nothing of an open package, no package resized or no longer
 * offered), no item or package retired, gone or not, and no refusal, as
 * each needs the volunteer: a refusal's reason is said only while it is
 * listed, and one retired with some left is taking only.
 */
export function goesBackWhole(older: Older, catalogue: Catalogue): boolean {
  const { cart, gaps } = toCart(older, catalogue);
  const { items } = indexOf(catalogue);
  const current = ([itemId, packageId]: [number, string]) =>
    items
      .get(itemId)
      ?.packages.some(
        (pack) => String(pack.id) === packageId && !pack.retired,
      ) === true;
  return (
    older.refused === null &&
    gaps.length === 0 &&
    cart.lines.length > 0 &&
    cart.lines.every((line) => items.get(line.itemId)?.retired === false) &&
    // An unread line is a gap already.
    older.lines.every(
      (line) => line === null || packagesHeld(line).every(current),
    )
  );
}

/** The packages `line` holds some of, as [item id, package id]. */
function packagesHeld(line: OldBatchLine | OldCartLine): [number, string][] {
  if ("itemId" in line) {
    return Object.entries(line.packages)
      .filter(([, n]) => holdsSome(n))
      .map(([id]) => [line.itemId, id]);
  }
  return line.package_id === null
    ? []
    : [[line.item_id, String(line.package_id)]];
}

/** The catalogue with its retired items no longer offered added, retired,
 * to name them and their packages in what older builds left; never to
 * fill a cart from. Made once per catalogue, so its index (indexOf) is
 * too. */
export function withRetired(catalogue: Catalogue): Catalogue {
  const made = alsoRetired.get(catalogue);
  if (made !== undefined) {
    return made;
  }
  const named: Item[] = catalogue.retired_items.map((item) => ({
    ...item,
    category: "",
    manufacturer: null,
    part_number: null,
    old_code: null,
    search_text: "",
    aliases: [],
    retired: true,
    // All named in packages: none left to name apart.
    retired_packages: [],
    packages: item.packages.map((pack) => ({
      ...pack,
      track_opened: false,
      retired: true,
      short_below: null,
      sealed: [],
    })),
  }));
  const all = { ...catalogue, items: [...catalogue.items, ...named] };
  alsoRetired.set(catalogue, all);
  return all;
}

const alsoRetired = new WeakMap<Catalogue, Catalogue>();

/** Whether a cart's line said anything of an open package: an amount
 * taken, used up, or what is on it. */
export function saidOfOpen(said: unknown): boolean {
  if (typeof said !== "object" || said === null) {
    return false;
  }
  const { amount, usedUp, now } = said as Record<string, unknown>;
  const taken = typeof amount === "string" && (readBox(amount) ?? 1) > 0;
  const told = typeof now === "string" && now.trim() !== "";
  return taken || usedUp === true || told;
}

/** An item's name, or words for one gone from the catalogue. */
export function nameOf(item: { name: string } | undefined): string {
  return item?.name ?? say(NO_LONGER_IN_CATALOGUE);
}

/** `amount`, in thousandths, in `unit`, the one it was typed in, else its
 * item's now: "20 m", or "2" for things counted. */
export function inUnit(
  amount: number,
  unit: string | null | undefined,
): string {
  return `${sayAmount(amount)}${unit == null || unit === "each" ? "" : ` ${unit}`}`;
}
