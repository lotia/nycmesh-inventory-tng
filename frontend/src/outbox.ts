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
 * first, so two tabs do not drop each other's batches.
 */
import { signal } from "@preact/signals";
import { v4 as uuid } from "uuid";
import * as v from "valibot";
import { type Api, check, makeApi, type Schemas } from "./api/client";
import {
  DeviceRefusedReply,
  id,
  KeyReusedReply,
  Saved,
  ValidationFailed,
} from "./api/replies";
import { NOT_REGISTERED } from "./registration";
import { hasStored, onStoredChange, readStored, writeStored } from "./stored";
import { telemetry } from "./telemetry";

/** Versioned: a build that changes the shape keeps to a key of its own, as
 * a tab of an older build, open during a deploy, would drop what it cannot
 * read from what it writes back: v3's batches may report what is on an
 * open package (`lengths`), and its saved batches may be dismissed. */
export const STORAGE_KEY = "inventory.outbox.v3";
/** The key of the build before, or, absent, of the one before that. Their
 * batches are copied here, never written there, so an older tab, open
 * during a deploy, keeps sending, settling and counting its own, and this
 * build sends them too: the server saves a batch once, by its key. */
export const BEFORE = "inventory.outbox.v2";
const LONG_BEFORE = "inventory.outbox.v1";
/** The keys of the batches in BEFORE already copied here, so one this
 * build has since forgotten is not copied again. */
const COPIED = `${STORAGE_KEY}.copied`;
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

/** On the list: all but the saved batches dismissed. */
const onList = (entry: Entry) => entry.dismissed !== true;

const nullableId = v.exactOptional(v.nullable(id));

/** An amount in an item's unit: a decimal, as sent, or an older page's
 * number. */
const amount = v.union([v.number(), v.string()]);

/** A batch as POST /api/transactions takes it. */
export const BatchBody = v.object({
  key: v.pipe(v.string(), v.uuid()),
  kind: v.picklist(["move", "receipt", "install", "open", "report"]),
  volunteer_id: id,
  from_location_id: nullableId,
  to_location_id: nullableId,
  install_number: nullableId,
  lines: v.array(
    v.object({
      item_id: id,
      quantity: amount,
      /** Sealed packages of it, kept sealed; absent (an older page's): loose. */
      package_id: nullableId,
      /** The size the page took that package to be: refused if resized
       * since. Absent (an older page's): not checked. */
      package_size: v.exactOptional(v.nullable(amount)),
      /** A move's taken from an open package: all of it, moved whole. */
      whole: v.exactOptional(v.nullable(v.boolean())),
      /** An opening's: the key of the open package it makes, followed;
       * another's, the open package it is taken from. */
      open_package_key: v.exactOptional(
        v.nullable(v.pipe(v.string(), v.uuid())),
      ),
    }),
  ),
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
});

export type Body = Schemas["Batch"];
/** A batch before the outbox gives it its key. */
export type Draft = Omit<Body, "key">;

/** A batch as kept: once a build keeping it under STORAGE_KEY is deployed,
 * a field added here takes a new key. */
export const Entry = v.object({
  body: BatchBody,
  /** What is in it, in words, so two batches can be told apart. */
  what: v.string(),
  queuedAt: v.number(),
  state: v.picklist(["waiting", "saved", "refused"]),
  /** While waiting: what it waits for. */
  waitingFor: v.nullable(v.picklist(["network", "registration"])),
  /** Why it was refused, or why it waits, in words. */
  note: v.nullable(v.string()),
  transactionId: v.nullable(id),
  /** Unexpected answers to this batch in a row. */
  failures: v.pipe(v.number(), v.integer()),
  /** Saved, and dismissed: off the list, kept until a catalogue shows it. */
  dismissed: v.exactOptional(v.literal(true)),
});

export type Entry = v.InferOutput<typeof Entry>;

/** A batch's reports, as a 422 names them. */
const REPORTS: Record<string, string> = {
  used_up: "Open package said used up",
  lengths: "What is on an open package",
};

/** A 422's problems, in words a volunteer can act on. A line of the cart
 * may be sent as several, one for each package and one loose (cart.ts,
 * toBatch), so a line is named as the cart's: by its item's place in it. */
function problems(
  detail: v.InferOutput<typeof ValidationFailed>["detail"],
  body: Body,
) {
  const items = [...new Set(body.lines.map((line) => line.item_id))];
  return detail
    .map(({ loc, msg }) => {
      const line = loc[0] === "body" && loc[1] === "lines" ? loc[2] : undefined;
      const words = typeof msg === "string" ? msg : "Not accepted";
      // A report's, by its place in the batch's list of them.
      const report = REPORTS[String(loc[1])];
      if (
        loc[0] === "body" &&
        report !== undefined &&
        typeof loc[2] === "number"
      ) {
        return `${report}, ${loc[2] + 1}: ${words}`;
      }
      const sent = typeof line === "number" ? body.lines[line] : undefined;
      return sent === undefined
        ? words
        : `Line ${items.indexOf(sent.item_id) + 1}: ${words}`;
    })
    .join(" ");
}

