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
import { onStoredChange, readStored, writeStored } from "./stored";
import { telemetry } from "./telemetry";

/** Versioned: a later build that changes the shape reads the one before.
 * v2's lines may name a package (docs/decisions/0020), which v1's reader
 * would drop if it wrote them back, so a tab of an older build keeps to
 * v1's. */
export const STORAGE_KEY = "inventory.outbox.v2";
const BEFORE = "inventory.outbox.v1";
/** How long to wait before trying again after no answer. */
export const RETRY_MS = 30_000;
/** Unexpected answers to one batch before its note asks for a report. */
export const REPORT_AFTER = 5;
/** Saved batches kept on show, newest first, so a volunteer can check. */
const SAVED_KEPT = 10;

const nullableId = v.exactOptional(v.nullable(id));

/** A batch as POST /api/transactions takes it. */
export const BatchBody = v.object({
  key: v.pipe(v.string(), v.uuid()),
  kind: v.picklist(["move", "receipt", "install", "open"]),
  volunteer_id: id,
  from_location_id: nullableId,
  to_location_id: nullableId,
  install_number: nullableId,
  lines: v.array(
    v.object({
      item_id: id,
      quantity: v.union([v.number(), v.string()]),
      /** Sealed packages of it, kept sealed; absent (an older page's): loose. */
      package_id: nullableId,
      /** A move's taken from an open package: all of it, moved whole. */
      whole: v.exactOptional(v.nullable(v.boolean())),
      /** An opening's: the key of the open package it makes, followed;
       * another's, the open package it is taken from. */
      open_package_key: v.exactOptional(
        v.nullable(v.pipe(v.string(), v.uuid())),
      ),
    }),
  ),
});

export type Body = Schemas["Batch"];
/** A batch before the outbox gives it its key. */
export type Draft = Omit<Body, "key">;

const Entry = v.object({
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
});

export type Entry = v.InferOutput<typeof Entry>;

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

  constructor(
    private readonly api: Api,
    private readonly storage: Storage = localStorage,
  ) {
    this.entries.value = this.read();
  }

  /** What storage holds, minus any entry that cannot be read: one bad entry
   * loses one batch, not every batch on the device. */
  private read(): Entry[] {
    // Until this build first writes, what an older one kept, to send.
    const stored =
      readStored(this.storage, STORAGE_KEY) ?? readStored(this.storage, BEFORE);
    if (!Array.isArray(stored)) {
      return [];
    }
    return stored.flatMap((entry) => {
      const parsed = v.safeParse(Entry, entry);
      return parsed.success ? [parsed.output] : [];
    });
  }

  /** Change what is kept, from what storage holds now. False if the browser
   * would not store it (full, or storage forbidden). */
  private update(change: (entries: Entry[]) => Entry[]): boolean {
    const next = change(this.read());
    const kept = writeStored(this.storage, STORAGE_KEY, next);
    // Shown as stored, so the page never shows what was not kept.
    this.entries.value = kept ? next : this.read();
    return kept;
  }

  private settle(key: string, changes: Partial<Entry>): void {
    this.update((entries) => {
      const next = entries.map((entry) =>
        entry.body.key === key ? { ...entry, ...changes } : entry,
      );
      const saved = next.filter((entry) => entry.state === "saved");
      const dropped = new Set(saved.slice(0, -SAVED_KEPT));
      return next.filter((entry) => !dropped.has(entry));
    });
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

  /** Forget a settled batch. */
  dismiss(key: string): void {
    this.update((entries) =>
      entries.filter(
        (entry) => entry.body.key !== key || entry.state === "waiting",
      ),
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
        // An open package an opening still waiting makes: a batch taking
        // from it waits too, or the server would refuse it for good.
        const unmade = new Set(waiting.flatMap((each) => keysMade(each.body)));
        const entry = waiting.find(
          (each) =>
            !tried.has(each.body.key) &&
            !keysNamed(each.body).some((key) => unmade.has(key)),
        );
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
    const stopFollowing = onStoredChange(STORAGE_KEY, () => {
      this.entries.value = this.read();
    });
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

/** The open packages an opening makes, by the keys the page gave them. */
function keysMade(body: Body): string[] {
  return body.kind !== "open"
    ? []
    : body.lines.flatMap((line) => line.open_package_key ?? []);
}

/** The open packages another batch takes from. */
function keysNamed(body: Body): string[] {
  return body.kind === "open"
    ? []
    : body.lines.flatMap((line) => line.open_package_key ?? []);
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
