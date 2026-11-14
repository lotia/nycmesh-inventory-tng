/**
 * What older builds of the scan page left on this browser, under keys of
 * their own (build.ts): saves not sent, and a cart with lines; and saves an
 * older build left under this build's own outbox key, once the server held
 * them back as from a build too old (outbox.ts, state too_old). This build
 * never sends them as they were. It lists each for the volunteer, read only
 * in the parts every version kept: the kind, who, the places, the items
 * and how much of each, and when. The scan page puts one that goes back
 * whole (goesBackWhole) in an empty cart itself; the volunteer adds each
 * other to the cart, to check, fill in what this build needs and save
 * again, or discards it, or a line of it that cannot go back (dropPart).
 * Nothing else is removed, but keys with nothing to lose (tidyOlder).
 *
 * Each version's shape is in the history of outbox.ts and cart.ts. Every
 * batch kept its key, kind, volunteer_id, places, install_number and lines
 * of item_id and quantity (a number in v1, then a decimal), with what,
 * queuedAt, state and note; package_id came in v1, open packages later.
 * Every cart kept its kind, volunteerId, places, installNumber, usedAt and
 * lines of itemId, packages (counts by package id) and loose; from v2, the
 * key of the save an older build left that filled it (batchKey), and each
 * line's sizes, the size each package was counted at.
 */
import { type Signal, signal } from "@preact/signals";
import * as v from "valibot";
import { id } from "./api/replies";
import {
  STORAGE_KEY as CART_KEY,
  type Cart,
  type Catalogue,
  freshCart,
  holdsSome,
  type Item,
  indexOf,
  type Kind,
  type Line,
  reconcile,
} from "./cart";
import {
  BatchKey,
  lineNumbers,
  STORAGE_KEY as OUTBOX_KEY,
  REFUSED_FOR_KEY,
  REPORTS,
} from "./outbox";
import { count, decimal, thousandths } from "./quantity";
import {
  hasStored,
  olderKeys,
  onStoredChange,
  readStored,
  removeStored,
  writeStored,
} from "./stored";

/** The builds up to the one keeping v3 each took over the batches under
 * the key before theirs, and sent them, but left them there as they were:
 * of v1 to v3, only the newest held stands for what was not sent. */
const TOOK_OVER = 3;

const KINDS = ["move", "receipt", "install", "open", "report"] as const;
const maybe = <T extends v.GenericSchema>(schema: T) =>
  v.fallback(v.nullable(schema), null);

/** A batch as any version kept it: what this build reads of it. */
const OldEntry = v.object({
  body: v.object({
    key: v.string(),
    kind: maybe(v.picklist(KINDS)),
    volunteer_id: maybe(id),
    from_location_id: maybe(id),
    to_location_id: maybe(id),
    install_number: maybe(id),
    // Null where the lines themselves cannot be read: how many is unknown.
    lines: maybe(v.array(v.unknown())),
    used_up: maybe(v.array(v.unknown())),
    lengths: maybe(v.array(v.unknown())),
  }),
  state: v.picklist(["waiting", "refused", "too_old"]),
  queuedAt: maybe(v.number()),
  note: maybe(v.string()),
});

const OldLine = v.object({
  item_id: id,
  quantity: v.union([v.number(), v.string()]),
  package_id: maybe(id),
  /** The size the page took that package to be, kept since inventory-tng-1p8
   * (during v3). */
  package_size: maybe(v.union([v.number(), v.string()])),
  open_package_key: maybe(v.string()),
});

/** A cart as any version kept it. */
const OldCart = v.object({
  kind: maybe(v.picklist(["move", "receipt", "install", "open"])),
  volunteerId: maybe(id),
  fromId: maybe(id),
  toId: maybe(id),
  installNumber: v.fallback(v.string(), ""),
  lines: v.pipe(v.array(v.unknown()), v.minLength(1)),
  usedAt: maybe(v.number()),
  batchKey: maybe(v.string()),
});