export class Outbox {
  /** Everything kept, oldest first, settled batches included. */
  readonly entries = signal<Entry[]>([]);
  private sending = false;
  private retry: ReturnType<typeof setTimeout> | undefined;
  /** Saved batches another tab let storage forget, as its catalogue showed
   * them: kept in `entries` until this tab's shows them too (forgetShown),
   * as its page counts them until then. Off the list. */
  private readonly forgottenElsewhere = new Map<string, Entry>();

  constructor(
    private readonly api: Api,
    private readonly storage: Storage = localStorage,
  ) {
    this.show(this.read());
  }

  /** `stored`, and the batches forgotten elsewhere, oldest first. */
  private show(stored: Entry[]): void {
    const here = new Set(stored.map((entry) => entry.body.key));
    const elsewhere = [...this.forgottenElsewhere.values()].filter(
      (entry) => !here.has(entry.body.key),
    );
    this.entries.value = oldestFirst(elsewhere, stored);
  }

  /** What storage holds: this build's, and an older build's not yet
   * copied here, oldest first. */
  private read(): Entry[] {
    return this.readAll().entries;
  }

  /** What read() returns, and `toCopy`: the keys COPIED is to hold, if it
   * does not hold them yet. */
  private readAll(): { entries: Entry[]; toCopy?: string[] } {
    const kept = readStored(this.storage, STORAGE_KEY);
    const here = parsed(kept);
    // LONG_BEFORE only while BEFORE was never written: the build before
    // copied its batches.
    const before = readStored(this.storage, BEFORE);
    const older = parsed(
      before !== undefined || hasStored(this.storage, BEFORE)
        ? before
        : readStored(this.storage, LONG_BEFORE),
    );
    // Trusted only beside what it says was copied: here unreadable, its
    // copies are gone too, so they are copied again.
    const listed = Array.isArray(kept) ? readStored(this.storage, COPIED) : [];
    const copied = new Set(Array.isArray(listed) ? listed : []);
    const keys = new Set(here.map((entry) => entry.body.key));
    const copies = older.filter(
      (entry) => !keys.has(entry.body.key) && !copied.has(entry.body.key),
    );
    // Only those older still holds, so COPIED never outgrows it.
    const toCopy = older.map((entry) => entry.body.key);
    const same =
      toCopy.length === copied.size && toCopy.every((key) => copied.has(key));
    return {
      entries: oldestFirst(copies, here),
      ...(same ? {} : { toCopy }),
    };
  }

  /** Change what is kept, from what storage holds now. False if the browser
   * would not store it (full, or storage forbidden). */
  private update(change: (entries: Entry[]) => Entry[]): boolean {
    const { entries, toCopy } = this.readAll();
    const next = change(entries);
    const kept = writeStored(this.storage, STORAGE_KEY, next);
    // Once kept here: listed copied first, a batch would be in neither.
    // Not listed, one forgotten here is copied again, which only shows it
    // until a catalogue does once more.
    if (kept && toCopy !== undefined) {
      writeStored(this.storage, COPIED, toCopy);
    }
    // Shown as stored, so the page never shows what was not kept.
    this.show(kept ? next : this.read());
    return kept;
  }

  private settle(key: string, changes: Partial<Entry>): void {
    this.update((entries) => {
      const next = entries.map((entry) =>
        entry.body.key === key ? { ...entry, ...changes } : entry,
      );
      const saved = next.filter((entry) => entry.state === "saved");
      const dropped = new Set(saved.slice(0, -SAVED_MOST));
      return next.filter((entry) => !dropped.has(entry));
    });
  }

