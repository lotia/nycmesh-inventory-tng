/**
 * Batches saved on this browser, kept until the server has answered about
 * them (docs/decisions/0002, 0009).
 *
 * Exactly once rests on the server, not here: each batch is given its
 * idempotency key when it is queued, every attempt sends the same body with
 * it, and the ledger answers a repeat with the first transaction. So the outbox
 * promises at least once: a batch settles only when the server has answered
 * about it, and nothing here ever gives a batch a new key.
 *
 * Kept in localStorage, which outlives the tab and does not depend on the
 * device cookie, so registering loses no work. Each change rereads storage
 * first, so two tabs do not drop each other's batches. Each build reads and
 * writes its own key only: a tab of an older build sends its own batches
 * before it asks for a reload (build.ts), and what an older build left
 * under an older key is listed for the volunteer to put back in the cart or
 * discard (older.ts). Builds that keep the same shape share a key, so each
 * batch names the build that queued it, and is sent as that build's: one
 * the server holds back as from a build too old (409 build_too_old) is
 * listed with what older builds left, unless it is this tab's own, or a
 * newer build's, when the tab is too old. A cart is sent under its own key
 * (cart.ts, batchKey), made with its first line, or that of a save an older
 * build left that filled it: if another tab recorded it first, a tab that
 * took it over or one that had not heard, the server takes it once.
 */
import { signal } from "@preact/signals";
import { v4 as uuid } from "uuid";
import * as v from "valibot";
import { type Api, check, makeApi, type Schemas } from "./api/client";
import {
  BuildTooOldReply,
  DeviceRefusedReply,
  id,
  KeyReusedReply,
  type RecordedBatch,
  Saved,
  ValidationFailed,
} from "./api/replies";
import { Builds, HEADER, MUST_UPGRADE } from "./build";
import { NOT_REGISTERED } from "./registration";
import { onStoredChange, readStored, writeStored } from "./stored";
import { telemetry } from "./telemetry";

/** Versioned: a build that changes what is kept takes a new key, as a tab
 * of an older build, open during a deploy, would drop what it cannot read
 * from what it writes back. v4's is stamped with the build that wrote it,
 * its entries each with the build that queued them, says which were
 * sent under a cart's key (putBack), and keeps why each was
 * refused or waits as a code (reason), not in words. */
export const STORAGE_KEY = "inventory.outbox.v4";
/** How long to wait before trying again after no answer. */
export const RETRY_MS = 30_000;
/** Unexpected answers to one batch before its note asks for a report. */
export const REPORT_AFTER = 5;
/** Saved batches kept on show, newest first, so a volunteer can check.
 * An older one, or one dismissed, is forgotten only once a catalogue shows
 * it (forgetShown), as the scan page counts it until then (cart.ts,
 * unseen). */
const SAVED_KEPT = 10;
/** Saved batches kept at most, shown by a catalogue or not, so storage
 * never fills when no catalogue is read: half the keys a catalogue is
 * asked about (cart.ts, MAX_KEYS_ASKED), leaving room for those waiting. */
export const SAVED_MOST = 50;

/** On the list: all but the saved batches dismissed, and those too old,
 * which older.ts lists. */
const onList = (entry: Entry) =>
  entry.dismissed !== true && entry.state !== "too_old";

const nullableId = v.exactOptional(v.nullable(id));

/** A batch's idempotency key. */
export const BatchKey = v.pipe(v.string(), v.uuid());

/** An amount in an item's unit: a decimal, as sent, or an older page's
 * number. */
const amount = v.union([v.number(), v.string()]);

/** A line of a batch as POST /api/transactions takes it. */
export const BatchLine = v.object({
  item_id: id,
  quantity: amount,
  /** Sealed packages of it, kept sealed; absent (an older page's): loose. */
  package_id: nullableId,
  /** The size the page took that package to be: refused if resized
   * since. The server requires it with package_id, but a build from
   * before it was sent kept lines without: read, so the server's
   * refusal names the line, never dropped unseen (inventory-tng-1p8). */
  package_size: v.exactOptional(v.nullable(amount)),
  /** A move's taken from an open package: all of it, moved whole. */
  whole: v.exactOptional(v.nullable(v.boolean())),
  /** An opening's: the key of the open package it makes, followed;
   * another's, the open package it is taken from. */
  open_package_key: v.exactOptional(v.nullable(v.pipe(v.string(), v.uuid()))),
});

