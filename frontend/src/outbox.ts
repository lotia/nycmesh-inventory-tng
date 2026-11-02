/**
 * Batches saved on this browser, kept until the server has answered about
 * them (docs/decisions/0002, 0009).
 *
 * Exactly once rests on the server, not here: each batch carries the
 * idempotency key its cart was given, every attempt sends the same body, and
 * the ledger answers a repeat with the first transaction. So the outbox
 * promises at least once: a batch settles only when the server has answered
 * about it, and nothing here ever gives a batch a new key.
 *
 * Kept in localStorage, which outlives the tab and does not depend on the
 * device cookie, so registering loses no work. Each change rereads storage
 * first, so two tabs do not drop each other's batches.
 */
import { signal } from "@preact/signals";
import * as v from "valibot";
import { type Api, check, makeApi, type Schemas } from "./api/client";
import {
  DeviceRefusedReply,
  KeyReusedReply,
  Saved,
  ValidationFailed,
} from "./api/replies";

/** Versioned: a later build that changes the shape reads this one too. */
export const STORAGE_KEY = "inventory.outbox.v1";
/** How long to wait before trying again after no answer. */
export const RETRY_MS = 30_000;
/** Saved batches kept on show, newest first, so a volunteer can check. */
const SAVED_KEPT = 10;

const id = v.pipe(v.number(), v.integer());
const nullableId = v.exactOptional(v.nullable(id));

/** A batch as POST /api/transactions takes it. */
export const BatchBody = v.object({
  key: v.pipe(v.string(), v.uuid()),
  kind: v.picklist(["move", "receipt", "install"]),
  volunteer_id: id,
  from_location_id: nullableId,
  to_location_id: nullableId,
  install_number: nullableId,
  lines: v.array(
    v.object({ item_id: id, quantity: v.union([v.number(), v.string()]) }),
  ),
});

export type Body = Schemas["Batch"];

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
});

export type Entry = v.InferOutput<typeof Entry>;

const NOT_REGISTERED: Record<"unregistered" | "revoked", string> = {
  unregistered: "Waiting for this browser to be registered.",
  revoked:
    "This browser's registration was withdrawn. Register it again to send this batch.",
};

/** A 422's problems, in words a volunteer can act on. */
function problems(detail: v.InferOutput<typeof ValidationFailed>["detail"]) {
  return detail
    .map(({ loc, msg }) => {
      const line = loc[0] === "body" && loc[1] === "lines" ? loc[2] : undefined;
      const words = typeof msg === "string" ? msg : "Not accepted";
      return typeof line === "number" ? `Line ${line + 1}: ${words}` : words;
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
    let stored: unknown;
    try {
      stored = JSON.parse(this.storage.getItem(STORAGE_KEY) ?? "[]");
    } catch {
      return [];
    }
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
    this.entries.value = next;
    try {
      this.storage.setItem(STORAGE_KEY, JSON.stringify(next));
      return true;
    } catch {
      return false;
    }
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
   * Keep this batch and start sending it. False means the browser would not
   * store it: the caller still holds the batch and must keep holding it.
   * The same key twice is one batch, so a double tap queues once.
   */
  queue(body: Body, what: string, now = Date.now()): boolean {
    const kept = this.update((entries) =>
      entries.some((entry) => entry.body.key === body.key)
        ? entries
        : [
            ...entries,
            {
              body,
              what,
              queuedAt: now,
              state: "waiting",
              waitingFor: null,
              note: null,
              transactionId: null,
            },
          ],
    );
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
   * Send what is waiting, oldest first, one at a time. Stops at the first
   * batch that cannot go now, since the rest would meet the same.
   */
  async send(): Promise<void> {
    if (this.sending) {
      return;
    }
    this.sending = true;
    clearTimeout(this.retry);
    try {
      for (;;) {
        const entry = this.read().find((each) => each.state === "waiting");
        if (entry === undefined || !(await this.sendOne(entry))) {
          return;
        }
      }
    } finally {
      this.sending = false;
    }
  }

  /** True when the server has settled it, so the next can go. */
  private async sendOne(entry: Entry): Promise<boolean> {
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
        });
        return true;
      case 403:
        // Sent again once the device registers (registration.ts), or when
        // the page next opens.
        this.settle(key, {
          waitingFor: "registration",
          note: NOT_REGISTERED[reply.body.reason],
        });
        return false;
      case 409:
        this.settle(key, {
          state: "refused",
          waitingFor: null,
          note: "Not saved: its key was already used for a different batch. Please report this.",
        });
        return true;
      case 422:
        this.settle(key, {
          state: "refused",
          waitingFor: null,
          note: `Not saved. ${problems(reply.body.detail)}`,
        });
        return true;
      default:
        this.settle(key, {
          waitingFor: "network",
          note:
            reply.status === "unexpected"
              ? `The inventory answered unexpectedly (${reply.code}). Trying again shortly.`
              : "No answer from the inventory. Trying again shortly.",
        });
        this.retry = setTimeout(() => void this.send(), RETRY_MS);
        return false;
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
    const changed = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY) {
        this.entries.value = this.read();
      }
    };
    target.addEventListener("online", send);
    target.document.addEventListener("visibilitychange", visible);
    target.addEventListener("storage", changed);
    return () => {
      target.removeEventListener("online", send);
      target.document.removeEventListener("visibilitychange", visible);
      target.removeEventListener("storage", changed);
      clearTimeout(this.retry);
    };
  }
}

let shared: Outbox | undefined;

/** The outbox every page on this browser uses. */
export function sharedOutbox(): Outbox {
  shared ??= new Outbox(makeApi());
  return shared;
}
