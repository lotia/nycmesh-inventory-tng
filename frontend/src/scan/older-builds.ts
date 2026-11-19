/**
 * What older builds of the scan page left on this browser, under keys of
 * their own (shared/build.ts): saves not sent, and a cart with lines; and
 * saves an older build left under this build's own outbox key, once the
 * server held them back as from a build too old (shared/outbox.ts, state
 * too_old). This build never sends them as they were. It lists each for the
 * volunteer, read only in the parts every version kept: the kind, who, the
 * places, the items and how much of each, and when. The scan page puts one
 * that goes back whole (older-to-cart.ts, goesBackWhole) in an empty cart
 * itself; the volunteer adds each other to the cart (toCart), to check,
 * fill in what this build needs and save again, or discards it, or a line
 * of it that cannot go back (dropPart). Nothing else is removed, but keys
 * with nothing to lose (tidyOlder).
 *
 * Each version's shape is in the history of shared/outbox.ts and cart.ts.
 * Every batch kept its key, kind, volunteer_id, places, install_number and
 * lines of item_id and quantity (a number in v1, then a decimal), with
 * queuedAt and state; its summary in words (what) to v4, then what that is
 * made from (outbox.Summary); and why it was refused: note, in words, to v3,
 * then reason, a code (outbox.Reason); package_id came in v1, open packages
 * later.
 * Every cart kept its kind, volunteerId, places, installNumber, usedAt and
 * lines of itemId, packages (counts by package id) and loose; from v2, the
 * key it is to be recorded under (batchKey), of a save an older build left
 * that filled it, or, since inventory-tng-e2d, made with its first line;
 * and each line's sizes, the size each package was counted at.
 */
import { type Signal, signal } from "@preact/signals";
import * as v from "valibot";
import { id } from "../api/replies";
import { say } from "../shared/i18n";
import {
  hasStored,
  olderKeys,
  onStoredChange,
  readStored,
  removeStored,
  writeStored,
} from "../shared/local-storage";
import {
  lineNumbers,
  STORAGE_KEY as OUTBOX_KEY,
  Reason,
  type ReportKind,
} from "../shared/outbox";
import { type Note, noteOf, type Said } from "../shared/outbox-notes";
import { STORAGE_KEY as CART_KEY, type Catalogue, holdsSome } from "./cart";
import {
  goesBackWhole,
  NUMBERED,
  saidOfOpen,
  toCart,
  withRetired,
} from "./older-to-cart";

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
  /** Why it was refused: in words to v3, as a code from v4. */
  note: maybe(v.string()),
  reason: maybe(Reason),
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
   * older build may be sending it still: a save's own, or a cart's, made
   * with its first line or a save's that filled it. None for one the
   * inventory refused for its key, which it then holds for another batch
   * and never takes again; nor for a cart a build before inventory-tng-e2d
   * filled itself, which gets one in the cart (CartStore.change). */
  batchKey: string | null;
  kind: (typeof KINDS)[number] | null;
  volunteerId: number | null;
  fromId: number | null;
  toId: number | null;
  installNumber: string;
  /** When it was saved, or a cart last changed, in milliseconds since 1970. */
  at: number | null;
  /** Why the inventory refused it; null while it waits. */
  refused: Refused | null;
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

/** Why the inventory refused a save, as a code (outbox.Reason), and as
 * the page says it: in this build's words from a code, or as an older
 * build kept them (refusedOf), null if it kept none. */
export type Refused = { code: Reason["code"]; note: Note | null };

export type OldBatchLine = v.InferOutput<typeof OldLine>;
export type OldCartLine = v.InferOutput<typeof OldCartLine>;

/** Each line read on its own, so one odd line loses only itself. */
function linesOf<T extends v.GenericSchema>(lines: unknown[], schema: T) {
  return lines.map((line) => {
    const read = v.safeParse(schema, line);
    return read.success ? (read.output as v.InferOutput<T>) : null;
  });
}