/** A batch as POST /api/transactions takes it. */
export const BatchBody = v.object({
  key: BatchKey,
  kind: v.picklist(["move", "receipt", "install", "open", "report"]),
  volunteer_id: id,
  from_location_id: nullableId,
  to_location_id: nullableId,
  install_number: nullableId,
  lines: v.array(BatchLine),
  /** Open packages said to be used up, by key (docs/decisions/0020). */
  used_up: v.exactOptional(v.nullable(v.array(v.pipe(v.string(), v.uuid())))),
  /** What is really on open packages, said, by key. */
  lengths: v.exactOptional(
    v.nullable(
      v.array(
        v.object({
          open_package_key: v.pipe(v.string(), v.uuid()),
          left: amount,
        }),
      ),
    ),
  ),
  /** The lines its cart held when put back under its key, before the
   * volunteer changed it (cart.ts, changedFrom), or those of the batch
   * queued here first under it (Outbox.queue): what it holds less of than
   * these, and than was recorded under the key, the volunteer took off,
   * which the server keeps for an administrator as it refuses it
   * (inventory-tng-0gg). Never part of what the batch is (sameBatch). A
   * build before keeps none: the server then keeps nothing. */
  changed_from: v.exactOptional(v.nullable(v.array(BatchLine))),
});

export type Body = Schemas["Batch"];
/** A batch before the outbox gives it its key. */
export type Draft = Omit<Body, "key">;

/** A batch's reports, as a 422 names them. */
export const REPORTS = {
  used_up: "Open package said used up",
  lengths: "What is on an open package",
} as const;

/** A kind of report a batch holds (REPORTS). */
export type ReportKind = keyof typeof REPORTS;

const isReport = (kind: unknown): kind is ReportKind =>
  typeof kind === "string" && Object.hasOwn(REPORTS, kind);

/** A line's package_size refused as a page's bug, as a build from before
 * sizes were sent kept its lines (inventory-tng-1p8), by the server's type:
 * its words are for whoever writes a client, these for the volunteer. */
const SIZE_REFUSED = {
  package_size_required:
    "A line of a package says the size it was counted in: load the page again, and count it again",
  package_size_without_package:
    "A loose line says a package's size: load the page again, and count it again",
} as const;

/** A whole number, as kept. */
const whole = v.pipe(v.number(), v.integer());

/** One of a 422's problems, as kept. */
const Problem = v.object({
  /** What it names: a line, numbered as the cart's (lineNumbers), or a
   * report (REPORTS), numbered among those of its kind from 1; or neither. */
  names: v.nullable(
    v.object({
      of: v.picklist(["line", ...(Object.keys(REPORTS) as ReportKind[])]),
      n: whole,
    }),
  ),
  /** A line's package_size refused as a page's bug: said in the page's
   * words (SIZE_REFUSED), not the server's. */
  size: v.nullable(
    v.picklist(Object.keys(SIZE_REFUSED) as (keyof typeof SIZE_REFUSED)[]),
  ),
  /** The server's words, if it gave any. */
  msg: v.nullable(v.string()),
});

type Problem = v.InferOutput<typeof Problem>;

/** What another tab recorded under a batch's key, as the server said
 * (409 key_reused): the transaction, as a batch sends one, under that key;
 * and who recorded it, as far as this browser knows: one of its tabs of
 * this build, whose outbox held a batch under the key (here), or another
 * (elsewhere). */
export const Recorded = v.object({
  by: v.picklist(["here", "elsewhere"]),
  transactionId: id,
  batch: BatchBody,
});

export type Recorded = v.InferOutput<typeof Recorded>;

/** Why a batch was refused, or why it waits, as kept: a code, and what
 * the page's words for it are made from (noteOf). */
export const Reason = v.union([
  v.object({
    code: v.picklist([
      // No answer: tried again.
      "no_answer",
      // The inventory cannot record just now (503): tried again.
      "unavailable",
      // This tab's own, or a newer build's, held back as too old.
      "must_upgrade",
      // 409 key_reused: the inventory holds its key for another batch.
      "key_reused",
    ]),
  }),
  /** 409 key_reused of a batch sent under its cart's key, which another
   * tab recorded first, as it was then: what it recorded, once the server
   * has said; or, listed so without being sent, as a batch under that key
   * was queued here first, the key to ask the server under (heldKey). A
   * build before inventory-tng-0gg keeps the code alone. */
  v.object({
    code: v.literal("recorded_elsewhere"),
    recorded: v.exactOptional(Recorded),
    heldKey: v.exactOptional(BatchKey),
    /** Its difference put in the cart (Add the difference to cart). */
    added: v.exactOptional(v.literal(true)),
  }),
  /** An answer this build does not understand: its HTTP status, and how
   * many in a row. */
  v.object({ code: v.literal("unexpected"), status: whole, tries: whole }),
  /** 403: the device is not registered, and why. */
  v.object({
    code: v.literal("not_registered"),
    why: DeviceRefusedReply.entries.reason,
  }),
  /** 422, and its problems. */
  v.object({ code: v.literal("refused"), problems: v.array(Problem) }),
]);

export type Reason = v.InferOutput<typeof Reason>;

/** Words for an alert: a string is the page's own, its numbers counts it
 * wrote, shown in italic; `{ quoted }` is words it did not write, such as
 * a name or the server's, shown as they are (Ali, inventory-tng-93z). */
export type Said = readonly (string | { quoted: string })[];

