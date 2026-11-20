/**
 * What the page says of a batch in the outbox (outbox.ts): why it was
 * refused, or why it waits, in words made anew from the code kept
 * (noteOf), so in the language spoken when it is shown; each problem a 422
 * names (PROBLEMS); and the heading over what another tab recorded
 * (recordedHeading).
 */
import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import type { Schemas } from "../api/client";
import { MUST_UPGRADE } from "./build";
import { say } from "./i18n";
import {
  type Problem,
  REPORT_AFTER,
  type Reason,
  type Recorded,
  type ReportKind,
} from "./outbox";
import { NOT_REGISTERED } from "./registration";

/** A kind of problem a 422 names: the ledger's (backend
 * scan/batch_schemas.BatchProblem), or a line's package size in FastAPI's
 * own (scan/batch_schemas.SizeProblem). */
export type ProblemKind =
  | Schemas["BatchProblem"]["type"]
  | Schemas["SizeProblem"]["type"];

const isProblem = (kind: string): kind is ProblemKind =>
  Object.hasOwn(PROBLEMS, kind);

/** Each kind of problem in words: the server sends kinds, never words
 * (Ali, inventory-tng-oic, 2026-11-15). Typed by the schema, so a kind new
 * on the server fails the type check until it has words here. A line's
 * package_size refused, as a build from before sizes were sent kept its
 * lines (inventory-tng-1p8), is in FastAPI's own 422, whose words are for
 * whoever writes a client: these are for the volunteer. */
export const PROBLEMS: Record<ProblemKind, MessageDescriptor> = {
  not_found: msg`There is no such row`,
  not_whole: msg`This item is counted in whole units`,
  not_whole_packages: msg`Whole packages only`,
  one_package: msg`Open one package a line: it is followed until used up`,
  open_package_closed: msg`That open package was closed as used up: take from another`,
  open_package_elsewhere: msg`That open package is at another place now`,
  open_package_whole: msg`Moving an open package whole takes all that is left on it, which changed: load the page again`,
  moved_whole: msg`That open package moves whole, still open: it is not used up`,
  package_resized: msg`This package's size changed since the page loaded: load the page again, and count it again`,
  refused: msg`The inventory's rules refuse this as sent: it changed since the page loaded. Load the page again, and do it again`,
  retired: msg`This item is retired: what is left may be taken, never received`,
  retired_package: msg`This package is retired: what is left may be taken, never received`,
  retired_package_none_left: msg`This package is retired, and the inventory has none left sealed: take this line off, record the rest again, and ask an administrator to count it`,
  repeated: msg`This item, in this package or loose, is on another line too: put it on one line`,
  package_size_required: msg`A line of a package says the size it was counted in: load the page again, and count it again`,
  package_size_without_package: msg`A loose line says a package's size: load the page again, and count it again`,
};

/** Words for an alert: a string is the page's own, its numbers counts it
 * wrote, shown in italic; `{ quoted }` is words it did not write, such as
 * a name or what an older build kept, shown as they are (Ali,
 * inventory-tng-93z). */
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
export const KEY_REUSED = msg`Not recorded: its key was already used for a different batch. Please report this.`;

/** What the page says first of a recorded_elsewhere, bold, as an alert,
 * with details below it (Ali, inventory-tng-0gg, 2026-11-15, 1). */
export const SOME_NOT_RECORDED = msg`Some items were not recorded. Details below.`;

export const RECORDED_HERE = msg`Already recorded from this tab in transaction {transaction}`;
export const RECORDED_ELSEWHERE = msg`Recorded in another tab in transaction {transaction}`;

/**
 * The heading over what another tab recorded, by who recorded it
 * (Recorded.by): this browser's own tab, as when it could not empty the
 * cart it recorded, or another (Ali, inventory-tng-0gg, 2026-11-15, 1 and
 * 6).
 */
