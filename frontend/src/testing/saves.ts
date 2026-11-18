/**
 * What the scan page finds kept in this browser, for tests: batches in
 * this build's outbox, and saves an older build left. Each builder gives a
 * plain move with every field filled; a test names only the fields its
 * own data changes.
 */
import { BUILD } from "../shared/build";
import {
  type Body,
  type Entry,
  STORAGE_KEY as OUTBOX_KEY,
} from "../shared/outbox";

/** The key a batch is kept under, unless a test gives it another. */
export const KEY = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
/** A second batch's key. */
export const OTHER_KEY = "6f1e2d3c-4b5a-4968-8776-5a4b3c2d1e0f";

/** A batch's body: volunteer 1 moving two boxes of radios (item 100,
 * package 1000) from the hub (10) to the van (12), under KEY. Any field
 * given replaces the default. */
export function moveBody(fields: Partial<Body> = {}): Body {
  return {
    key: KEY,
    kind: "move",
    volunteer_id: 1,
    from_location_id: 10,
    to_location_id: 12,
    lines: [{ item_id: 100, quantity: "8", package_id: 1000 }],
    ...fields,
  };
}

/** A batch as this build's outbox keeps it: moveBody(), from the hub to
 * the van, queued at 1 by this build, and waiting. Any field given
 * replaces the default. */
export function outboxEntry(fields: Partial<Entry> = {}): Entry {
  return {
    body: moveBody(),
    summary: { from: "Hub", to: "Van" },
    queuedAt: 1,
    build: BUILD,
    state: "waiting",
    waitingFor: null,
    reason: null,
    transactionId: null,
    failures: 0,
    ...fields,
  };
}

/** Keeps `entries` under this build's outbox key, stamped by `build`, as
 * a tab of that build would. */
export function storeOutbox(entries: unknown[], build = BUILD) {
  keep(OUTBOX_KEY, { build, entries });
}

/** A save of `body` in the list builds before v4 kept, with only what this
 * build needs to read one: the body, waiting. Older builds kept more
 * (queuedAt, what, install_number and others, scan/older.ts says which): a test
 * adds those its own data holds. Any field given replaces the default or is
 * added. */
export function olderSave(body: object, fields: Record<string, unknown> = {}) {
  return { body, state: "waiting", ...fields };
}

/** Keeps `value` under `key` as JSON, as the build that wrote it would:
 * older builds' saves and carts are kept this way. */
export function keep(key: string, value: unknown) {
  localStorage.setItem(key, JSON.stringify(value));
}
