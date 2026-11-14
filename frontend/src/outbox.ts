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
 * newer build's, when the tab is too old. A cart filled from a save an
 * older build left is sent under that save's key (cart.ts, batchKey): if
 * that build's tab saved it first, the server takes it once.
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
 * its entries each with the build that queued them, and says which were
 * sent under an older save's key (putBack). */
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

/** A batch as POST /api/transactions takes it. */
export const BatchBody = v.object({
  key: BatchKey,
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
       * since. The server requires it with package_id, but a build from
       * before it was sent kept lines without: read, so the server's
       * refusal names the line, never dropped unseen (inventory-tng-1p8). */
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
  /** The build that queued it, named when it is sent (build.HEADER). */
  build: v.string(),
  /** too_old: held back by the server as from a build too old, and listed
   * with what older builds left (older.ts), to put back in the cart. */
  state: v.picklist(["waiting", "saved", "refused", "too_old"]),
  /** While waiting: what it waits for. */
  waitingFor: v.nullable(v.picklist(["network", "registration"])),
  /** Why it was refused, or why it waits, in words. */
  note: v.nullable(v.string()),
  transactionId: v.nullable(id),
  /** Unexpected answers to this batch in a row. */
  failures: v.pipe(v.number(), v.integer()),
  /** Saved, and dismissed: off the list, kept until a catalogue shows it. */
  dismissed: v.exactOptional(v.literal(true)),
  /** Sent under the key of a save an older build left, put back in the
   * cart (older.ts): that build's tab may have saved it first. */
  putBack: v.exactOptional(v.literal(true)),
});

export type Entry = v.InferOutput<typeof Entry>;

/** What STORAGE_KEY holds: the batches, and the build that last wrote them.
 * Each entry is read on its own (parsed). */
export const Kept = v.object({
  build: v.string(),
  entries: v.array(v.unknown()),
});

/** A batch whose key the inventory holds for another (409 key_reused). */
export const KEY_REUSED =
  "Not recorded: its key was already used for a different batch. Please report this.";

/** A save put back in the cart, then changed, whose older build's tab
 * recorded it first, as it was (409 key_reused), as kept; every note the
 * page shows for it opens with it (Ali, inventory-tng-93z and
 * inventory-tng-wyq, 'One wording for a put-back save refused for its
 * key'). */
export const RECORDED_ELSEWHERE =
  "Not recorded: another tab recorded this cart before your changes.";

/** Said after RECORDED_ELSEWHERE until the page is reloaded, beside the
 * page's Reload, so what it shows follows (Ali, inventory-tng-93z,
 * question 4, C): words for the page, never kept. */
export const RELOAD_TO_CONTINUE = "Reload the page to continue.";

/** What every build has kept of a save put back, changed and refused as
 * another tab recorded it first: this one, then those before. */
const RECORDED_ELSEWHERE_KEPT: ReadonlySet<string> = new Set([
  RECORDED_ELSEWHERE,
  // Kept by builds before, as they were, whatever the page says now:
  // inventory-tng-wyq's first, and inventory-tng-93z's, with the reload's
  // words.
  "Not recorded: another tab recorded this first.",
  "Not recorded: another tab recorded this first. Reload the page to continue.",
  "Not saved: another tab saved it first, as it was before your changes. Check what was saved, then save your changes again.",
]);

/** Whether `note` says another tab recorded the save first, as any build
 * kept it: what it held is recorded, but not the changes since, so it
 * never goes in a cart again, which would record it twice (Ali,
 * inventory-tng-wyq, question c). */
export function recordedElsewhere(note: string | null): boolean {
  return RECORDED_ELSEWHERE_KEPT.has(note ?? "");
}

/** What the page says of one recordedElsewhere, once nothing is left to
 * do but discard it (Ali, inventory-tng-wyq, question c): words for the
 * page, never kept. */
export const SCAN_AGAIN_CHANGED = `${RECORDED_ELSEWHERE} Scan again only what you changed.`;

/** What every build has said of a batch refused for its key: this one,
 * then those before, whose words an older key may still hold. */
export const REFUSED_FOR_KEY: ReadonlySet<string> = new Set([
  KEY_REUSED,
  ...RECORDED_ELSEWHERE_KEPT,
  "Not saved: its key was already used for a different batch. Please report this.",
]);

/** A batch waiting, after no answer: said as kept while the browser is
 * online. */
export const NO_ANSWER = "No answer from the inventory. Trying again shortly.";

/** That the browser says it is offline (Ali, inventory-tng-wyq,
 * 'Offline'): words for the page, never kept. */
export const OFFLINE_NOW = "This device is offline.";
/** NO_ANSWER's words while the browser says it is offline. */
export const OFFLINE = `${OFFLINE_NOW} Sending when it's back.`;

/** Before a refused batch's problems (422), as this build says it and as
 * builds before did: shown in bold, the problems after it not. */
const NOT_RECORDED = "Not recorded.";
const REFUSED_LEADS = [NOT_RECORDED, "Not saved."] as const;

/**
 * Words for an alert: a string is the page's own, its numbers counts it
 * wrote, shown in italic; `{ quoted }` is words it did not write, such as
 * a name or the server's, shown as they are (Ali, inventory-tng-93z).
 */
export type Said = readonly (string | { quoted: string })[];

/** `said` as plain text, where nothing is in italic. */
export function wordsOf(said: Said): string {
  return said
    .map((part) => (typeof part === "string" ? part : part.quoted))
    .join("");
}

/** A 422's note (problems, below) as its lead, of REFUSED_LEADS, and its
 * problems: where each names its line or report, the page's words; the
 * rest, the server's. Null if `note` is no such refusal. */