/** The entries a stored outbox holds: a list, as v1 to v3 kept them, or
 * {entries}, as every one since does. */
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
  const { body, state, queuedAt, note, reason } = save;
  const refused = state === "refused" ? refusedOf(reason, note) : null;
  return {
    key,
    batch: body.key,
    // Refused for its key, the inventory holds that key for another.
    batchKey:
      refused?.code === "key_reused" || refused?.code === "recorded_elsewhere"
        ? null
        : body.key,
    kind: body.kind,
    volunteerId: body.volunteer_id,
    fromId: body.from_location_id,
    toId: body.to_location_id,
    installNumber:
      body.install_number === null ? "" : String(body.install_number),
    at: queuedAt,
    refused,
    lines: linesOf(body.lines ?? [], OldLine),
    linesLost: body.lines === null,
    reports: {
      usedUp: body.used_up?.length ?? 0,
      lengths: body.lengths?.length ?? 0,
    },
  };
}

/** A batch this build made, as listed, to put in the cart as a save put
 * back is (toCart), under its own key (recorded-elsewhere.ts,
 * differenceOf). */
export function asSave(body: unknown): Older {
  return saveOf(
    "",
    v.parse(OldEntry, { body, state: "waiting", queuedAt: null }),
  );
}

/** What main's build (v3) kept of a batch refused for its key: its words
 * as kept, read for their code and shown as kept, never said anew. */
const KEY_REUSED_KEPT =
  "Not saved: its key was already used for a different batch. Please report this.";
/** Before the problems main's build kept of a batch refused (422). */
const NOT_SAVED = "Not saved.";

/** Where a problem main's build kept names its line or report, in its
 * words, as kept: not REPORTS, whose words may change. */
const MARKERS =
  /((?:^| )(?:Line \d+|(?:Open package said used up|What is on an open package), \d+): )/;

/**
 * Why a save was refused: from its code, as this build keeps it; or from
 * the words an older build kept, read for their code, and shown as kept,
 * main's "Not saved." in bold before its problems, each naming its line
 * or report in the page's words and why in the server's (Ali,
 * inventory-tng-93z).
 */
function refusedOf(reason: Reason | null, note: string | null): Refused {
  if (reason !== null) {
    return { code: reason.code, note: noteOf(reason) };
  }
  const words = note ?? "";
  if (words === KEY_REUSED_KEPT) {
    return { code: "key_reused", note: { bold: [words] } };
  }
  if (!words.startsWith(NOT_SAVED)) {
    return { code: "refused", note: words === "" ? null : { bold: [words] } };
  }
  const plain: Said = words
    .slice(NOT_SAVED.length)
    .split(MARKERS)
    .flatMap((part, at) =>
      part === "" ? [] : [at % 2 === 1 ? part : { quoted: part }],
    );
  return { code: "refused", note: { bold: [NOT_SAVED], plain } };
}

/**
 * A batch of the outbox (outbox.Entry) the inventory refused, line by
 * line in the words this list uses (toCart's parts), named from
 * `catalogue`, retired items too: for the volunteer to compare with what
 * was recorded. Each item's numbered by its place in the batch, as a
 * 422's "Line n" names it (shared/outbox.ts, problems); a report is not (Ali,
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
    itemId === null ? words : say(NUMBERED, { n: lineOf(itemId), words }),
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

/** What became of one read earlier: listed as it was; listed still, by
 * its olderId, but changed (an older tab refused it or changed its cart,
 * or another tab discarded a line of it); or gone (sent by an older tab,
 * or put back or discarded in another). */
export type Now = "unchanged" | "changed" | "gone";

/** What became of a change to one: done; not done, as it had changed or
 * gone meanwhile (Now); or not kept by the browser. */
export type Outcome = "done" | Exclude<Now, "unchanged"> | "not_kept";