const OldCartLine = v.object({
  itemId: id,
  packages: v.fallback(v.record(v.string(), v.string()), {}),
  /** The size each package was counted at, kept since v2. */
  sizes: v.fallback(v.record(v.string(), v.string()), {}),
  loose: v.fallback(v.string(), ""),
  open: v.fallback(v.record(v.string(), v.unknown()), {}),
});

/** What an older build left, one save or one cart, as listed. */
export type Older = {
  /** The key it is kept under. */
  key: string;
  /** A save's batch key, unique among saves; a cart's, null. */
  batch: string | null;
  /** The key to save it under again (cart.ts, batchKey), as a tab of an
   * older build may be sending it still: a save's own, or a cart's that a
   * save filled. None for one the inventory refused for its key, which it
   * then holds for another batch and never takes again. */
  batchKey: string | null;
  kind: (typeof KINDS)[number] | null;
  volunteerId: number | null;
  fromId: number | null;
  toId: number | null;
  installNumber: string;
  /** When it was saved, or a cart last changed, in milliseconds since 1970. */
  at: number | null;
  /** Why the inventory refused it; null while it waits. */
  refused: string | null;
  /** Its lines, as the batch or the cart kept them, each read on its own:
   * null for one that cannot be. */
  lines: (OldBatchLine | OldCartLine | null)[];
  /** Whether it held lines that could not be read even as a list, so how
   * many is unknown. */
  linesLost: boolean;
  /** How many open packages it said were used up, and of how many it
   * said what was on them. */
  reports: { usedUp: number; lengths: number };
};

type OldBatchLine = v.InferOutput<typeof OldLine>;
type OldCartLine = v.InferOutput<typeof OldCartLine>;

/** Each line read on its own, so one odd line loses only itself. */
function linesOf<T extends v.GenericSchema>(lines: unknown[], schema: T) {
  return lines.map((line) => {
    const read = v.safeParse(schema, line);
    return read.success ? (read.output as v.InferOutput<T>) : null;
  });
}

/** The entries a stored outbox holds: a list, as v1 to v3 kept them, or
 * {entries}, as v4 does. */
function entriesOf(stored: unknown): unknown[] {
  if (Array.isArray(stored)) {
    return stored;
  }
  return v.is(Entries, stored) ? stored.entries : [];
}

const Entries = v.object({ entries: v.array(v.unknown()) });

/** `stored` holding `entries` in its place, as entriesOf reads them. */
function withEntries(stored: unknown, entries: unknown[]): unknown {
  return Array.isArray(stored) ? entries : { ...(stored as object), entries };
}

/** Whether `entry` is the save of batch key `batch`. */
function isSave(entry: unknown, batch: string | null): boolean {
  const read = v.safeParse(OldEntry, entry);
  return read.success && read.output.body.key === batch;
}

function savesIn(key: string, stored: unknown): Older[] {
  return entriesOf(stored).flatMap((entry) => {
    const read = v.safeParse(OldEntry, entry);
    // This build's own key: only the saves held back as too old. The rest
    // are the outbox's.
    if (
      !read.success ||
      (key === OUTBOX_KEY && read.output.state !== "too_old")
    ) {
      return [];
    }
    return [saveOf(key, read.output)];
  });
}

/** A save kept under `key`, as listed. */
function saveOf(key: string, save: v.InferOutput<typeof OldEntry>): Older {
  const { body, state, queuedAt, note } = save;
  return {
    key,
    batch: body.key,
    batchKey:
      state === "refused" && REFUSED_FOR_KEY.has(note ?? "") ? null : body.key,
    kind: body.kind,
    volunteerId: body.volunteer_id,
    fromId: body.from_location_id,
    toId: body.to_location_id,
    installNumber:
      body.install_number === null ? "" : String(body.install_number),
    at: queuedAt,
    refused: state === "refused" ? (note ?? "") : null,
    lines: linesOf(body.lines ?? [], OldLine),
    linesLost: body.lines === null,
    reports: {
      usedUp: body.used_up?.length ?? 0,
      lengths: body.lengths?.length ?? 0,
    },
  };
}

