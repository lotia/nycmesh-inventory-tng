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
 */

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