/** What became of `older`, read earlier, as storage holds it now. */
export function compared(older: Older, storage: Storage = localStorage): Now {
  const id = olderId(older);
  const was = JSON.stringify(older);
  const now = readOlder(storage).filter((each) => olderId(each) === id);
  if (now.length === 0) {
    return "gone";
  }
  return now.some((each) => JSON.stringify(each) === was)
    ? "unchanged"
    : "changed";
}

/** Whether the browser kept a change. */
const kept = (done: boolean): Outcome => (done ? "done" : "not_kept");

/**
 * Remove one: a save from its key, the rest kept as they were, or the
 * cart. An older key left with nothing to list goes; this build's outbox
 * key stays, as the outbox's. A save goes even if changed meanwhile, as
 * one put in the cart must never stay listed too, unless `unlessChanged`
 * (a volunteer's Discard, OlderStore.discard); a cart changed meanwhile
 * never does, as what changed was never seen.
 */
export function removeOlder(
  older: Older,
  storage: Storage = localStorage,
  unlessChanged = false,
): Outcome {
  const now = compared(older, storage);
  if (
    now === "gone" ||
    (now === "changed" && (unlessChanged || older.batch === null))
  ) {
    return now;
  }
  return removeRead(older, storage);
}

/** Remove `older`, as removeOlder, once compared. */
function removeRead(older: Older, storage: Storage): Outcome {
  if (older.batch === null) {
    return kept(removeStored(storage, older.key));
  }
  const stored = readStored(storage, older.key);
  const next = withEntries(
    stored,
    entriesOf(stored).filter((entry) => !isSave(entry, older.batch)),
  );
  if (older.key !== OUTBOX_KEY && savesIn(older.key, next).length === 0) {
    return kept(removeStored(storage, older.key));
  }
  return kept(writeStored(storage, older.key, next));
}

/**
 * Discard a part of `older` (Part.drop): what is left is kept as it was,
 * under its key, so a save keeps its batch key, as one put back in the
 * cart and changed there does (Older.batchKey); one left with nothing to
 * list in `catalogue` goes whole (removeOlder), but one that says lines
 * could not be read stays (nothingListed). Only if `older` is unchanged
 * meanwhile, as the parts were read from it.
 */
export function dropPart(
  older: Older,
  drop: readonly Cut[],
  catalogue: Catalogue,
  storage: Storage = localStorage,
): Outcome {
  const now = compared(older, storage);
  if (now !== "unchanged") {
    return now;
  }
  const stored = readStored(storage, older.key);
  let next: unknown;
  if (older.batch === null) {
    const cart = stored as { lines: unknown[] };
    next = { ...cart, lines: cut(cart.lines, drop) };
  } else {
    // A save's reports dropped, by kind, at their places.
    const droppedOf = (kind: ReportKind) =>
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
      const cutReports = (kind: ReportKind) =>
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
    return removeRead(older, storage);
  }
  return kept(writeStored(storage, older.key, next));
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

/** What discarding a part drops from what is kept, by where in what was
 * read: a line whole, or of a cart's line the counts of some packages, its
 * loose and what it said of some open packages; or a save's report. */
export type Cut =
  | { at: number; packages?: string[]; loose?: true; open?: string[] }
  | { report: ReportKind; at: number };

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

  /** What became of `older`, read earlier (compared). */
  compared(older: Older): Now {
    return compared(older, this.storage);
  }

  /** Remove `older`, and list again (removeOlder). */
  remove(older: Older): Outcome {
    const removed = removeOlder(older, this.storage);
    this.refresh();
    return removed;
  }

  /** Discard `older` as it was read, and list again: nothing if it has
   * changed meanwhile, as what changed was never seen (Ali,
   * inventory-tng-8gj); otherwise as removeOlder. */
  discard(older: Older): Outcome {
    const discarded = removeOlder(older, this.storage, true);
    this.refresh();
    return discarded;
  }

  /** Discard a part of `older`, and list again (dropPart). */
  dropPart(older: Older, drop: readonly Cut[], catalogue: Catalogue): Outcome {
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