/**
 * A batch of the outbox (outbox.Entry) the inventory refused, line by
 * line in the words this list uses (toCart's parts), named from
 * `catalogue`, retired items too: for the volunteer to compare with what
 * was recorded. Each item's numbered by its place in the batch, as a
 * 422's "Line n" names it (outbox.ts, problems); a report is not (Ali,
 * inventory-tng-93z).
 */
export function linesSaid(entry: unknown, catalogue: Catalogue): string[] {
  const read = v.safeParse(OldEntry, entry);
  if (!read.success) {
    return [];
  }
  const save = saveOf("", read.output);
  const lineOf = lineNumbers(
    save.lines.flatMap((line) =>
      line !== null && "item_id" in line ? [line.item_id] : [],
    ),
  );
  return toCart(save, withRetired(catalogue)).parts.map(({ itemId, words }) =>
    itemId === null ? words : `${lineOf(itemId)}. ${words}`,
  );
}

function cartIn(key: string, stored: unknown): Older[] {
  const read = v.safeParse(OldCart, stored);
  if (!read.success) {
    return [];
  }
  const cart = read.output;
  return [
    {
      key,
      batch: null,
      batchKey: cart.batchKey,
      kind: cart.kind,
      volunteerId: cart.volunteerId,
      fromId: cart.fromId,
      toId: cart.toId,
      installNumber: cart.installNumber,
      at: cart.usedAt === null || cart.usedAt <= 0 ? null : cart.usedAt,
      refused: null,
      lines: linesOf(cart.lines, OldCartLine),
      linesLost: false,
      reports: { usedUp: 0, lengths: 0 },
    },
  ];
}

/** The outbox's older keys this build reads: of v1 to v3, only the newest
 * held, and every one after; and those the newest took over. */
function outboxKeys(storage: Storage): {
  read: string[];
  superseded: string[];
} {
  const keys = olderKeys(OUTBOX_KEY);
  const newest = keys
    .slice(0, TOOK_OVER)
    .findLastIndex((key) => hasStored(storage, key));
  const from = Math.max(newest, 0);
  return { read: keys.slice(from), superseded: keys.slice(0, from) };
}

/** What older builds left: the saves, then a cart. No build since v3
 * copies another's saves, so each is under one key. */
export function readOlder(storage: Storage = localStorage): Older[] {
  const saves = [...outboxKeys(storage).read, OUTBOX_KEY].flatMap((key) =>
    savesIn(key, readStored(storage, key)),
  );
  const carts = olderKeys(CART_KEY).flatMap((key) =>
    cartIn(key, readStored(storage, key)),
  );
  return [...saves, ...carts];
}

/** The batch keys of every save under `key`, whatever became of each. */
function batchesIn(storage: Storage, key: string): string[] {
  return entriesOf(readStored(storage, key)).flatMap((entry) => {
    const read = v.safeParse(Batched, entry);
    return read.success ? [read.output.body.key] : [];
  });
}

const Batched = v.object({ body: v.object({ key: v.string() }) });

/**
 * Remove, on load, the older keys with nothing to lose: those that hold
 * nothing to list (only saves the inventory has, or a cart with no lines),
 * and those of v1 to v3 whose every save a newer one holds too. A newer
 * one took over a key's saves only while it had none of its own, so a
 * save an older tab kept there afterwards is in no other: that key stays,
 * unlisted, as its other saves were sent from the newer. A key whose text
 * is not even JSON is left, as no build wrote it so.
 */
