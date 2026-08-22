/**
 * Batches this device could not send, kept until it can.
 *
 * Decision 0011 section 6 deferred this and named a service worker. It is not
 * one, and the reason is in that section's amendment: Background Sync is
 * Chromium-only, so on the platform decision 0011 says to test on it would
 * wake up for nobody. What replaces it is a queue in `localStorage` that the
 * page drains -- when the app opens, when the browser fires `online`, when a
 * save gets through, and when the volunteer presses the button.
 *
 * ## What "exactly once" rests on
 *
 * Not on this file. A queued batch keeps the body it was going to be sent
 * with, idempotency key and all, and that key was minted when the cart opened
 * (`cartState.ts`) -- so every attempt is byte-identical, including the actor
 * the server scopes the key to. The server holds a unique index on
 * `(actor, idempotency_key)` and answers a second arrival with the first
 * transaction and a 200 rather than recording anything
 * (`StockTransactionCreateView` in backend/src/inventory/views.py).
 *
 * So what this side promises is *at least* once: an entry is marked done only
 * once the server has answered about it, so a reply lost on the way back
 * leaves the batch waiting and it goes again. Exactly once is what the key
 * buys on top of that, and it is why nothing here may ever mint a new one for
 * a batch already queued.
 *
 * Two things it does not promise, both worth knowing:
 *
 * - **A replayed batch is recorded when it is replayed**, not when it was
 *   filled. That is docs/decisions/0018-occurred-at-is-the-server-clock.md,
 *   which names this queue and declines to solve it.
 * - **Two tabs are two copies.** Each holds the queue it last read, so the
 *   second to write wins in storage. The ledger is unharmed either way -- the
 *   key is what protects it -- but a batch can be sent by the tab that still
 *   remembers it after another tab's write dropped it from storage.
 */
import { apiPost, asApiError } from "../api/client";
import { isRecordedBatch } from "../api/types";
import { isNumber, isText, matches, read, type Shape, write } from "../storage";
import { recordedInWords } from "./recorded";

/** Versioned, for the reason cartStorage's key is. */
export const STORAGE_KEY = "nycmesh-inventory.outbox.v1";

/** How a queued batch ended, once the server has answered about it. */
export interface QueuedOutcome {
  /** Whether the ledger has it. False is a refusal, and the end of the road. */
  recorded: boolean;
  detail: string;
}

export interface QueuedBatch {
  /** The cart's idempotency key: what identifies this batch here and there. */
  key: string;
  /** The request body, verbatim, because a retry must not differ in any way. */
  body: Record<string, unknown>;
  /** What is in it, in words, so two waiting batches can be told apart. */
  what: string;
  queuedAt: number;
  /** Null while it is still waiting to be sent. */
  outcome: QueuedOutcome | null;
}

const isFlag = (value: unknown): boolean => typeof value === "boolean";
// An array is an object to `typeof`, and a body posted as one is a request the
// endpoint has no reading of.
const isBody = (value: unknown): boolean =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const isSettled = (value: unknown): boolean =>
  value === null || matches(value, { recorded: isFlag, detail: isText });

/**
 * What a stored entry has to be to be sent.
 *
 * Stricter than it looks worth being, because the thing being restored is a
 * write: an entry whose `body` came back as a string would be posted as one,
 * and an entry with no `key` is a batch nothing can identify as a replay.
 */
const QUEUED_SHAPE: Shape = {
  key: isText,
  body: isBody,
  what: isText,
  queuedAt: isNumber,
  outcome: isSettled,
};

/**
 * What is stored, minus whatever cannot be read.
 *
 * Entry by entry rather than all or nothing. One unreadable row is one batch
 * lost; taking the queue with it loses every other batch on the device, and
 * the next write puts the empty array back over them.
 */
function stored(): QueuedBatch[] {
  const entries = read(STORAGE_KEY, Array.isArray) ?? [];
  return entries.filter((entry: unknown): entry is QueuedBatch => matches(entry, QUEUED_SHAPE));
}

/**
 * Held in memory as well, and handed out by reference: `useSyncExternalStore`
 * compares snapshots by identity, so a fresh array per read would re-render
 * without end.
 */
let held: QueuedBatch[] | null = null;
const listeners = new Set<() => void>();

/** Everything queued, oldest first, settled entries included. */
export function outbox(): QueuedBatch[] {
  held ??= stored();
  return held;
}

/** How many are still waiting for the network. */
export function waiting(batches: QueuedBatch[]): QueuedBatch[] {
  return batches.filter((batch) => batch.outcome === null);
}

/**
 * The new queue, in memory and on the device. False if the device would not
 * take it -- see `queueBatch`, which is the only caller that can act on that.
 */
function replace(batches: QueuedBatch[]): boolean {
  held = batches;
  const kept = write(STORAGE_KEY, batches);
  for (const listener of listeners) {
    listener();
  }
  return kept;
}

