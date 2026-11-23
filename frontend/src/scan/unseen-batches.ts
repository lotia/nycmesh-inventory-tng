/**
 * The outbox's batches a catalogue has not seen: those it is asked about
 * (keysToAsk), those it does not show (unseen), and the sealed packages at
 * each place as those batches leave them (sealedCounts, by sealedKey).
 */
import { type Draft, type Entry, fromOpen } from "../shared/outbox";
import { sent, thousandths } from "../shared/quantity";
import type { Catalogue } from "./cart";

/** Batch keys a catalogue is asked about (scan/catalogue_api.py, MOST_KEYS). */
export const MAX_KEYS_ASKED = 100;

export function sealedKey(packageId: number, placeId: number): string {
  return `${packageId}@${placeId}`;
}

/** A batch in the outbox, as offered and sealedCounts read it. */
export type Pending = Pick<Entry, "state" | "body" | "queuedAt">;

/** The batch keys a catalogue is asked about (scan/catalogue_api.py,
 * MOST_KEYS): its outbox's saved ones, then those waiting, oldest first. */
export function keysToAsk(outbox: readonly Pending[]): string[] {
  const of = (state: string) =>
    outbox.filter((each) => each.state === state).map((each) => each.body.key);
  return [...of("saved"), ...of("waiting")].slice(0, MAX_KEYS_ASKED);
}

/**
 * The outbox's batches a catalogue does not show: those waiting or saved
 * whose keys it does not say are saved (keysToAsk). One saved after it was
 * read is caught when the page asks again, after every save.
 */
export function unseen<T extends Pending>(
  outbox: readonly T[],
  catalogue: Catalogue,
): T[] {
  const shown = new Set(catalogue.saved);
  return outbox.filter(
    (each) =>
      (each.state === "waiting" || each.state === "saved") &&
      !shown.has(each.body.key),
  );
}

/**
 * How many of each package are sealed at each place, by sealedKey: the
 * catalogue's, less what `pending` (unseen) opens or takes and plus what
 * it brings, so opening the last one sealed at a place leaves none there
 * to fill the next opening with (sealedHere).
 */
export function sealedCounts(
  catalogue: Catalogue,
  pending: readonly Pending[],
): Map<string, number> {
  const counts = new Map<string, number>();
  // Each package's size, in thousandths, and its item's unit.
  const packs = new Map<number, { size: number; unit: string }>();
  const add = (
    packageId: number,
    placeId: number | null | undefined,
    n: number,
  ) => {
    if (placeId != null) {
      const key = sealedKey(packageId, placeId);
      counts.set(key, (counts.get(key) ?? 0) + n);
    }
  };
  for (const item of catalogue.items) {
    for (const pack of item.packages) {
      packs.set(pack.id, { size: sent(pack.size), unit: item.unit });
      for (const { location_id, count } of pack.sealed) {
        add(pack.id, location_id, count);
      }
    }
  }
  for (const { body } of pending) {
    for (const line of body.lines) {
      // A line from an open package moves no sealed one; nor is one of a
      // package the catalogue has no more counted.
      const packageId = line.package_id;
      const taken = fromOpen(line, body.kind);
      const pack = packageId == null ? undefined : packs.get(packageId);
      // Read as stored, which another build may have written: one
      // unreadable is skipped.
      const quantity = thousandths(String(line.quantity));
      if (
        packageId == null ||
        taken ||
        pack === undefined ||
        refusedAsCounted(line, pack.size, pack.unit) ||
        quantity === null
      ) {
        continue;
      }
      // Rounded up, as the catalogue counts a part of one
      // (scan/catalogue_api.py).
      const n = Math.ceil(quantity / pack.size);
      add(packageId, body.from_location_id, -n);
      if (body.kind !== "open") {
        add(packageId, body.to_location_id, n);
      }
    }
  }
  return counts;
}

/** Whether the server refuses a line of a package of `size` (thousandths)
 * of an item counted in `unit` as counted, so it moves none: counted in a
 * size its package no longer has (package_resized), or in none, as an
 * older build's stored line may be (inventory-tng-1p8); or in another
 * unit than its item's now (unit_changed), or in none. */
export function refusedAsCounted(
  line: Draft["lines"][number],
  size: number,
  unit: string,
): boolean {
  return (
    line.package_size == null ||
    thousandths(String(line.package_size)) !== size ||
    line.unit !== unit
  );
}