export function tidyOlder(storage: Storage = localStorage): void {
  const tidy = (
    key: string,
    list: (key: string, stored: unknown) => Older[],
    newer = new Set<string | null>(),
  ) => {
    const stored = readStored(storage, key);
    if (
      stored !== undefined &&
      list(key, stored).every((each) => newer.has(each.batch))
    ) {
      removeStored(storage, key);
    }
  };
  const { read, superseded } = outboxKeys(storage);
  // Only a superseded key's saves can be under a newer key too.
  superseded.forEach((key, at) => {
    const newer = [...superseded.slice(at + 1), ...read];
    tidy(
      key,
      savesIn,
      new Set(newer.flatMap((each) => batchesIn(storage, each))),
    );
  });
  for (const key of read) {
    tidy(key, savesIn);
  }
  for (const key of olderKeys(CART_KEY)) {
    tidy(key, cartIn);
  }
}

/**
 * Remove one: a save from its key, the rest kept as they were, or the
 * cart. An older key left with nothing to list goes; this build's outbox
 * key stays, as the outbox's. False if the browser would
 * not keep the change, or `older` has gone meanwhile (sent by an older
 * tab, or put back in another).
 */
export function removeOlder(
  older: Older,
  storage: Storage = localStorage,
): boolean {
  if (!isListed(older, storage)) {
    return false;
  }
  if (older.batch === null) {
    return removeStored(storage, older.key);
  }
  const stored = readStored(storage, older.key);
  const next = withEntries(
    stored,
    entriesOf(stored).filter((entry) => !isSave(entry, older.batch)),
  );
  if (older.key !== OUTBOX_KEY && savesIn(older.key, next).length === 0) {
    return removeStored(storage, older.key);
  }
  return writeStored(storage, older.key, next);
}

/**
 * Discard a part of `older` (Part.drop): what is left is kept as it was,
 * under its key, so a save keeps its batch key, as one put back in the
 * cart and changed there does (Older.batchKey); one left with nothing to
 * list in `catalogue` goes whole (removeOlder), but one that says lines
 * could not be read stays (nothingListed). False if the browser would
 * not keep the change, or `older` has changed meanwhile.
 */
export function dropPart(
  older: Older,
  drop: readonly Cut[],
  catalogue: Catalogue,
  storage: Storage = localStorage,
): boolean {
  if (!isUnchanged(older, storage)) {
    return false;
  }
  const stored = readStored(storage, older.key);
  let next: unknown;
  if (older.batch === null) {
    const cart = stored as { lines: unknown[] };
    next = { ...cart, lines: cut(cart.lines, drop) };
  } else {
    // A save's reports dropped, by kind, at their places.
    const droppedOf = (kind: "used_up" | "lengths") =>
      new Set(
        drop.flatMap((each) =>
          "report" in each && each.report === kind ? [each.at] : [],
        ),
      );
    const dropped = {
      used_up: droppedOf("used_up"),
      lengths: droppedOf("lengths"),
    };
    const entries = entriesOf(stored).map((entry) => {
      if (!isSave(entry, older.batch)) {
        return entry;
      }
      const { body } = entry as { body: Record<string, unknown> };
      const cutReports = (kind: "used_up" | "lengths") =>
        Array.isArray(body[kind])
          ? {
              [kind]: body[kind].filter((_, at) => !dropped[kind].has(at)),
            }
          : {};
      return {
        ...(entry as object),
        body: {
          ...body,
          // Lines not even a list are lost already: none is listed.
          ...(Array.isArray(body.lines)
            ? { lines: cut(body.lines, drop) }
            : {}),
          ...cutReports("used_up"),
          ...cutReports("lengths"),
        },
      };
    });
    next = withEntries(stored, entries);
  }
  const after = (older.batch === null ? cartIn : savesIn)(older.key, next).find(
    (each) => each.batch === older.batch,
  );
  if (after === undefined || nothingListed(after, catalogue)) {
    return removeOlder(older, storage);
  }
  return writeStored(storage, older.key, next);
}

/** Whether `older` lists nothing more: no part, and no note that lines
 * could not be read, which stays, with its own Discard, until that is
 * tapped (Ali, inventory-tng-0su). */