export function subscribeToOutbox(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Take this batch off the volunteer's hands, and say whether it was taken.
 *
 * Keyed on the idempotency key, so pressing Save twice with no signal queues
 * one batch rather than two -- which the server would absorb anyway, but a
 * volunteer looking at two identical rows has no way to know that.
 *
 * False means the device would not store it: a full or forbidden
 * `localStorage`. The caller still holds the batch at that point and must go
 * on holding it, because this queue would lose it at the next reload and say
 * nothing.
 */
export function queueBatch(
  batch: Pick<QueuedBatch, "key" | "body" | "what">,
  at: number = Date.now(),
): boolean {
  const before = outbox();
  const others = before.filter((queued) => queued.key !== batch.key);
  if (replace([...others, { ...batch, queuedAt: at, outcome: null }])) {
    return true;
  }
  // Put the queue back as it was. The caller still holds this batch and is
  // about to be told so; leaving a copy here that the next reload loses would
  // show it in two places and lose it from both.
  replace(before);
  return false;
}

/** Forget one entry: dismissing news, or abandoning a batch. */
export function discardBatch(key: string): void {
  replace(outbox().filter((queued) => queued.key !== key));
}

/**
 * The 4xx that are about this moment rather than about this batch.
 *
 * 408 is a timeout and 429 is this API's own throttle
 * (backend/src/inventory/throttling.py). 401 and 403 are a session that has
 * lapsed while the phone was in a pocket: the volunteer signs in again and the
 * batch is still theirs, so treating it as doomed would destroy work over an
 * expiry, which is the one thing this queue exists to prevent.
 */
const NOT_NOW = new Set([401, 403, 408, 429]);

/**
 * Whether this refusal is one no amount of retrying gets past.
 *
 * The batch endpoint's own refusals are here -- a 400 listing bad lines, a 409
 * saying the batch is not the act it claims. Neither is a connection problem,
 * and a queue that kept trying would post the same doomed batch every time the
 * app opened for the rest of the device's life.
 */
function isFinal(status: number): boolean {
  return status >= 400 && status < 500 && !NOT_NOW.has(status);
}

/**
 * What to say about a batch the ledger now has. The words are recorded.ts's.
 *
 * An answer that is not the shape this reads still went in -- the status said
 * so -- so it is described as a batch with nothing to advise about rather than
 * being claimed as a failure.
 */
function describe(answer: unknown): string {
  return recordedInWords(isRecordedBatch(answer) ? answer.warnings : []);
}

/** One attempt: how it ended, or null if this is a moment to wait out. */
async function attempt(batch: QueuedBatch): Promise<QueuedOutcome | null> {
  let answer: unknown;
  try {
    // A 200 here is the server saying it already had this batch, which is a
    // success and the whole point of the key. `apiPost` resolves for both.
    answer = await apiPost<unknown>("/api/stock/transactions", batch.body);
  } catch (error: unknown) {
    const refused = asApiError(error);
    return isFinal(refused.status) ? { recorded: false, detail: refused.message } : null;
  }
  // Outside the catch above on purpose: everything from here on is this app
  // reading a successful answer, and a throw in it is a bug in this app. Read
  // inside the try it would be caught as a status of 0, mistaken for having no
  // signal, and would stall the whole run on a batch the ledger already has.
  return { recorded: true, detail: describe(answer) };
}

function settle(key: string, outcome: QueuedOutcome): void {
  replace(outbox().map((queued) => (queued.key === key ? { ...queued, outcome } : queued)));
}

/** True while a run is in flight, so two triggers do not double up. */
let sending = false;
/** A trigger that arrived during a run, owed a pass of its own. */
let asked = false;

/**
 * One pass over what is waiting, oldest first.
 *
 * The queue is read again before every attempt rather than listed once at the
 * top: a run outlives several awaits, and a batch the volunteer discarded in
 * the meantime must not still be posted into an append-only ledger.
 *
 * `attempted` is why that re-reading loop is known to end. Every turn either
 * returns or adds a key it will never take again, so the pass is over in at
 * most one attempt per key that was ever in the queue -- and the proof is
 * here, in the loop, rather than resting on `settle` having taken the entry
 * out of `waiting` afterwards. In a file whose subject is posting a batch
 * exactly once, a loop that posts is worth its own guarantee.
 *
 * The one thing it changes: a batch queued under a key this pass has already
 * tried waits for the next trigger instead of going now. That is the same
 * batch the server would answer as a replay anyway, so nothing is lost by
 * letting it wait.
 */
async function drain(): Promise<void> {
  const attempted = new Set<string>();
  for (;;) {
    const next = waiting(outbox()).find((batch) => !attempted.has(batch.key));
    if (next === undefined) {
      return;
    }
    attempted.add(next.key);
    const outcome = await attempt(next);
    if (outcome === null) {
      return;
    }
    settle(next.key, outcome);
  }
}

/**
 * Send everything waiting, oldest first.
 *
 * One run at a time. The triggers overlap by design -- opening the app while
 * the browser also fires `online` is ordinary -- and two runs would post the
 * same batch twice. The server would absorb that, but the second attempt
 * would answer against an entry the first has already settled.
 *
 * A trigger that arrives during a run is owed a pass rather than dropped: a
 * save that gets through while the queue is draining is news the run may
 * already be past, and the batch it just queued would then sit there until
 * something else happened to ask.
 *
 * A refusal on the merits settles that one batch and the run carries on; one
 * bad batch must not hold up the good ones behind it. Anything else -- nothing
 * at the other end, a 500, a throttle -- stops the pass where it stands, since
 * whatever is wrong is not going to be different for the next batch in line.
 */
export async function sendOutbox(): Promise<void> {
  if (sending) {
    asked = true;
    return;
  }
  sending = true;
  try {
    do {
      asked = false;
      await drain();
    } while (asked);
  } finally {
    sending = false;
  }
}

/**
 * Lets a test start again from nothing. Not used by the app.
 *
 * From nothing means all four: what is held here, what is on the device, the
 * run flags, and the listeners an unmounted panel left behind. A reset that
 * emptied only the first would hand the next test the previous one's queue
 * back the moment anything read it.
 */
export function forgetOutbox(): void {
  held = null;
  sending = false;
  asked = false;
  listeners.clear();
  write(STORAGE_KEY, []);
}
