/** Open packages offered to a cart, and what a cut leaves on one. */

import { sent, thousandths } from "../shared/quantity";
import type { Catalogue, Line } from "./cart";
import { indexOf } from "./search";
import { type Pending, refusedAsCounted } from "./unseen-batches";

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

/** offered's open packages of an item at a place: usable ones fewest left
 * first, ties by id, as the stock page lists them
 * (open_packages.fewest_left_first), then short ends (isShortEnd), which
 * the stock page shows on a line of their own, the same way, then those
 * showing nothing, or less (docs/decisions/0020). One opened on this
 * phone that the catalogue does not show yet has no id here (0), so it
 * follows those that have one, in the order it was opened. By what is on
 * each now, never after a cut being typed. */
export function offeredAt(
  all: readonly Offered[],
  itemId: number,
  placeId: number | null,
): Offered[] {
  const group = (each: Offered) =>
    each.left <= 0 ? 2 : Number(isShortEnd(each.left, each.short_below));
  const unsaved = (each: Offered) => Number(each.id === 0);
  return all
    .filter((each) => each.item_id === itemId && each.location_id === placeId)
    .sort(
      (a, b) =>
        group(a) - group(b) ||
        a.left - b.left ||
        unsaved(a) - unsaved(b) ||
        a.id - b.id,
    );
}

/** Whether an open package with `left` (thousandths) on it is a short
 * end: some left, but less than its package's too-short-to-use length
 * (open_packages.is_short_end). */
function isShortEnd(left: number, shortBelow: Offered["short_below"]): boolean {
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