/** A note on show: `bold`, then `plain`, not bold, as a refusal's
 * problems after "Not recorded." (Ali, inventory-tng-93z). */
export type Note = { bold: Said; plain?: Said };

/** `said` as plain text, where nothing is in italic. */
export function wordsOf(said: Said): string {
  return said
    .map((part) => (typeof part === "string" ? part : part.quoted))
    .join("");
}

/** A batch refused for its key (key_reused), in words. */
export const KEY_REUSED =
  "Not recorded: its key was already used for a different batch. Please report this.";

/** What the page says first of a recorded_elsewhere, bold, as an alert,
 * with details below it (Ali, inventory-tng-0gg, 2026-11-15, 1). */
export const SOME_NOT_RECORDED = "Some items were not recorded. Details below.";

/**
 * The heading over what another tab recorded, by who recorded it
 * (Recorded.by): this browser's own tab, as when it could not empty the
 * cart it recorded, or another (Ali, inventory-tng-0gg, 2026-11-15, 1 and
 * 6).
 */
export function recordedHeading(recorded: Recorded): string {
  return recorded.by === "here"
    ? `Already recorded from this tab in transaction ${recorded.transactionId}`
    : `Recorded in another tab in transaction ${recorded.transactionId}`;
}

/** The heading over what a recorded_elsewhere holds otherwise than was
 * recorded (recorded.ts), or, with nothing known of what was recorded,
 * over its lines (Ali, inventory-tng-0gg, 2026-11-15, 1). What it held is
 * recorded, but not the changes since, so it never goes in a cart whole
 * again, which would record it twice (Ali, inventory-tng-wyq, question c,
 * and inventory-tng-g0e, d). */
export const MAKE_CHANGES = "Not recorded. Make any changes and record again.";

/** A batch waiting after no_answer, while the browser is online. */
export const NO_ANSWER = "No answer from the inventory. Trying again shortly.";

/** NO_ANSWER's words while the browser says it is offline (Ali,
 * inventory-tng-wyq, 'Offline'). */
export const OFFLINE = "This device is offline. Sending when it's back.";

/** Before a refused batch's problems (422), in bold. */
const NOT_RECORDED = "Not recorded.";

/** What the page says of `reason`; of no_answer, as the browser is
 * `online` or not. */
export function noteOf(reason: Reason, online = true): Note {
  switch (reason.code) {
    case "no_answer":
      return { bold: [online ? NO_ANSWER : OFFLINE] };
    case "unavailable":
      return {
        bold: [
          "The inventory can't record this just now. Trying again shortly.",
        ],
      };
    case "must_upgrade":
      return { bold: [MUST_UPGRADE] };
    case "key_reused":
      return { bold: [KEY_REUSED] };
    case "recorded_elsewhere":
      return { bold: [SOME_NOT_RECORDED] };
    case "unexpected":
      return {
        bold: [
          reason.tries >= REPORT_AFTER
            ? `The inventory keeps failing on this batch (${reason.status}, ${reason.tries} tries). It is kept and tried again; please report this.`
            : `The inventory answered unexpectedly (${reason.status}). Trying again shortly.`,
        ],
      };
    case "not_registered":
      return {
        bold: [`${NOT_REGISTERED[reason.why]} Register it to send this batch.`],
      };
    case "refused":
      return {
        bold: [NOT_RECORDED],
        plain: reason.problems.flatMap(problemSaid),
      };
  }
}

/** One of a 422's problems, after a space: what it names in the page's
 * words, then why, in the server's, or the page's for a size refused. */
function problemSaid({ names, size, msg }: Problem): Said {
  const where =
    names === null
      ? []
      : [
          names.of === "line"
            ? `Line ${names.n}: `
            : `${REPORTS[names.of]}, ${names.n}: `,
        ];
  const why =
    size !== null
      ? SIZE_REFUSED[size]
      : msg === null
        ? "Not accepted"
        : { quoted: msg };
  return [" ", ...where, why];
}

/** A batch as kept: once a build keeping it under STORAGE_KEY is deployed,
 * a field added here takes a new key. */
export const Entry = v.object({
  body: BatchBody,
  /** What is in it, in words, so two batches can be told apart. */
  what: v.string(),
  queuedAt: v.number(),
  /** The build that queued it, named when it is sent (build.HEADER). */
  build: v.string(),
  /** too_old: held back by the server as from a build too old, and listed
   * with what older builds left (older.ts), to put back in the cart. */
  state: v.picklist(["waiting", "saved", "refused", "too_old"]),
  /** While waiting: what it waits for. */
  waitingFor: v.nullable(v.picklist(["network", "registration"])),
  /** Why it was refused, or why it waits: a code, with what the page's
   * words for it are made from (noteOf), never the words. */
  reason: v.nullable(Reason),
  transactionId: v.nullable(id),
  /** Unexpected answers to this batch in a row. */
  failures: whole,
  /** Saved, and dismissed: off the list, kept until a catalogue shows it. */
  dismissed: v.exactOptional(v.literal(true)),
  /** Sent under its cart's key (cart.ts, batchKey), which another tab may
   * have recorded first: a tab of an older build, whose save or cart this
   * took over (older.ts), or one that had not heard it was recorded. Named
   * for the save put back, which first had one, as v4 keeps it. */
  putBack: v.exactOptional(v.literal(true)),
});

