/**
 * What one scanned code does to the batch.
 *
 * Shared deliberately: a code arrives from the phone's own camera app as a
 * deep link, from the in-app scanner, from a wedge scanner, and from somebody
 * typing what is printed under a dead QR. All four mean the same thing, so
 * they resolve and apply the same way -- see
 * docs/decisions/0011-qr-batch-scanning.md sections 3 and 5.
 */
import { apiGet, asApiError, isAbort } from "../api/client";
import type { Item, ResolvedLabel } from "../api/types";
import type { CartIntent } from "../cart/CartProvider";
import type { ScannedLabel } from "../cart/cartState";

/**
 * A scanned label whose amount the volunteer still has to say.
 *
 * The label itself, in the shape the cart takes -- so what is entered later is
 * dispatched as the scan it always was, with the amount as the override the
 * reducer already accepts. `revoked` travels with it because the advice that
 * gets a faded sticker reprinted must not be lost between the scan and the
 * answer, and cable labels are the oldest ones on the shelf.
 */
export interface Measured {
  label: ScannedLabel;
  revoked: boolean;
}

/** What happened, in the terms the volunteer needs to hear it. */
export type Outcome =
  | { applied: "item"; name: string; quantity: number; revoked: boolean }
  | { applied: "location"; revoked: boolean }
  | { applied: "measured"; measured: Measured }
  | { applied: "unknown"; code: string }
  | { applied: "failed"; detail: string };

/**
 * Whether a scan of this item may be recorded without asking how much.
 *
 * Only where the unit is `each`. Decision 0011 section 5: a cable label says
 * what a full box is, and a volunteer scanning one is as likely to be
 * returning part of it -- so nothing measured is defaulted, because a
 * quantity nobody looked at is the ambiguity this project exists to remove.
 */
export function countsItself(unitOfMeasure: string): boolean {
  return unitOfMeasure === "each";
}

/**
 * Resolve a code and apply it, or say why not.
 *
 * The dispatch is passed in rather than a context read here, so this stays a
 * function a test can call and the caller keeps deciding what to do with the
 * answer.
 */
export async function applyCode(
  code: string,
  dispatch: (intent: CartIntent) => void,
  signal?: AbortSignal,
): Promise<Outcome> {
  let label: ResolvedLabel;
  try {
    label = await apiGet<ResolvedLabel>(`/api/labels/${encodeURIComponent(code)}`, signal);
  } catch (error: unknown) {
    if (isAbort(error)) {
      throw error;
    }
    const refused = asApiError(error);
    // A code that is not ours is not a failure to report as one: the volunteer
    // is holding something, and the answer is to help them find it rather than
    // to show them a status code.
    if (refused.status === 404) {
      return { applied: "unknown", code };
    }
    return { applied: "failed", detail: refused.message };
  }

  const revoked = label.revoked_at !== null;

  if (label.kind === "location" && label.location !== null) {
    // Scanning a wall code says where this batch is moving stock from or to.
    // It is the mockup's "only 1 QR code to scan", and setting it is
    // idempotent, so scanning it twice is harmless.
    dispatch({ type: "setLocation", locationId: label.location });
    return { applied: "location", revoked };
  }

  if (label.item === null) {
    return { applied: "unknown", code };
  }

  // The label says how much one scan of it means; the item says what to call
  // it and what it is counted in. The cart line needs both.
  let item: Item;
  try {
    item = await apiGet<Item>(`/api/items/${label.item}`, signal);
  } catch (error: unknown) {
    if (isAbort(error)) {
      throw error;
    }
    return { applied: "failed", detail: asApiError(error).message };
  }

  const scanned: ScannedLabel = {
    code: label.code,
    item: { id: item.id, name: item.name, unitOfMeasure: item.unit_of_measure },
    quantity: Number(label.quantity),
  };

  // Nothing goes into the batch yet for a measured item: the caller asks, and
  // calls `recordMeasured` with the answer. See countsItself.
  if (!countsItself(item.unit_of_measure)) {
    return { applied: "measured", measured: { label: scanned, revoked } };
  }

  dispatch({ type: "scan", label: scanned });
  return { applied: "item", name: item.name, quantity: scanned.quantity, revoked };
}

/** The measured scan above, once somebody has said how much. */
export function recordMeasured(
  measured: Measured,
  quantity: number,
  dispatch: (intent: CartIntent) => void,
): Outcome {
  // The override the reducer takes for exactly this: the label's own quantity
  // is not a safe default where the unit is not `each`.
  dispatch({ type: "scan", label: measured.label, quantity });
  return {
    applied: "item",
    name: measured.label.item.name,
    quantity,
    revoked: measured.revoked,
  };
}
