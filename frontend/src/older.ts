/**
 * What older builds of the scan page left on this browser, under keys of
 * their own (build.ts): saves not sent, and a cart with lines. This build
 * never sends them as they were. It lists each for the volunteer, read only
 * in the parts every version kept: the kind, who, the places, the items
 * and how much of each, and when. The volunteer puts each back in the cart,
 * to check, fill in what this build needs and save again, or discards it.
 * Nothing is removed until they do, but keys with nothing to lose
 * (tidyOlder).
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
  indexOf,
  type Kind,
  type Line,
  reconcile,
} from "./cart";
import { STORAGE_KEY as OUTBOX_KEY } from "./outbox";
import { decimal, thousandths } from "./quantity";
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
    lines: v.fallback(v.array(v.unknown()), []),
    used_up: maybe(v.array(v.unknown())),
    lengths: maybe(v.array(v.unknown())),
  }),
  state: v.picklist(["waiting", "refused"]),
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
        lines: linesOf(body.lines, OldLine),
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
  const saves = outboxKeys(storage).read.flatMap((key) =>
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
 * cart. A key left with nothing to list goes. False if the browser would
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
  if (savesIn(older.key, next).length === 0) {
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

/** What putting `older` in the cart leaves to do again, in words. */
export type Gaps = string[];

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
  const gaps: Gaps = [];
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
  for (const line of older.lines) {
    if (line === null) {
      unread += 1;
    } else if ("itemId" in line) {
      const made = lineOf(line.itemId);
      made.packages = { ...line.packages };
      made.loose = line.loose;
      if (Object.values(line.open).some(saidOfOpen)) {
        gaps.push(
          `${nameOf(items.get(line.itemId))}: take what was taken from an open package again.`,
        );
      }
    } else {
      const item = items.get(line.item_id);
      const amount = thousandths(String(line.quantity));
      const made = lineOf(line.item_id);
      if (amount === null) {
        unread += 1;
        continue;
      }
      if (line.open_package_key !== null && older.kind !== "open") {
        gaps.push(
          `${nameOf(item)}: ${decimal(amount)}${unitOf(item)} was taken from an open package. Take it again.`,
        );
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
        unread += 1;
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
  if (unread > 0) {
    gaps.push(
      unread === 1
        ? "A line could not be read: add it again if it is missing."
        : `${unread} lines could not be read: add them again if they are missing.`,
    );
  }
  if (older.reports > 0) {
    gaps.push(
      "It also said an open package was used up, or what was on one: say so again on the open package.",
    );
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