export type Entry = v.InferOutput<typeof Entry>;

/** What became of a batch queued (Outbox.queue): kept and sent; the same
 * batch already queued, so nothing added; listed refused as recorded
 * elsewhere, never sent; or not kept, as the browser would not store it. */
export type Queued = "queued" | "already" | "refused" | "not_kept";

/** Whether the server may hold this batch's key: it may be sent, it was
 * saved, or it was refused for its key. Refused for what it held (422), or
 * held back as from a build too old, it holds none. */
function holdsKey(entry: Entry): boolean {
  return (
    entry.state === "waiting" ||
    entry.state === "saved" ||
    entry.reason?.code === "key_reused" ||
    entry.reason?.code === "recorded_elsewhere"
  );
}

/** `entries`, each refused for what it held (422) under `key` given a
 * key of its own, as the server now holds or asks about `key`: so one key
 * names one batch. */
function rekeyRefused(entries: Entry[], key: string | undefined): Entry[] {
  return entries.map((each) =>
    each.body.key === key && each.reason?.code === "refused"
      ? { ...each, body: { ...each.body, key: uuid() } }
      : each,
  );
}

/** Each body's batchId, worked out once: bodies are never changed in
 * place, and each render and update names every batch listed. */
const batchIds = new WeakMap<Entry["body"], string>();

/** What names a batch, as storage reads it (parsed, so its fields in one
 * order): its key and what it holds, as one key may name two
 * (Outbox.queue). What it was changed from is not what it is, as the
 * server compares. */
export function batchId(body: Entry["body"]): string {
  let id = batchIds.get(body);
  if (id === undefined) {
    id = JSON.stringify({ ...body, changed_from: undefined });
    batchIds.set(body, id);
  }
  return id;
}

/** Whether two batches, each as storage reads it, are the same. */
function sameBatch(a: Entry["body"], b: Entry["body"]): boolean {
  return batchId(a) === batchId(b);
}

/** What STORAGE_KEY holds: the batches, and the build that last wrote them.
 * Each entry is read on its own (parsed). */
export const Kept = v.object({
  build: v.string(),
  entries: v.array(v.unknown()),
});

/** The number of a batch's line naming an item, by the item's first place
 * among `itemIds`, its lines' (problems' "Line n"; older.linesSaid). */
export function lineNumbers(
  itemIds: readonly number[],
): (itemId: number) => number {
  const items = [...new Set(itemIds)];
  return (itemId) => items.indexOf(itemId) + 1;
}

/** A 422's problems, as kept. A line of the cart may be sent as several,
 * one for each package and one loose (cart.ts, toBatch), so a line is
 * named as the cart's: by its item's place in it. */
function problems(
  detail: v.InferOutput<typeof ValidationFailed>["detail"],
  body: Body,
): Problem[] {
  const lineOf = lineNumbers(body.lines.map((line) => line.item_id));
  return detail.map(({ loc, msg, type }) => {
    const line = loc[0] === "body" && loc[1] === "lines" ? loc[2] : undefined;
    const size =
      loc[3] === "package_size" && Object.hasOwn(SIZE_REFUSED, type)
        ? (type as keyof typeof SIZE_REFUSED)
        : null;
    const said = { size, msg: typeof msg === "string" ? msg : null };
    // A report's, by its place in the batch's list of them.
    if (
      loc[0] === "body" &&
      isReport(loc[1]) &&
      // Whole, as kept: one that is not would drop the entry.
      typeof loc[2] === "number" &&
      Number.isInteger(loc[2])
    ) {
      return { names: { of: loc[1], n: loc[2] + 1 }, ...said };
    }
    const sent = typeof line === "number" ? body.lines[line] : undefined;
    return {
      names:
        sent === undefined ? null : { of: "line", n: lineOf(sent.item_id) },
      ...said,
    };
  });
}

export class Outbox {
  /** Everything kept, oldest first, settled batches included. */
  readonly entries = signal<Entry[]>([]);
  private sending = false;
  private retry: ReturnType<typeof setTimeout> | undefined;
  /** Saved batches another tab let storage forget, as its catalogue showed
   * them: kept in `entries` until this tab's shows them too (forgetShown),
   * as its page counts them until then. Off the list. By batchId, as one
   * key may name two (queue). */
  private readonly forgottenElsewhere = new Map<string, Entry>();

  constructor(
    private readonly api: Api,
    private readonly storage: Storage = localStorage,
    /** This build, and whether a newer one is out. */
    readonly builds = new Builds(undefined, storage),
  ) {
    this.show(this.read());
  }

