/** Open packages offered to a cart, and the batches the catalogue has not seen. */

import { type Draft, type Entry, fromOpen } from "../shared/outbox";
import { sent, thousandths } from "../shared/quantity";
import type { Catalogue, Line } from "./cart";
import { indexOf } from "./search";

/** Batch keys a catalogue is asked about (scan/catalogue_api.py, MOST_KEYS). */
export const MAX_KEYS_ASKED = 100;

export function sealedKey(packageId: number, placeId: number): string {
  return `${packageId}@${placeId}`;
}

/** A batch in the outbox, as offered and sealedCounts read it. */
type Pending = Pick<Entry, "state" | "body" | "queuedAt">;

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
  const sizes = new Map<number, number>();
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
      sizes.set(pack.id, sent(pack.size));
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
      const size = packageId == null ? undefined : sizes.get(packageId);
      // Read as stored, which another build may have written: one
      // unreadable is skipped.
      const quantity = thousandths(String(line.quantity));
      if (
        packageId == null ||
        taken ||
        size === undefined ||
        refusedAsCounted(line, size) ||
        quantity === null
      ) {
        continue;
      }
      // Rounded up, as the catalogue counts a part of one
      // (scan/catalogue_api.py).
      const n = Math.ceil(quantity / size);
      add(packageId, body.from_location_id, -n);
      if (body.kind !== "open") {
        add(packageId, body.to_location_id, n);
      }
    }
  }
  return counts;
}

/** Whether the server refuses a line of a package of `size` (thousandths)
 * as counted, so it moves none: counted in a size its package no longer
 * has (package_resized), or in none, as an older build's stored line may
 * be (inventory-tng-1p8). */
function refusedAsCounted(line: Draft["lines"][number], size: number): boolean {
  return (
    line.package_size == null || thousandths(String(line.package_size)) !== size
  );
}

export type OpenPackage = Catalogue["open_packages"][number];
/** An open package a cart can take from, as it will be once the batches
 * waiting are saved: where it is, and what is left on it, in thousandths. */
export type Offered = Omit<OpenPackage, "left"> & { left: number };

/**
 * The open packages carts can take from: the catalogue's, and those opened
 * in `pending` (unseen), under the keys this page made, so they can be
 * taken from before the server has seen them; each as those batches leave
 * it, cut from or moved whole (docs/decisions/0020). Worked out once for
 * every line of a cart (offeredAt).
 */
export function offered(
  catalogue: Catalogue,
  pending: readonly Pending[],
): Offered[] {
  const byKey = new Map<string, Offered>(
    catalogue.open_packages.map((each) => [
      each.key,
      { ...each, left: sent(each.left) },
    ]),
  );
  const known = new Set(byKey.keys());
  const { items } = indexOf(catalogue);
  for (const { body: draft, queuedAt } of pending) {
    // Said to be used up: offered no more.
    for (const key of draft.used_up ?? []) {
      byKey.delete(key);
    }
    for (const line of draft.lines) {
      const key = line.open_package_key;
      // As stored, which another build may have written: one unreadable is
      // skipped, as sealedCounts does.
      const amount = thousandths(String(line.quantity));
      if (key == null || amount === null) {
        continue;
      }
      if (draft.kind === "open") {
        if (known.has(key)) {
          continue;
        }
        // A retired package is missing once the catalogue says none is
        // sealed: named once saved, from the catalogue's open package.
        const pack = items
          .get(line.item_id)
          ?.packages.find((each) => each.id === line.package_id);
        // One the server refuses opens none, so is never offered: one in no
        // size whatever the catalogue holds.
        const refused =
          pack === undefined
            ? line.package_size == null
            : refusedAsCounted(line, sent(pack.size));
        if (!refused) {
          byKey.set(key, {
            id: 0,
            key,
            item_id: line.item_id,
            package_id: line.package_id ?? 0,
            package_name: pack?.name ?? "package",
            location_id: draft.from_location_id ?? 0,
            opened_at: new Date(queuedAt).toISOString(),
            left: amount,
            short_below: pack?.short_below ?? null,
          });
        }
        continue;
      }
      const open = byKey.get(key);
      const to = line.whole ? draft.to_location_id : undefined;
      if (open === undefined) {
        continue;
      }
      if (to == null) {
        open.left -= amount;
      } else {
        open.location_id = to;
      }
    }
  }
  return [...byKey.values()];
}

/** offered's open packages of an item at a place, as the stock page lists
 * them (open_packages.fewest_left_first): fewest left first, ties by id,
 * then short ends (isShortEnd) the same way, then those showing nothing,
 * or less (docs/decisions/0020). One opened on this phone that the
 * catalogue does not show yet has no id here (0), so it follows those
 * that have one, in the order it was opened. By what is on each now,
 * never after a cut being typed. */
export function offeredAt(
  all: readonly Offered[],
  itemId: number,
  placeId: number | null,
): Offered[] {
  // Usable, then short ends, then those showing nothing or less.
  const last = (each: Offered) =>
    each.left <= 0 ? 2 : Number(isShortEnd(each.left, each.short_below));
  const unsaved = (each: Offered) => Number(each.id === 0);
  return all
    .filter((each) => each.item_id === itemId && each.location_id === placeId)
    .sort(
      (a, b) =>
        last(a) - last(b) ||
        a.left - b.left ||
        unsaved(a) - unsaved(b) ||
        a.id - b.id,
    );
}

/** Whether an open package with `left` (thousandths) on it is a short
 * end: some left, but less than its package's too-short-to-use length
 * (open_packages.is_short_end). */
export function isShortEnd(
  left: number,
  shortBelow: Offered["short_below"],
): boolean {
  const below = shortBelow === null ? null : thousandths(shortBelow);
  return below !== null && 0 < left && left < below;
}

/** Why a cut leaves nothing usable on an open package. */
export type Ending = "short" | "used" | "over";

/** What a cut of `amount` (thousandths) leaves on an open package, and
 * why nothing usable would be left then, if so: less than nothing, none,
 * or less than its too-short-to-use length (open_packages.is_short_end). */
export function afterCut(
  open: Pick<Offered, "left" | "short_below">,
  amount: number,
): { after: number; ending: Ending | null } {
  const after = open.left - amount;
  if (after < 0) {
    return { after, ending: "over" };
  }
  if (after === 0) {
    return { after, ending: "used" };
  }
  return {
    after,
    ending: isShortEnd(after, open.short_below) ? "short" : null,
  };
}

export type Taken = NonNullable<Line["open"]>[string];

/** The cut typed on an open package, as afterCut sees it; null for none:
 * blank, nothing, not yet a number, or all of it moved whole. */
export function cutOf(
  open: Pick<Offered, "left" | "short_below">,
  taken: Taken,
): ReturnType<typeof afterCut> | null {
  const typed = thousandths(taken.amount);
  return taken.whole || typed === null || typed === 0
    ? null
    : afterCut(open, typed);
}

/** Whether an open package is said to be used up: as the volunteer said,
 * else when the cut leaves nothing usable on it (Ali, 2026-11-12). */
export function usedUpOf(
  open: Pick<Offered, "left" | "short_below">,
  taken: Taken,
): boolean {
  return taken.usedUp ?? cutOf(open, taken)?.ending != null;
}