  /** What the list shows: all but the saved batches dismissed. */
  shown(): Entry[] {
    return this.entries.value.filter(
      (entry) => onList(entry) && !this.forgottenElsewhere.has(entry.body.key),
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
    for (const key of inCatalogue) {
      this.forgottenElsewhere.delete(key);
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
   * Give this batch its key, keep it, and start sending it. The key is made
   * here, before anything is sent, and kept with the batch for every
   * attempt. False means the browser would not store it: nothing was sent,
   * and the caller still holds the batch.
   */
  queue(draft: Draft, what: string, now = Date.now()): boolean {
    const entry = newEntry({ ...draft, key: uuid() }, what, now);
    const kept = this.update((entries) => [...entries, entry]);
    if (kept) {
      void this.send();
    }
    return kept;
  }

  /** Take a settled batch off the list: a refused one is forgotten, a
   * saved one once a catalogue shows it (forgetShown). */
  dismiss(key: string): void {
    this.update((entries) =>
      entries.flatMap((entry) => {
        if (entry.body.key !== key || entry.state === "waiting") {
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
    if (this.sending) {
      return;
    }
    this.sending = true;
    clearTimeout(this.retry);
    const tried = new Set<string>();
    try {
      for (;;) {
        const waiting = this.read().filter((each) => each.state === "waiting");
        // An open package an earlier batch still waiting makes, takes from
        // or reports: a later one naming it waits too, in order, or the
        // server, which checks what is left on it and where, would refuse
        // it for good.
        const before = new Set<string>();
        const entry = waiting.find((each) => {
          const named = keysNamed(each.body);
          const ready =
            !tried.has(each.body.key) && !named.some((key) => before.has(key));
          for (const key of [...keysMade(each.body), ...named]) {
            before.add(key);
          }
          return ready;
        });
        if (entry === undefined) {
          return;
        }
        tried.add(entry.body.key);
        if ((await this.sendOne(entry)) === "stop") {
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

  /** Whether the round goes on to the next batch. */
  private async sendOne(entry: Entry): Promise<"next" | "stop"> {
    const key = entry.body.key;
    const reply = await check(
      this.api.POST("/api/transactions", { body: entry.body }),
      {
        200: Saved,
        201: Saved,
        403: DeviceRefusedReply,
        409: KeyReusedReply,
        422: ValidationFailed,
      },
    );
    switch (reply.status) {
      case 200:
      case 201:
        this.settle(key, {
          state: "saved",
          waitingFor: null,
          note: null,
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
                  note: `${NOT_REGISTERED[reply.body.reason]} Register it to send this batch.`,
                }
              : each,
          ),
        );
        return "stop";
      case 409:
        this.settle(key, {
          state: "refused",
          waitingFor: null,
          note: "Not saved: its key was already used for a different batch. Please report this.",
        });
        return "next";
      case 422:
        this.settle(key, {
          state: "refused",
          waitingFor: null,
          note: `Not saved. ${problems(reply.body.detail, entry.body)}`,
        });
        return "next";
      case "unexpected": {
        // Something about this batch, perhaps: the others still go. Never
        // dropped; tried again each round.
        const failures = entry.failures + 1;
        telemetry.record({ kind: "outbox_retry", reason: "unexpected" });
        this.settle(key, {
          waitingFor: "network",
          failures,
          note:
            failures >= REPORT_AFTER
              ? `The inventory keeps failing on this batch (${reply.code}, ${failures} tries). It is kept and tried again; please report this.`
              : `The inventory answered unexpectedly (${reply.code}). Trying again shortly.`,
        });
        this.later();
        return "next";
      }
      default:
        telemetry.record({
          kind: "outbox_retry",
          reason: reply.status === "unavailable" ? "unavailable" : "no_answer",
        });
        this.settle(key, {
          waitingFor: "network",
          note:
            reply.status === "unavailable"
              ? "The inventory can't save just now. Trying again shortly."
              : "No answer from the inventory. Trying again shortly.",
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
      const here = new Set(stored.map((entry) => entry.body.key));
      for (const entry of this.entries.value) {
        if (entry.state === "saved" && !here.has(entry.body.key)) {
          this.forgottenElsewhere.set(entry.body.key, entry);
        }
      }
      this.show(stored);
    };
    // An older build's tab too, open during a deploy: its batches show here.
    const stopFollowing = onStoredChange([STORAGE_KEY, BEFORE], follow);
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

/** Both, oldest first; `rest` alone, as it is, if `extra` is empty. */
function oldestFirst(extra: Entry[], rest: Entry[]): Entry[] {
  return extra.length === 0
    ? rest
    : [...extra, ...rest].sort((a, b) => a.queuedAt - b.queuedAt);
}

/** Stored entries, minus any that cannot be read: one bad entry loses one
 * batch, not every batch on the device. */
function parsed(stored: unknown): Entry[] {
  if (!Array.isArray(stored)) {
    return [];
  }
  return stored.flatMap((entry) => {
    const result = v.safeParse(Entry, entry);
    return result.success ? [result.output] : [];
  });
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

/** A batch as first kept: waiting, and not yet tried. */
export function newEntry(body: Body, what: string, now = Date.now()): Entry {
  return {
    body,
    what,
    queuedAt: now,
    state: "waiting",
    waitingFor: null,
    note: null,
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