  /** `stored`, and the batches forgotten elsewhere, oldest first. */
  private show(stored: Entry[]): void {
    const here = new Set(stored.map((entry) => batchId(entry.body)));
    const elsewhere = [...this.forgottenElsewhere].flatMap(([id, entry]) =>
      here.has(id) ? [] : [entry],
    );
    this.entries.value = oldestFirst(elsewhere, stored);
  }

  /** What storage holds, oldest first. */
  private read(): Entry[] {
    const kept = v.safeParse(Kept, readStored(this.storage, STORAGE_KEY));
    return kept.success ? parsed(kept.output.entries) : [];
  }

  /** Change what is kept, from what storage holds now. False if the browser
   * would not store it (full, or storage forbidden). */
  private update(change: (entries: Entry[]) => Entry[]): boolean {
    const next = change(this.read());
    const kept = writeStored(this.storage, STORAGE_KEY, {
      build: this.builds.build,
      entries: next,
    } satisfies v.InferOutput<typeof Kept>);
    // Shown as stored, and as storage reads it back (parsed, batchId), so
    // the page never shows what was not kept.
    this.show(kept ? parsed(next) : this.read());
    return kept;
  }

  /** An answer to the batch `body`, applied to it while it still waits, or
   * a save whatever became of it: another tab may have sent it meanwhile,
   * and one saved is never refused or listed to save again. Not to another
   * batch under its key, as a tab of a build before inventory-tng-e2d may
   * queue for a cart it had not heard was recorded: that one is sent, and
   * answered, itself. */
  private settle(body: Entry["body"], changes: Partial<Entry>): void {
    this.update((entries) => {
      const next = entries.map((entry) =>
        sameBatch(entry.body, body) &&
        (entry.state === "waiting" ||
          heldKeyOf(entry) !== undefined ||
          changes.state === "saved")
          ? { ...entry, ...changes }
          : entry,
      );
      const saved = next.filter((entry) => entry.state === "saved");
      const dropped = new Set(saved.slice(0, -SAVED_MOST));
      return next.filter((entry) => !dropped.has(entry));
    });
  }

  /** What the list shows: all but the saved batches dismissed. */
  shown(): Entry[] {
    return this.entries.value.filter(
      (entry) =>
        onList(entry) && !this.forgottenElsewhere.has(batchId(entry.body)),
    );
  }

  /**
   * Forget the saved batches a catalogue shows (`saved`, its keys) that are
   * dismissed or older than the newest SAVED_KEPT on show. Until then the
   * scan page still counts them (cart.ts, unseen), so an opening or a cut
   * saved while the catalogue could not be read again is never lost.
   */
  forgetShown(saved: readonly string[]): void {
    const inCatalogue = new Set(saved);
    const before = this.forgottenElsewhere.size;
    for (const [id, entry] of this.forgottenElsewhere) {
      if (inCatalogue.has(entry.body.key)) {
        this.forgottenElsewhere.delete(id);
      }
    }
    if (this.forgottenElsewhere.size !== before) {
      this.show(this.read());
    }
    const forgotten = (entries: Entry[]) => {
      const onShow = entries.filter(
        (entry) => entry.state === "saved" && onList(entry),
      );
      const kept = new Set(onShow.slice(-SAVED_KEPT));
      return entries.filter(
        (entry) =>
          entry.state === "saved" &&
          inCatalogue.has(entry.body.key) &&
          !kept.has(entry),
      );
    };
    // Checked on what is shown, so a load that forgets nothing writes
    // nothing.
    if (forgotten(this.entries.value).length > 0) {
      this.update((entries) => {
        const gone = new Set(forgotten(entries));
        return entries.filter((entry) => !gone.has(entry));
      });
    }
  }