function nothingListed(older: Older, catalogue: Catalogue): boolean {
  const { lost, parts } = toCart(older, withRetired(catalogue));
  return parts.length === 0 && lost === null;
}

/** `lines` as kept, less what `drop` cuts from them: a line whole, or of a
 * cart's line some counts, its loose or what it said of open packages,
 * the line going too once it holds nothing. Fields it never had are not
 * added, so an older build still reads it. */
function cut(lines: unknown[], drop: readonly Cut[]): unknown[] {
  return lines.flatMap((line, at) => {
    const cuts = drop.filter((each) => !("report" in each) && each.at === at);
    if (cuts.length === 0) {
      return [line];
    }
    const parts = cuts as {
      packages?: string[];
      loose?: true;
      open?: string[];
    }[];
    if (
      parts.some(
        (each) =>
          each.packages === undefined &&
          each.loose === undefined &&
          each.open === undefined,
      )
    ) {
      return [];
    }
    const kept = { ...(line as Record<string, unknown>) };
    const without = (field: "packages" | "open", keys: string[]) => {
      if (typeof kept[field] === "object" && kept[field] !== null) {
        kept[field] = Object.fromEntries(
          Object.entries(kept[field]).filter(([key]) => !keys.includes(key)),
        );
      }
    };
    without(
      "packages",
      parts.flatMap((each) => each.packages ?? []),
    );
    without(
      "open",
      parts.flatMap((each) => each.open ?? []),
    );
    if (parts.some((each) => each.loose) && "loose" in kept) {
      kept.loose = "";
    }
    const read = v.safeParse(OldCartLine, kept);
    const holds =
      read.success &&
      (Object.values(read.output.packages).some(holdsSome) ||
        read.output.loose.trim() !== "" ||
        Object.values(read.output.open).some(saidOfOpen));
    return holds ? [kept] : [];
  });
}

/** Whether `older` is still listed, every part of it as it was read. */
export function isUnchanged(
  older: Older,
  storage: Storage = localStorage,
): boolean {
  const was = JSON.stringify(older);
  return readOlder(storage).some((each) => JSON.stringify(each) === was);
}

/** Whether `older` is still listed as it was read. */
export function isListed(
  older: Older,
  storage: Storage = localStorage,
): boolean {
  return readOlder(storage).some(
    (each) =>
      each.key === older.key &&
      each.batch === older.batch &&
      (older.batch !== null || JSON.stringify(each) === JSON.stringify(older)),
  );
}

/** What putting `older` in the cart leaves to do again, in words: that
 * lines could not be read, then each line left out, as "item: note",
 * each said once. */
export type Gaps = string[];

/** Why something cannot go back, in Ali's words (inventory-tng-3c0,
 * inventory-tng-0su, inventory-tng-93z), shown in the alert's style. Atop
 * the save. */
export const LINES_LOST =
  "Could not read previously added items, scan them again.";
/** Under a save's line of a package resized since. */
export const RESIZED =
  "The package size has changed since this item was added. Discard and re-add the new package size.";
/** Under a sealed count of a package no longer offered: retired, with none
 * sealed left. */
export const ITEM_RETIRED = "This item has been retired.";
/** Under what was taken from, or said of, an open package. */
export const RESCAN = "Can't add these items. Please rescan.";

/** Lines that could not be read, counted. */
export function unreadWords(n: number): string {
  return n === 1
    ? "Could not read 1 previously added item, scan it again."
    : `Could not read ${n} previously added items, scan them again.`;
}

/** What discarding a part drops from what is kept, by where in what was
 * read: a line whole, or of a cart's line the counts of some packages, its
 * loose and what it said of some open packages; or a save's report. */
