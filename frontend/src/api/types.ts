/**
 * The shapes the API answers with.
 *
 * Hand-written rather than generated, and deliberately partial: this file
 * describes what the app reads, not everything `backend/openapi.yaml`
 * documents. That document is the contract these must agree with -- see
 * DEVELOPERS.md "The API schema" -- and the integration suite is what catches
 * a disagreement, because a type cannot.
 *
 * Decimals arrive as strings. DRF renders them that way on purpose: a decimal
 * is not a double, and a quantity of stock is not something to round on the
 * way through JSON.
 *
 * One guard lives here as well, for the one answer this app acts on before it
 * has finished reading -- see `isRecordedBatch`.
 */
import { isNumber, isText, matches } from "../storage";

/** One page of a list endpoint, in DRF's `PageNumberPagination` shape. */
export interface Page<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

/** How much of one item is at one location, derived from the ledger. */
export interface ItemBalance {
  location: number;
  quantity: string;
}

/**
 * An active label on an item, and what one scan of it means.
 *
 * The distinct quantities across an item's labels *are* its packaging; see
 * docs/decisions/0011-qr-batch-scanning.md section 5.
 */
export interface ItemLabel {
  code: string;
  quantity: string;
}

export interface Item {
  id: number;
  name: string;
  category: number | null;
  unit_of_measure: string;
  minimum_stock: string;
  reorder_quantity: string;
  active: boolean;
  balances: ItemBalance[];
  labels: ItemLabel[];
}

/** A volunteer as the pick-list shows them. */
export interface Volunteer {
  id: number;
  display_name: string;
  email: string | null;
  slack_id: string | null;
}

/**
 * The 409 from `POST /api/volunteers`: the identifier is taken, by a record
 * the pick-list will not show.
 *
 * A dead end without this -- the searcher was offered nothing, and a plain
 * 400 would name a record the API refuses to show them. See
 * docs/decisions/0015-merged-identifier-conflict.md.
 */
export interface VolunteerConflict {
  detail: string;
  code: "volunteer_merged" | "volunteer_inactive";
  field: "email" | "slack_id";
  volunteer: Volunteer;
  /** Whether the named volunteer can be picked as they stand. */
  selectable: boolean;
}

/**
 * What a scanned or typed code points at, as `GET /api/labels/{code}` answers.
 *
 * A revoked label resolves rather than 404s: the sticker is superseded, but it
 * still says what it pointed at, and refusing the scan would block a volunteer
 * over bookkeeping. `revoked_at` is how the client is told.
 */
export interface ResolvedLabel {
  code: string;
  kind: "item" | "location";
  /** Null on a location label, which stands for no quantity of anything. */
  quantity: string | null;
  revoked_at: string | null;
  item: number | null;
  location: number | null;
}

/**
 * One label as the cached map carries it.
 *
 * Not `ResolvedLabel`. `LabelMapSerializer` drops `revoked_at`, because the
 * map is live labels only and the field would say null a few hundred times --
 * so a row from here is *not revoked by construction*, and reading the missing
 * field off one would warn about every good sticker in the building. It
 * carries the item's name and unit instead, which is what a cart line needs
 * and what saves the client holding the whole catalogue as well.
 */
export interface MappedLabel {
  code: string;
  kind: "item" | "location";
  /** Null on a location label, which stands for no quantity of anything. */
  quantity: string | null;
  item: number | null;
  location: number | null;
  item_name: string | null;
  unit_of_measure: string | null;
}

/** One thing wrong with a submitted batch, and where. */
export interface BatchError {
  /** The position in the submitted movements, or null for the batch itself. */
  index: number | null;
  field: string;
  detail: string;
}

/** Nothing was saved. Every bad line is listed, so one pass fixes them all. */
export interface BatchRejected {
  detail: string;
  errors: BatchError[];
}

/** Stock went negative. The movement was recorded anyway. */
export interface BatchWarning {
  item: number;
  location: number;
  balance: string;
  detail: string;
}

/** A recorded batch, as it is read back. */
export interface RecordedBatch {
  id: number;
  warnings: BatchWarning[];
}

/**
 * Whether what came back really is one, rather than merely typed as one.
 *
 * The type parameter on `apiPost<RecordedBatch>` is a hope, not a fact: a 2xx
 * whose body is not JSON resolves as null -- an empty answer, or an HTML page
 * from something in front of Django -- and reading `warnings` off that throws
 * on the success path, where a throw is read as the save having failed. Both
 * the submit bar and the outbox ask this before they believe an answer.
 */
export function isRecordedBatch(value: unknown): value is RecordedBatch {
  if (!matches(value, { id: isNumber, warnings: Array.isArray })) {
    return false;
  }
  return (value as RecordedBatch).warnings.every((warning: unknown) =>
    matches(warning, { detail: isText }),
  );
}

/** Somewhere stock can be, as the pick-list shows it. */
export interface Location {
  id: number;
  name: string;
  kind: string;
  parent: number | null;
  held_by: number | null;
  active: boolean;
}