  /**
   * Give this batch its key, keep it, and start sending it. The key is
   * `key`, its cart's (cart.ts, batchKey), so the server takes the cart
   * once, whichever tab sends it first; or, for a cart an earlier build
   * kept without one, made here. Either way it is kept with the batch for
   * every attempt. Says what became of it (Queued); not_kept means the
   * browser would not store it: nothing was sent, and the caller still
   * holds the batch.
   *
   * A key already queued here is that cart again, emptied by Record but
   * not kept so, or filled again by a tab that had not heard. The same
   * again, waiting or saved, is not queued twice. Another batch, while
   * the server may hold that key (one under it waits, was saved, or was
   * refused for it), is not recorded, as the server would refuse it (409
   * key_reused): it is listed so, under a key of its own, never sent, so
   * one key names one batch and no answer to the first settles it. One
   * refused for what it held (422) holds no key: it takes a key of its
   * own, and this batch goes under the cart's.
   */
  queue(draft: Draft, what: string, key?: string): Queued {
    const entry = newEntry(
      { ...draft, key: key ?? uuid() },
      what,
      this.builds.build,
    );
    if (key !== undefined) {
      entry.putBack = true;
    }
    const body = v.parse(BatchBody, entry.body);
    let queued = "already" as Queued;
    const kept = this.update((entries) => {
      const before = entries.filter((each) => each.body.key === key);
      if (
        before.some(
          (each) =>
            (each.state === "waiting" || each.state === "saved") &&
            sameBatch(each.body, body),
        )
      ) {
        return entries;
      }
      if (before.some(holdsKey)) {
        queued = "refused";
        // Changed from what was queued here first under the key, saved or
        // waiting: what it took off that is the volunteer's.
        const first =
          before.find((each) => each.state === "saved") ??
          before.find((each) => each.state === "waiting");
        return [
          ...entries,
          {
            ...entry,
            body: {
              ...entry.body,
              key: uuid(),
              ...(first === undefined
                ? {}
                : { changed_from: first.body.lines }),
            },
            state: "refused",
            // Asked under the cart's key once nothing under it waits
            // (send): so the server says what it recorded, and keeps what
            // this took off a line it recorded, for an administrator.
            reason: {
              code: "recorded_elsewhere",
              ...(key === undefined ? {} : { heldKey: key }),
            },
          },
        ];
      }
      queued = "queued";
      return [...rekeyRefused(entries, key), entry];
    });
    if (!kept) {
      return "not_kept";
    }
    if (queued !== "already") {
      // A batch listed refused is asked about under the key held for it.
      void this.send();
    }
    return queued;
  }

  /** Mark one recorded elsewhere as having had its difference put in the
   * cart (Add the difference to cart), so it is offered no more: that batch
   * alone, not another under its key. False if the browser would not keep
   * that. */
  differenceAdded(listed: Entry): boolean {
    const body = v.parse(BatchBody, listed.body);
    return this.update((entries) =>
      entries.map((entry) =>
        sameBatch(entry.body, body) &&
        entry.reason?.code === "recorded_elsewhere"
          ? { ...entry, reason: { ...entry.reason, added: true as const } }
          : entry,
      ),
    );
  }

  /** Take a settled batch off the list: a refused one is forgotten, a
   * saved one once a catalogue shows it (forgetShown). That batch alone,
   * not another under its key. */
  dismiss(listed: Entry): void {
    const body = v.parse(BatchBody, listed.body);
    this.update((entries) =>
      entries.flatMap((entry) => {
        if (!sameBatch(entry.body, body) || entry.state === "waiting") {
          return [entry];
        }
        return entry.state === "saved"
          ? [{ ...entry, dismissed: true as const }]
          : [];
      }),
    );
  }

  /**
   * Send what is waiting, oldest first, one at a time. Order does not
   * matter to the ledger: each batch is its own transaction, and balances
   * may go below zero (docs/decisions/0003). So a batch the server keeps
   * failing on is passed over until the next round, and the rest still go.
   * What would fail for every batch (no answer, the database or a proxy
   * down, an unregistered device) stops the round.
   */
  async send(): Promise<void> {
    // Too old, nothing this tab sends is taken: kept until it reloads.
    if (this.sending || this.builds.newer.value === "too_old") {
      return;
    }
    this.sending = true;
    clearTimeout(this.retry);
    const tried: Entry["body"][] = [];
    try {
      for (;;) {
        const stored = this.read();
        const waiting = stored.filter((each) => each.state === "waiting");
        // An open package an earlier batch still waiting makes, takes from
        // or reports: a later one naming it waits too, in order, or the
        // server, which checks what is left on it and where, would refuse
        // it for good.
        const before = new Set<string>();
        const entry = waiting.find((each) => {
          const named = keysNamed(each.body);
          const ready =
            !tried.some((body) => sameBatch(body, each.body)) &&
            !named.some((key) => before.has(key));
          for (const key of [...keysMade(each.body), ...named]) {
            before.add(key);
          }
          return ready;
        });
        // Then one listed recorded elsewhere without being sent, asked
        // under the key held for it once nothing under that key waits.
        const asked =
          entry ??
          stored.find((each) => {
            const held = heldKeyOf(each);
            return (
              held !== undefined &&
              !tried.some((body) => sameBatch(body, each.body)) &&
              !waiting.some((other) => other.body.key === held)
            );
          });
        if (asked === undefined) {
          return;
        }
        tried.push(asked.body);
        if ((await this.sendOne(asked)) === "stop") {
          return;
        }
      }
    } finally {
      this.sending = false;
    }
  }

  private later(): void {
    clearTimeout(this.retry);
    this.retry = setTimeout(() => void this.send(), RETRY_MS);
  }

  /** POST `body` as `build`'s, hearing of a newer build from the reply. */
  private post(body: Body, build: string) {
    const call = this.api.POST("/api/transactions", {
      // As the build that queued it, which the server judges it by.
      params: { header: { [HEADER]: build } },
      body,
    });
    // A save, and a 409 build_too_old, name the server's build
    // (page_builds.py), which may be newer; its other answers name none.
    call.then(
      ({ response }) =>
        this.builds.heard(response.headers.get(HEADER), "server"),
      () => {},
    );
    return check(call, {
      200: Saved,
      201: Saved,
      403: DeviceRefusedReply,
      409: v.variant("reason", [KeyReusedReply, BuildTooOldReply]),
      422: ValidationFailed,
    });
  }

