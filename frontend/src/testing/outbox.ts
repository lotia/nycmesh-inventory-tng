/** What the outbox's tests (shared/outbox.*.test.ts) share: batches as
 * it keeps them, and what it holds and sends. Never bundled. */
import { v4 as uuid } from "uuid";
import {
  type Draft,
  type Entry,
  type Outbox,
  STORAGE_KEY,
  type Summary,
} from "../shared/outbox";
import { noteOf, wordsOf } from "../shared/outbox-notes";
import { saved } from "./fetch";
import { outboxEntry, storeOutbox } from "./saves";

/** A move of three of item 5 from place 1 to 2, but for `fields`. */
export function draft(fields: Partial<Draft> = {}): Draft {
  return {
    kind: "move",
    volunteer_id: 1,
    from_location_id: 1,
    to_location_id: 2,
    lines: [{ item_id: 5, quantity: "3" }],
    ...fields,
  };
}

/** A batch's summary, told apart from others' by the place it names. */
export const named = (from: string): Summary => ({ from, to: null });

/** An entry as storage might hold it: draft(), under a key of its own. */
export function entry(fields: Partial<Entry> = {}): Entry {
  return outboxEntry({
    body: { ...draft(), key: uuid() },
    summary: named("a"),
    ...fields,
  });
}

/** Kept as this build keeps them. */
export const store = (...entries: unknown[]) => storeOutbox(entries);

/** Replies in turn, then saves for good. */
export function replies(...queued: (() => Response | Promise<Response>)[]) {
  return () => (queued.shift() ?? saved)();
}

/** Each batch's state, in order. */
export const states = (outbox: Outbox) =>
  outbox.entries.value.map((e) => e.state);

/** What the page says of the first batch's reason, as plain text. */
export function reasonSaid(outbox: Outbox): string | null {
  const reason = outbox.entries.value[0]?.reason ?? null;
  if (reason === null) {
    return null;
  }
  const { bold, plain = [] } = noteOf(reason);
  return wordsOf([...bold, ...plain]);
}

/** The entries kept under the outbox's key. */
export const stored = () =>
  JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null")?.entries;

/** The bodies of the requests `sent`. */
export const bodies = (sent: Request[]) =>
  Promise.all(sent.map((request) => request.clone().json()));
