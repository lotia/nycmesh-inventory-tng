/**
 * The label map, kept on the device.
 *
 * Decision 0011 section 6 says the client holds it so a scan resolves without
 * a round trip, and `LabelListView` is unpaginated for exactly that. Nothing
 * fetched it, so every scanned code cost two live requests -- twenty-four
 * scans in a basement was forty-eight, on the connection section 1 gives as
 * the reason the batch design exists at all.
 *
 * One request. The map carries the item's name and unit, so the catalogue
 * does not have to be held as well -- `LabelMapSerializer` says what holding
 * the paginated catalogue used to cost.
 *
 * Filled when the app opens, in the same localStorage discipline as the cart
 * and the remembered volunteer.
 *
 * ## How stale it is allowed to get
 *
 * A wrong item name in a cart line is worse than a slow scan, so the rule errs
 * towards asking:
 *
 * - It is refreshed whenever the app opens. A volunteer who reloads has a
 *   current map.
 * - If that refresh fails -- which is the basement this exists for -- a cache
 *   younger than `CACHE_MAX_AGE_MS` is used anyway. A day-old name is worth
 *   more than no scanning at all.
 * - Older than that, it is ignored entirely and codes resolve live. A cache
 *   nobody has been able to refresh for a day is not one to put names from.
 * - A code the cache does not hold always falls back to a live read, so a
 *   label minted since the last refresh still scans.
 *
 * What it never does is *write*. This is a read-through cache of one
 * unpaginated endpoint; the ledger is untouched by any of it.
 */
import { apiGet } from "../api/client";
import type { MappedLabel } from "../api/types";
import { isNumber, matches, read, write } from "../storage";

/** Versioned, for the reason cartStorage's key is. */
export const STORAGE_KEY = "nycmesh-inventory.labels.v1";

/** A day. See the staleness rule above. */
export const CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;

interface Cache {
  filledAt: number;
  labels: Record<string, MappedLabel>;
}

const isLabelMap = (value: unknown): boolean => typeof value === "object" && value !== null;

function isCache(value: unknown): value is Cache {
  return matches(value, { filledAt: isNumber, labels: isLabelMap });
}

/** Held in memory too, so a scan does not parse the whole map from JSON. */
let live: Cache | null = null;

function current(now: number): Cache | null {
  live ??= read(STORAGE_KEY, isCache);
  if (live === null) {
    return null;
  }
  return now - live.filledAt <= CACHE_MAX_AGE_MS ? live : null;
}

/**
 * What the cache says this code is, or null to go and ask.
 *
 * Null covers three cases that all mean the same thing to a caller: nothing
 * cached, a cache too old to trust, and a code minted since it was filled.
 */
export function cachedLabel(code: string, now: number = Date.now()): MappedLabel | null {
  return current(now)?.labels[code] ?? null;
}

/**
 * Fetch the map and keep it.
 *
 * Answers whether it worked rather than throwing: a failure here is a slower
 * scanner, not a broken one, and the caller has nothing useful to do about it.
 */
export async function refreshLabelCache(
  signal?: AbortSignal,
  now: number = Date.now(),
): Promise<boolean> {
  const cache: Cache = { filledAt: now, labels: {} };
  try {
    const labels = await apiGet<MappedLabel[]>("/api/labels", signal);
    // Shape checked rather than assumed. This promises not to throw, and a
    // guard around the fetch alone would let anything answering with the wrong
    // shape leave here as an unhandled rejection -- which is the failure this
    // module exists to keep off the scanning path.
    if (!Array.isArray(labels)) {
      return false;
    }
    for (const label of labels) {
      cache.labels[label.code] = label;
    }
  } catch {
    return false;
  }

  live = cache;
  write(STORAGE_KEY, cache);
  return true;
}

/**
 * Take one code out, because this device has just stopped it being live.
 *
 * THE OTHER HALF OF REVOKING FROM THIS APP. The map holds live labels only, so
 * a row in it is not revoked by construction (see `MappedLabel`) -- and a cache
 * filled before the revocation would go on answering the next scan of that
 * sticker as though nothing had happened, for up to `CACHE_MAX_AGE_MS`. The
 * sticker somebody is holding would read as fine on the very device that
 * retired it, which is the opposite of what a revocation is for.
 *
 * Only this device's. Everybody else's map is stale until it is refreshed,
 * which is the ordinary staleness the rule above already covers: their scan
 * still resolves and the shelf is still counted, and they are told the next
 * time the app opens. What this closes is the case where the client already
 * knows better than what it is holding.
 */
export function forgetLabel(code: string, now: number = Date.now()): void {
  // Through `current` rather than off `live`, so a cache this session has not
  // read yet is hydrated from storage before it is edited. Reading `live`
  // directly did nothing at all in the one case worth covering: a refresh that
  // failed over a cache still young enough to be trusted, which is the basement
  // this module exists for. A cache too old to be read is left alone, because
  // nothing will read it either.
  const cache = current(now);
  if (cache !== null) {
    delete cache.labels[code];
    write(STORAGE_KEY, cache);
  }
}

/** Lets a test start again from nothing. Not used by the app. */
export function forgetLabelCache(): void {
  live = null;
}