export function refusalOf(
  note: string,
): { lead: string; problems: Said } | null {
  const lead = REFUSED_LEADS.find((each) => note.startsWith(each));
  if (lead === undefined) {
    return null;
  }
  const problems = note
    .slice(lead.length)
    .split(MARKERS)
    .flatMap((part, at) =>
      part === "" ? [] : [at % 2 === 1 ? part : { quoted: part }],
    );
  return { lead, problems };
}

/** A batch's reports, as a 422 names them. */
export const REPORTS = {
  used_up: "Open package said used up",
  lengths: "What is on an open package",
} as const;

/** Where a 422's problem names its line or report (refusalOf). */
const MARKERS = new RegExp(
  `((?:^| )(?:Line \\d+|(?:${Object.values(REPORTS).join("|")}), \\d+): )`,
);

/** A line's package_size refused as a page's bug, as a build from before
 * sizes were sent kept its lines (inventory-tng-1p8), by the server's type:
 * its words are for whoever writes a client, these for the volunteer. */
const SIZE_REFUSED = new Map([
  [
    "package_size_required",
    "A line of a package says the size it was counted in: load the page again, and count it again",
  ],
  [
    "package_size_without_package",
    "A loose line says a package's size: load the page again, and count it again",
  ],
]);

/** The number of a batch's line naming an item, by the item's first place
 * among `itemIds`, its lines' (problems' "Line n"; older.linesSaid). */
export function lineNumbers(
  itemIds: readonly number[],
): (itemId: number) => number {
  const items = [...new Set(itemIds)];
  return (itemId) => items.indexOf(itemId) + 1;
}

/** A 422's problems, in words a volunteer can act on. A line of the cart
 * may be sent as several, one for each package and one loose (cart.ts,
 * toBatch), so a line is named as the cart's: by its item's place in it. */
function problems(
  detail: v.InferOutput<typeof ValidationFailed>["detail"],
  body: Body,
) {
  const lineOf = lineNumbers(body.lines.map((line) => line.item_id));
  return detail
    .map(({ loc, msg, type }) => {
      const line = loc[0] === "body" && loc[1] === "lines" ? loc[2] : undefined;
      const words =
        (loc[3] === "package_size" ? SIZE_REFUSED.get(type) : undefined) ??
        (typeof msg === "string" ? msg : "Not accepted");
      // A report's, by its place in the batch's list of them.
      const report =
        typeof loc[1] === "string" && Object.hasOwn(REPORTS, loc[1])
          ? REPORTS[loc[1] as keyof typeof REPORTS]
          : undefined;
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
        : `Line ${lineOf(sent.item_id)}: ${words}`;
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
    /** This build, and whether a newer one is out. */
    readonly builds = new Builds(undefined, storage),
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
    // Shown as stored, so the page never shows what was not kept.
    this.show(kept ? next : this.read());
    return kept;
  }

  /** An answer to the batch `key`, applied to it while it still waits, or
   * a save whatever became of it: another tab may have sent it meanwhile,
   * and one saved is never refused or listed to save again. */
  private settle(key: string, changes: Partial<Entry>): void {
    this.update((entries) => {
      const next = entries.map((entry) =>
        entry.body.key === key &&
        (entry.state === "waiting" || changes.state === "saved")
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
   * attempt; or it is `putBack`, the key of a save an older build left
   * that filled the cart, so the server takes that save once, whichever
   * tab sends it first. False means the browser would not store it:
   * nothing was sent, and the caller still holds the batch.
   */
  queue(draft: Draft, what: string, putBack?: string): boolean {
    const entry = newEntry(
      { ...draft, key: putBack ?? uuid() },
      what,
      this.builds.build,
    );
    if (putBack !== undefined) {
      entry.putBack = true;
    }
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
    // Too old, nothing this tab sends is taken: kept until it reloads.
    if (this.sending || this.builds.newer.value === "too_old") {
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
    const call = this.api.POST("/api/transactions", {
      // As the build that queued it, which the server judges it by.
      params: { header: { [HEADER]: entry.build } },
      body: entry.body,
    });
    // A save, and a 409 build_too_old, name the server's build
    // (page_builds.py), which may be newer; its other answers name none.
    call.then(
      ({ response }) =>
        this.builds.heard(response.headers.get(HEADER), "server"),
      () => {},
    );
    const reply = await check(call, {
      200: Saved,
      201: Saved,
      403: DeviceRefusedReply,
      409: v.variant("reason", [KeyReusedReply, BuildTooOldReply]),
      422: ValidationFailed,
    });
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
        if (reply.body.reason === "build_too_old") {
          if (
            entry.build !== this.builds.build &&
            !this.builds.isNewer(entry.build)
          ) {
            // An older build's, kept under this key: too old itself, not
            // this tab. Listed with what older builds left (older.ts).
            this.settle(key, {
              state: "too_old",
              waitingFor: null,
              note: null,
            });
            return "next";
          }
          // This tab's own, or a newer one's: this tab is too old. Kept,
          // waiting: a newer build of the page lists it (older.ts).
          this.builds.learn("too_old");
          this.settle(key, {
            waitingFor: null,
            note: MUST_UPGRADE,
          });
          return "stop";
        }
        // A save put back: an older build's tab saved it first, as it
        // was. This, changed since, is not recorded, and says so.
        this.settle(key, {
          state: "refused",
          waitingFor: null,
          note: entry.putBack ? RECORDED_ELSEWHERE : KEY_REUSED,
        });
        return "next";
      case 422:
        this.settle(key, {
          state: "refused",
          waitingFor: null,
          note: `${NOT_RECORDED} ${problems(reply.body.detail, entry.body)}`,
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
              ? "The inventory can't record this just now. Trying again shortly."
              : NO_ANSWER,
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
