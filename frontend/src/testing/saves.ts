/**
 * What the scan page finds kept in this browser, for tests: batches in
 * this build's outbox, and saves an older build left. Each builder gives a
 * plain move with every field filled; a test names only the fields its
 * own data changes.
 */

import { type Older, readOlder } from "../scan/older-builds";
import { BUILD } from "../shared/build";
import {
  type Body,
  type Draft,
  type Entry,
  STORAGE_KEY as OUTBOX_KEY,
} from "../shared/outbox";

/** The key a batch is kept under, unless a test gives it another. */
export const KEY = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
/** A second batch's key. */
export const OTHER_KEY = "6f1e2d3c-4b5a-4968-8776-5a4b3c2d1e0f";

let keys = 0;
/** A batch with a key, as the outbox keeps it. */
export function keyed(draft: Draft): Body {
  keys += 1;
  return {
    ...draft,
    key: `00000000-0000-4000-8000-${String(keys).padStart(12, "0")}`,
  };
}

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
 * (queuedAt, what, install_number and others, scan/older-builds.ts says
 * which): a test adds those its own data holds. Any field given replaces
 * the default or is added. */
export function olderSave(body: object, fields: Record<string, unknown> = {}) {
  return { body, state: "waiting", ...fields };
}

/** A batch as an older build kept it: a move of cable, waiting, with
 * every field v3 kept. */
export function olderMove(fields: Record<string, unknown> = {}, body = {}) {
  return olderSave(
    moveBody({ key: KEY, lines: [{ item_id: 101, quantity: "2" }], ...body }),
    {
      what: "Move: 1 item from Hub to Van",
      queuedAt: 1_700_000_000_000,
      waitingFor: null,
      note: null,
      transactionId: null,
      failures: 0,
      ...fields,
    },
  );
}

/** The first save an older build left, as read. */
export function firstOlder(): Older {
  return readOlder()[0] as Older;
}

/** The only save kept, a move of cable but for `body` and `fields`, as
 * read. */
export function onlyOlder(body = {}, fields = {}): Older {
  keep("inventory.outbox.v3", [olderMove(fields, body)]);
  return firstOlder();
}

/** A save of a box retired with none left, cable taken from an open
 * package, a box resized since, two boxes still offered, and a report. */
export function mixedOlder(): Older {
  return onlyOlder({
    lines: [
      { item_id: 100, quantity: "4", package_id: 1004 },
      { item_id: 101, quantity: "12", open_package_key: OTHER_KEY },
      { item_id: 100, quantity: "4", package_id: 1000, package_size: 2 },
      { item_id: 100, quantity: "8", package_id: 1000 },
    ],
    used_up: [OTHER_KEY],
  });
}

/** Keeps `value` under `key` as JSON, as the build that wrote it would:
 * older builds' saves and carts are kept this way. */
export function keep(key: string, value: unknown) {
  localStorage.setItem(key, JSON.stringify(value));
}