export type Cut =
  | { at: number; packages?: string[]; loose?: true; open?: string[] }
  | { report: "used_up" | "lengths"; at: number };

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
 * of a package resized since it was saved, or of one the catalogue no
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
            `${nameOf(item)}: ${
              packs === null
                ? `${n} × ${pack.name}`
                : `${decimal(packs * countedAt)}${unitOf(item)}`
            }`,
            RESIZED,
            { at, packages: [packageId] },
          );
        } else if (item === undefined || pack !== undefined) {
          kept[packageId] = n;
        } else if (holdsSome(n)) {
          // Neither shown nor saved by the cart, so said, not kept: named
          // if the catalogue still names it (Item.retired_packages).
          const gone = retiredPackage(item, packageId);
          leave(`${item.name}: ${n} × ${gone?.name ?? GONE}`, ITEM_RETIRED, {
            at,
            packages: [packageId],
          });
        }
      }
      for (const [key, each] of Object.entries(line.open)) {
        if (saidOfOpen(each)) {
          leave(openWords(item, each), RESCAN, { at, open: [key] });
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
      const by = `${decimal(amount)}${unitOf(item)}`;
      const leave = (note: string, what = by) =>
        left.push({
          itemId: line.item_id,
          words: `${nameOf(item)}: ${what}`,
          note,
          drop: [{ at }],
        });
      if (line.open_package_key !== null && older.kind !== "open") {
        leave(RESCAN, `${by}${FROM_OPEN}`);
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
        const size =
          gone === undefined
            ? 0
            : (thousandths(String(line.package_size ?? gone.size)) ?? 0);
        leave(
          ITEM_RETIRED,
          gone !== undefined && size > 0 && amount % size === 0
            ? `${amount / size} × ${gone.name}`
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
        leave(RESIZED);
        return;
      }
      const made = lineOf(line.item_id, { at });
      if (
        pack !== undefined &&
        size !== null &&
        size > 0 &&
        amount % size === 0
      ) {
        // What this loop wrote: a count, or none yet.
        const before = Number(made.packages[pack.id] ?? 0);
        made.packages = {
          ...made.packages,
          [pack.id]: String(before + amount / size),
        };
      } else {
        // What this loop wrote is always a decimal, so never null here.
        /* v8 ignore next */
        made.loose = decimal((thousandths(made.loose) ?? 0) + amount);
      }
    }
  });
  const report = (kind: "used_up" | "lengths", n: number) =>
    Array.from({ length: n }, (_, at) => ({
      itemId: null,
      words: REPORTS[kind],
      note: RESCAN,
      drop: [{ report: kind, at }],
    }));
  left.push(
    ...report("used_up", older.reports.usedUp),
    ...report("lengths", older.reports.lengths),
  );
  const lost = older.linesLost
    ? LINES_LOST
    : unread > 0
      ? unreadWords(unread)
      : null;
  const kind: Kind =
    older.kind === null || older.kind === "report" ? last.kind : older.kind;
  // Choices the catalogue no longer has are cleared, for the cart to ask.
  const cart = reconcile(
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
          : [`${itemId === null ? words : nameOf(named(itemId))}: ${note}`],
      ),
    ]),
  ];
  return { cart, gaps, lost, parts };
}

/** A package, or its item, the catalogue no longer names (Ali,
 * inventory-tng-0su). */
const GONE = "a removed package or item";
/** After an amount taken from an open package. */
const FROM_OPEN = " from an open package";

/** `item`'s package `packageId` no longer offered, if the catalogue still
 * names it (Item.retired_packages). */
function retiredPackage(item: Item | undefined, packageId: string) {
  return item?.retired_packages.find((pack) => String(pack.id) === packageId);
}

/** What an older cart's line said of an open package, in words. */
function openWords(item: Item | undefined, said: unknown): string {
  const { amount, usedUp } = said as Record<string, unknown>;
  const taken = typeof amount === "string" ? (thousandths(amount) ?? 0) : 0;
  const what =
    taken > 0
      ? `${decimal(taken)}${unitOf(item)}${FROM_OPEN}`
      : REPORTS[usedUp === true ? "used_up" : "lengths"];
  return `${nameOf(item)}: ${what}`;
}

/** A cart's line in words: its item, then how many of each package, and
 * how much loose; null if it holds none. */
