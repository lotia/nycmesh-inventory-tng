/**
 * The volunteer this device last worked as.
 *
 * Remembered so nobody retypes their name on every submission, which is what
 * produced 102 spellings of fewer people in the sheet this replaces. The cart
 * already carries `actorId` and survives a reload, but an id is not something
 * to show somebody: this keeps the name beside it so the picker can open
 * saying who you are rather than asking again.
 *
 * Separate from the cart on purpose. A cart is one batch and is cleared when
 * it is sent; the person standing at the shelf is not.
 */
import type { Volunteer } from "../api/types";
import { isNumber, isText, matches, read, write } from "../storage";

/** Versioned, for the reason cartStorage's key is. */
export const STORAGE_KEY = "nycmesh-inventory.volunteer.v1";

/** What is kept: enough to show and to attribute, and nothing else. */
export interface RememberedVolunteer {
  id: number;
  displayName: string;
}

function isRemembered(value: unknown): value is RememberedVolunteer {
  return matches(value, { id: isNumber, displayName: isText });
}

export function loadVolunteer(): RememberedVolunteer | null {
  return read(STORAGE_KEY, isRemembered);
}

/** Stores the volunteer, and hands back what was stored so callers agree. */
export function saveVolunteer(volunteer: Volunteer): RememberedVolunteer {
  const remembered = { id: volunteer.id, displayName: volunteer.display_name };
  write(STORAGE_KEY, remembered);
  return remembered;
}
