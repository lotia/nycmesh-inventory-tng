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
 * again, or discards it. Nothing else is removed, but keys with nothing to
 * lose (tidyOlder).
 *
 * Each version's shape is in the history of outbox.ts and cart.ts. Every
 * batch kept its key, kind, volunteer_id, places, install_number and lines
 * of item_id and quantity (a number in v1, then a decimal), with what,
 * queuedAt, state and note; package_id came in v1, open packages later.
 * Every cart kept its kind, volunteerId, places, installNumber, usedAt and
 * lines of itemId, packages (counts by package id) and loose.
 */
import { type Signal, signal } from "@preact/signals";
import * as v from "valibot";
import { id } from "./api/replies";
import {
  STORAGE_KEY as CART_KEY,
  type Cart,
  type Catalogue,
  freshCart,
  type Item,
  indexOf,
  type Kind,
  type Line,
  reconcile,
} from "./cart";
import { STORAGE_KEY as OUTBOX_KEY } from "./outbox";
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
});

const OldCartLine = v.object({
  itemId: id,
  packages: v.fallback(v.record(v.string(), v.string()), {}),
  loose: v.fallback(v.string(), ""),
  open: v.fallback(v.record(v.string(), v.unknown()), {}),
});

/** What an older build left, one save or one cart, as listed. */
export type Older = {
  /** The key it is kept under. */
  key: string;
  /** A save's batch key, unique among saves; a cart's, null. */
  batch: string | null;
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
  /** Open packages it said were used up, or what was on them. */
  reports: number;
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

function savesIn(key: string, stored: unknown): Older[] {
  return entriesOf(stored).flatMap((entry) => {
    const read = v.safeParse(OldEntry, entry);
    if (!read.success) {
      return [];
    }
    const { body, state, queuedAt, note } = read.output;
    // This build's own key: only the saves held back as too old. The rest
    // are the outbox's.
    if (key === OUTBOX_KEY && state !== "too_old") {
      return [];
    }
    return [
      {
        key,
        batch: body.key,
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
        reports: (body.used_up?.length ?? 0) + (body.lengths?.length ?? 0),
      },
    ];
  });
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
      kind: cart.kind,
      volunteerId: cart.volunteerId,
      fromId: cart.fromId,
      toId: cart.toId,
      installNumber: cart.installNumber,
      at: cart.usedAt === null || cart.usedAt <= 0 ? null : cart.usedAt,
      refused: null,
      lines: linesOf(cart.lines, OldCartLine),
      linesLost: false,
      reports: 0,
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
  const left = entriesOf(stored).filter((entry) => {
    const read = v.safeParse(OldEntry, entry);
    return !read.success || read.output.body.key !== older.batch;
  });
  const next = Array.isArray(stored)
    ? left
    : { ...(stored as object), entries: left };
  if (older.key !== OUTBOX_KEY && savesIn(older.key, next).length === 0) {
    return removeStored(storage, older.key);
  }
  return writeStored(storage, older.key, next);
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

/** What putting `older` in the cart leaves to do again, in words: each
 * said once for the whole save, not for each line. */
export type Gaps = string[];

/** In Ali's words (inventory-tng-3c0), each said once for a save. */
export const LINES_LOST =
  "Sorry. Could not read previously added items, scan them again.";
export const RESIZED =
  "Sorry. The package size has changed since this item was added. Discard and re-add the new package size.";

/** Lines that could not be read, counted. */
export function unreadWords(n: number): string {
  return n === 1
    ? "Sorry. Could not read 1 previously added item, scan it again."
    : `Sorry. Could not read ${n} previously added items, scan them again.`;
}

/** What was taken from, or said of, open packages, counted. */
export function openWords(n: number): string {
  return n === 1
    ? "Sorry. Could not read 1 open item, scan it in again."
    : `Sorry. Could not read ${n} open items, scan them in again.`;
}

/**
 * `older` as a cart to check and save, keeping `last`'s choices where it
 * has none; and what of it the cart cannot hold, in words. A batch's
 * package line goes back as packages, if the item still has the package
 * and the amount is whole ones of it; else as loose, for the cart to say
 * what is wrong; one of a package resized since it was saved is left to
 * do again. What was taken from an open package, and what was said of
 * one, are left to do again too, as the open packages may have changed.
 */
export function toCart(
  older: Older,
  catalogue: Catalogue,
  last: Cart = freshCart(),
): { cart: Cart; gaps: Gaps } {
  const { items } = indexOf(catalogue);
  const lines = new Map<number, Line>();
  const lineOf = (itemId: number): Line => {
    const made = lines.get(itemId) ?? {
      itemId,
      packages: {},
      loose: "",
      typed: true,
    };
    lines.set(itemId, made);
    return made;
  };
  let unread = 0;
  let open = older.reports;
  let resized = false;
  for (const line of older.lines) {
    if (line === null) {
      unread += 1;
    } else if ("itemId" in line) {
      const made = lineOf(line.itemId);
      made.packages = { ...line.packages };
      made.loose = line.loose;
      open += Object.values(line.open).filter(saidOfOpen).length;
    } else {
      const item = items.get(line.item_id);
      const amount = thousandths(String(line.quantity));
      const made = lineOf(line.item_id);
      if (amount === null) {
        unread += 1;
        continue;
      }
      if (line.open_package_key !== null && older.kind !== "open") {
        open += 1;
        continue;
      }
      const pack = item?.packages.find((each) => each.id === line.package_id);
      const size = pack === undefined ? null : thousandths(pack.size);
      if (
        line.package_size !== null &&
        size !== null &&
        thousandths(String(line.package_size)) !== size
      ) {
        // Resized since: neither its count of packages nor loose would say
        // what was moved, so it is done again, as the server would ask.
        resized = true;
        continue;
      }
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
  }
  const gaps: Gaps = [];
  if (older.linesLost) {
    gaps.push(LINES_LOST);
  } else if (unread > 0) {
    gaps.push(unreadWords(unread));
  }
  if (open > 0) {
    gaps.push(openWords(open));
  }
  if (resized) {
    gaps.push(RESIZED);
  }
  const kind: Kind =
    older.kind === null || older.kind === "report" ? last.kind : older.kind;
  return {
    // Choices the catalogue no longer has are cleared, for the cart to ask.
    cart: reconcile(
      {
        ...freshCart(last),
        kind,
        volunteerId: older.volunteerId ?? last.volunteerId,
        fromId: older.fromId,
        toId: older.toId,
        installNumber: older.installNumber,
        lines: [...lines.values()],
      },
      catalogue,
    ),
    gaps,
  };
}

/**
 * Whether `older` goes back in the cart whole, for the page to put it
 * there without a tap: something to put back, nothing left out (no line
 * unread, nothing of an open package, no package resized), no item or
 * package retired, gone or not, and no refusal, as each needs the
 * volunteer: a refusal's reason is said only while it is listed, and a
 * package gone would go back loose, or not at all.
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
      .filter(([, n]) => (count(n) ?? 0) !== 0)
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
  return listed
    .filter(
      (each) => !skip.has(olderId(each)) && goesBackWhole(each, catalogue),
    )
    .sort((a, b) => timeOf(a) - timeOf(b))[0];
}

/** When it was saved, or a cart last changed; one of no known time, last. */
const timeOf = (each: Older): number => each.at ?? Infinity;

/** One of what older builds left, among the rest. */
export function olderId(each: Older): string {
  return `${each.key} ${each.batch ?? "cart"}`;
}

/** The catalogue with its retired items no longer offered added, retired,
 * to name them and their packages in what older builds left; never to
 * fill a cart from. */
export function withRetired(catalogue: Catalogue): Catalogue {
  const named: Item[] = catalogue.retired_items.map((item) => ({
    ...item,
    category: "",
    manufacturer: null,
    part_number: null,
    old_code: null,
    search_text: "",
    retired: true,
    packages: item.packages.map((pack) => ({
      ...pack,
      track_opened: false,
      retired: true,
      short_below: null,
      sealed: [],
    })),
  }));
  return { ...catalogue, items: [...catalogue.items, ...named] };
}

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
export function nameOf(item: Catalogue["items"][number] | undefined): string {
  return item?.name ?? "An item no longer in the catalogue";
}

/** An item's unit after an amount: none for things counted. */
export function unitOf(item: Catalogue["items"][number] | undefined): string {
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

  /** Follow other tabs' changes to the older keys: an older build's tab
   * still open may send a save meanwhile. Returns what stops it. */
  listen(): () => void {
    return onStoredChange(
      [...olderKeys(OUTBOX_KEY), ...olderKeys(CART_KEY)],
      () => this.refresh(),
    );
  }
}