  /**
   * Ask the server what it recorded under the key held for one listed
   * recorded elsewhere without being sent (heldKeyOf), by sending it under
   * that key: so the server, refusing it (409 key_reused), says what it
   * recorded and keeps what this took off a line it recorded, for an
   * administrator. Recorded instead, as nothing held the key after all
   * (the batch under it refused, 422), it is saved under it; the same as
   * recorded, it is dropped, as the batch under the key says it; refused
   * (422), it says why. Anything else leaves it as it is, asked again later,
   * but a build too old to be taken, which asks no more. Whether the round
   * goes on to the next batch.
   */
  private async askHeld(entry: Entry, held: string): Promise<"next" | "stop"> {
    const sent = { ...entry.body, key: held };
    const reply = await this.post(sent, entry.build);
    const listed = (changes: Partial<Entry>) =>
      this.settle(entry.body, changes);
    switch (reply.status) {
      case 200:
        this.update((entries) =>
          entries.filter((each) => !sameBatch(each.body, entry.body)),
        );
        return "next";
      case 201:
        // What else is under the key was refused for what it held (422),
        // so the server held none: it takes a key of its own, as in
        // queue, so one key names one batch.
        this.update((entries) => rekeyRefused(entries, held));
        listed({
          state: "saved",
          reason: null,
          transactionId: reply.body.transaction_id,
          body: sent,
        });
        return "next";
      case 403:
        return "stop";
      case 409:
        if (reply.body.reason === "key_reused") {
          listed({
            reason: recordedElsewhere(
              held,
              reply.body.recorded ?? null,
              this.holdsSaved(held, entry.body),
            ),
          });
          return "next";
        }
        if (
          entry.build === this.builds.build ||
          this.builds.isNewer(entry.build)
        ) {
          this.builds.learn("too_old");
          return "stop";
        }
        listed({ reason: { code: "recorded_elsewhere" } });
        return "next";
      case 422:
        listed({
          reason: {
            code: "refused",
            problems: problems(reply.body.detail, entry.body),
          },
        });
        return "next";
      case "unexpected":
        this.later();
        return "next";
      default:
        this.later();
        return "stop";
    }
  }

  /** Whether this outbox holds a batch saved under `key` other than
   * `body`: this browser recorded what is under it. */
  private holdsSaved(key: string, body: Body): boolean {
    return this.read().some(
      (each) =>
        each.state === "saved" &&
        each.body.key === key &&
        !sameBatch(each.body, body),
    );
  }

  /** Whether the round goes on to the next batch. */
  private async sendOne(entry: Entry): Promise<"next" | "stop"> {
    const held = heldKeyOf(entry);
    if (held !== undefined) {
      return this.askHeld(entry, held);
    }
    const reply = await this.post(entry.body, entry.build);
    switch (reply.status) {
      case 200:
      case 201:
        this.settle(entry.body, {
          state: "saved",
          waitingFor: null,
          reason: null,
          transactionId: reply.body.transaction_id,
          failures: 0,
        });
        return "next";
      case 403:
        // Sent again once the device registers (registration.ts), or when
        // the page next opens.
        // Every waiting batch waits for the same thing.
        this.update((entries) =>
          entries.map((each) =>
            each.state === "waiting"
              ? {
                  ...each,
                  waitingFor: "registration",
                  reason: { code: "not_registered", why: reply.body.reason },
                }
              : each,
          ),
        );
        return "stop";
      case 409:
        if (reply.body.reason === "build_too_old") {
          if (
            entry.build !== this.builds.build &&
            !this.builds.isNewer(entry.build)
          ) {
            // An older build's, kept under this key: too old itself, not
            // this tab. Listed with what older builds left (older.ts).
            this.settle(entry.body, {
              state: "too_old",
              waitingFor: null,
              reason: null,
            });
            return "next";
          }
          // This tab's own, or a newer one's: this tab is too old. Kept,
          // waiting: a newer build of the page lists it (older.ts).
          this.builds.learn("too_old");
          this.settle(entry.body, {
            waitingFor: null,
            reason: { code: "must_upgrade" },
          });
          return "stop";
        }
        // Sent under its cart's key, which another tab recorded first, as
        // it was then: this, changed since, is not recorded, and says so,
        // with what was recorded, if the server said.
        this.settle(entry.body, {
          state: "refused",
          waitingFor: null,
          reason: entry.putBack
            ? recordedElsewhere(
                entry.body.key,
                reply.body.recorded ?? null,
                this.holdsSaved(entry.body.key, entry.body),
              )
            : { code: "key_reused" },
        });
        return "next";
      case 422:
        this.settle(entry.body, {
          state: "refused",
          waitingFor: null,
          reason: {
            code: "refused",
            problems: problems(reply.body.detail, entry.body),
          },
        });
        return "next";
      case "unexpected": {
        // Something about this batch, perhaps: the others still go. Never
        // dropped; tried again each round.
        const failures = entry.failures + 1;
        telemetry.record({ kind: "outbox_retry", reason: "unexpected" });
        this.settle(entry.body, {
          waitingFor: "network",
          failures,
          reason: { code: "unexpected", status: reply.code, tries: failures },
        });
        this.later();
        return "next";
      }
      default:
        telemetry.record({
          kind: "outbox_retry",
          reason: reply.status === "unavailable" ? "unavailable" : "no_answer",
        });
        this.settle(entry.body, {
          waitingFor: "network",
          reason: {
            code: reply.status === "unavailable" ? "unavailable" : "no_answer",
          },
        });
        this.later();
        return "stop";
    }
  }