export function lineWords(line: Line, item: Item | undefined): string | null {
  // An item gone: its packages too (Ali, inventory-tng-93z).
  const packages =
    item?.packages ??
    Object.keys(line.packages).map((id) => ({ id, name: GONE }));
  const parts = packages.flatMap((pack) => {
    const packs = count(line.packages[pack.id] ?? "") ?? 0;
    return packs > 0 ? [`${packs} × ${pack.name}`] : [];
  });
  const loose = thousandths(line.loose) ?? 0;
  if (loose > 0) {
    const after = parts.length > 0 ? " loose" : "";
    parts.push(`${decimal(loose)}${unitOf(item)}${after}`);
  }
  if (parts.length === 0) {
    // An item gone is said, as the cart shows it, even holding none.
    return item === undefined ? nameOf(item) : null;
  }
  return `${nameOf(item)}: ${parts.join(", ")}`;
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

/** Of `listed`, the oldest that goes back whole and `skip` does not hold,
 * by its id (olderId); one of no known time last. */
export function nextWhole(
  listed: readonly Older[],
  catalogue: Catalogue,
  skip: ReadonlySet<string> = new Set(),
): Older | undefined {
  // Sorted first, so only those before the first that goes are read.
  return [...listed]
    .sort((a, b) => timeOf(a) - timeOf(b))
    .find((each) => !skip.has(olderId(each)) && goesBackWhole(each, catalogue));
}

/** When it was saved, or a cart last changed; one of no known time, last. */
const timeOf = (each: Older): number => each.at ?? Infinity;

/** One of what older builds left, among the rest. */
export function olderId(each: Older): string {
  return `${each.key} ${each.batch ?? "cart"}`;
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
function saidOfOpen(said: unknown): boolean {
  if (typeof said !== "object" || said === null) {
    return false;
  }
  const { amount, usedUp, now } = said as Record<string, unknown>;
  const taken = typeof amount === "string" && (thousandths(amount) ?? 1) > 0;
  const told = typeof now === "string" && now.trim() !== "";
  return taken || usedUp === true || told;
}

/** An item's name, or words for one gone from the catalogue. */
function nameOf(item: { name: string } | undefined): string {
  return item?.name ?? "An item no longer in the catalogue";
}

/** An item's unit after an amount: none for things counted. */
function unitOf(item: Item | undefined): string {
  return item === undefined || item.unit === "each" ? "" : ` ${item.unit}`;
}

/** What older builds left, as listed now, kept as other tabs change it. */
export class OlderStore {
  readonly listed: Signal<Older[]>;

  /** Tidies first (tidyOlder): only a page loading makes one. */
  constructor(private readonly storage: Storage = localStorage) {
    tidyOlder(storage);
    this.listed = signal(readOlder(storage));
  }

  /** Read what is listed again. */
  refresh(): void {
    this.listed.value = readOlder(this.storage);
  }

  /** Whether `older` is still listed as it was read. */
  isListed(older: Older): boolean {
    return isListed(older, this.storage);
  }

  /** Remove `older`, and list again. False as removeOlder says. */
  remove(older: Older): boolean {
    const removed = removeOlder(older, this.storage);
    this.refresh();
    return removed;
  }

  /** Whether `older` is still listed, every part as it was read. */
  isUnchanged(older: Older): boolean {
    return isUnchanged(older, this.storage);
  }

  /** Discard a part of `older`, and list again. False as dropPart says. */
  dropPart(older: Older, drop: readonly Cut[], catalogue: Catalogue): boolean {
    const dropped = dropPart(older, drop, catalogue, this.storage);
    this.refresh();
    return dropped;
  }

  /** Follow other tabs' changes to the older keys: an older build's tab
   * still open may send a save meanwhile. Returns what stops it. */
  listen(): () => void {
    return onStoredChange(
      [...olderKeys(OUTBOX_KEY), ...olderKeys(CART_KEY)],
      () => this.refresh(),
    );
  }
}