export function recordedHeading(recorded: Recorded): string {
  return say(recorded.by === "here" ? RECORDED_HERE : RECORDED_ELSEWHERE, {
    transaction: recorded.transactionId,
  });
}

/** The heading over what a recorded_elsewhere holds otherwise than was
 * recorded (scan/recorded-elsewhere.ts), or, with nothing known of what
 * was recorded, over its lines (Ali, inventory-tng-0gg, 2026-11-15, 1).
 * What it held is recorded, but not the changes since, so it never goes in
 * a cart whole again, which would record it twice (Ali, inventory-tng-wyq,
 * question c, and inventory-tng-g0e, d). */
export const MAKE_CHANGES = msg`Not recorded. Make any changes and record again.`;

/** A batch waiting after no_answer, while the browser is online. */
export const NO_ANSWER = msg`No answer from the inventory. Trying again shortly.`;

/** NO_ANSWER's words while the browser says it is offline (Ali,
 * inventory-tng-wyq, 'Offline'). */
export const OFFLINE = msg`This device is offline. Sending when it's back.`;

/** Before a refused batch's problems (422), in bold. */
export const NOT_RECORDED = msg`Not recorded.`;

export const UNAVAILABLE = msg`The inventory can't record this just now. Trying again shortly.`;
/** An unexpected answer to a batch, REPORT_AFTER times or more in a row. */
export const KEEPS_FAILING = msg({
  message:
    "{tries, plural, one {The inventory keeps failing on this batch ({status}, # try). It is kept and tried again; please report this.} other {The inventory keeps failing on this batch ({status}, # tries). It is kept and tried again; please report this.}}",
});
export const UNEXPECTED = msg`The inventory answered unexpectedly ({status}). Trying again shortly.`;
/** After why this browser cannot save (registration.NOT_REGISTERED). */
export const REGISTER_TO_SEND = msg`Register it to send this batch.`;

/** A 422's problem the page has no words for. */
export const NOT_ACCEPTED = msg`Not accepted`;
/** A problem with the line it names, numbered as the cart's. */
export const ON_LINE = msg`Line {n}: {why}`;
/** A problem with the report it names, numbered among those of its kind. */
const ON_REPORT: Record<ReportKind, MessageDescriptor> = {
  used_up: msg`Open package said used up, {n}: {why}`,
  lengths: msg`What is on an open package, {n}: {why}`,
};

/** What the page says of `reason`; of no_answer, as the browser is
 * `online` or not. */
export function noteOf(reason: Reason, online = true): Note {
  switch (reason.code) {
    case "no_answer":
      return { bold: [say(online ? NO_ANSWER : OFFLINE)] };
    case "unavailable":
      return { bold: [say(UNAVAILABLE)] };
    case "must_upgrade":
      return { bold: [say(MUST_UPGRADE)] };
    case "key_reused":
      return { bold: [say(KEY_REUSED)] };
    case "recorded_elsewhere":
      return { bold: [say(SOME_NOT_RECORDED)] };
    case "unexpected": {
      const { status, tries } = reason;
      return {
        bold: [
          tries >= REPORT_AFTER
            ? say(KEEPS_FAILING, { status, tries })
            : say(UNEXPECTED, { status }),
        ],
      };
    }
    case "not_registered":
      return {
        bold: [`${say(NOT_REGISTERED[reason.why])} ${say(REGISTER_TO_SEND)}`],
      };
    case "refused":
      return {
        bold: [say(NOT_RECORDED)],
        plain: reason.problems.flatMap(problemSaid),
      };
  }
}

/** One of a 422's problems, after a space: what it names, then why, in
 * the page's words for its kind. */
function problemSaid({ names, kind }: Problem): Said {
  const why = say(isProblem(kind) ? PROBLEMS[kind] : NOT_ACCEPTED);
  if (names === null) {
    return [" ", why];
  }
  const where = names.of === "line" ? ON_LINE : ON_REPORT[names.of];
  return [" ", say(where, { n: names.n, why })];
}