  /** Send when the browser comes back online or the page is shown again,
   * and follow other tabs' changes. Returns what stops listening. */
  listen(target: Window = window): () => void {
    const send = () => void this.send();
    const visible = () => {
      if (target.document.visibilityState === "visible") {
        send();
      }
    };
    const follow = () => {
      const stored = this.read();
      const here = new Set(stored.map((entry) => batchId(entry.body)));
      for (const entry of this.entries.value) {
        const id = batchId(entry.body);
        if (entry.state === "saved" && !here.has(id)) {
          this.forgottenElsewhere.set(id, entry);
        }
      }
      this.show(stored);
    };
    const stopFollowing = onStoredChange([STORAGE_KEY], follow);
    target.addEventListener("online", send);
    target.document.addEventListener("visibilitychange", visible);
    return () => {
      target.removeEventListener("online", send);
      target.document.removeEventListener("visibilitychange", visible);
      stopFollowing();
      clearTimeout(this.retry);
    };
  }
}

/** The key held for an entry listed recorded elsewhere without being sent
 * (Outbox.queue), to ask the server under; undefined for any other. */
function heldKeyOf(entry: Entry): string | undefined {
  return entry.state === "refused" &&
    entry.reason?.code === "recorded_elsewhere"
    ? entry.reason.heldKey
    : undefined;
}

/** A 409 key_reused of a batch sent under its cart's key `key`, as kept:
 * what the server said was recorded under it, if anything, and whether
 * this browser's outbox holds that, saved (`here`). */
function recordedElsewhere(
  key: string,
  recorded: v.InferOutput<typeof RecordedBatch> | null,
  here: boolean,
): Reason {
  if (recorded === null) {
    return { code: "recorded_elsewhere" };
  }
  const { transaction_id, ...batch } = recorded;
  return {
    code: "recorded_elsewhere",
    recorded: {
      by: here ? "here" : "elsewhere",
      transactionId: transaction_id,
      batch: { ...batch, key },
    },
  };
}

/** Both, oldest first; `rest` alone, as it is, if `extra` is empty. */
function oldestFirst(extra: Entry[], rest: Entry[]): Entry[] {
  return extra.length === 0
    ? rest
    : [...extra, ...rest].sort((a, b) => a.queuedAt - b.queuedAt);
}

/** Stored entries, minus any that cannot be read: one bad entry loses one
 * batch, not every batch on the device. */
function parsed(stored: readonly unknown[]): Entry[] {
  return stored.flatMap((entry) => {
    const result = v.safeParse(Entry, entry);
    return result.success ? [result.output] : [];
  });
}

/** Whether a line of a batch of `kind` was taken from an open package,
 * cut or moved whole: it never goes back in a cart, nor moves a sealed
 * package. */
export function fromOpen(
  line: { open_package_key?: string | null },
  kind: Body["kind"] | null,
): boolean {
  return kind !== "open" && (line.open_package_key ?? null) !== null;
}

/** The open packages an opening makes, by the keys the page gave them. */
function keysMade(body: Body): string[] {
  return body.kind !== "open"
    ? []
    : body.lines.flatMap((line) => line.open_package_key ?? []);
}

/** The open packages another batch takes from, or says are used up. */
function keysNamed(body: Body): string[] {
  return body.kind === "open"
    ? []
    : [
        ...body.lines.flatMap((line) => line.open_package_key ?? []),
        ...(body.used_up ?? []),
        ...(body.lengths ?? []).map((each) => each.open_package_key),
      ];
}

/** A batch as first kept by `build`: waiting, and not yet tried. */
export function newEntry(
  body: Body,
  what: string,
  build: string,
  now = Date.now(),
): Entry {
  return {
    body,
    what,
    queuedAt: now,
    build,
    state: "waiting",
    waitingFor: null,
    reason: null,
    transactionId: null,
    failures: 0,
  };
}

let shared: Outbox | undefined;

/** The outbox every page on this browser uses. */
export function sharedOutbox(): Outbox {
  shared ??= new Outbox(makeApi());
  return shared;
}
