/**
 * A batch the inventory refused (422) whose problems name lines, less
 * those lines: the rest, for Record the rest to put in the cart, to record
 * again (restOf); and the lines taken off, each with why, in words
 * (takenOff). Decided by Ali, 2026-11-20 (inventory-tng-clvh). A refused
 * batch recorded nothing, so the rest is never recorded twice.
 */
import { msg } from "@lingui/core/macro";
import { v5 as uuidFrom } from "uuid";
import { say } from "../shared/i18n";
import { type Body, batchId, type Entry } from "../shared/outbox";
import { problemWords } from "../shared/outbox-notes";
import type { Catalogue } from "./cart";
import { nameOf, withRetired } from "./older-to-cart";
import { indexOf } from "./search";

/** The button under a refused batch whose problems name lines (Ali,
 * 2026-11-20). */
export const RECORD_THE_REST = msg`Record the rest`;
/** In the cart's status, a line taken off, then why: the problem's words. */
export const TAKEN_OFF = msg`{name}: taken off. {why}`;
/** Under the refused batch, once the rest is in the cart, until recorded. */
export const REST_IN_CART = msg`The other lines are in your cart, waiting to be recorded.`;

/** Of a refused batch, each problem naming a line, with that line's item:
 * numbered by its item's first place among the batch's lines
 * (outbox.lineNumbers). */
function namedLines(entry: Entry): { itemId: number; kind: string }[] {
  const { reason, body } = entry;
  if (reason?.code !== "refused") {
    return [];
  }
  const items = [...new Set(body.lines.map((line) => line.item_id))];
  return reason.problems.flatMap(({ names, kind }) => {
    const itemId = names?.of === "line" ? items[names.n - 1] : undefined;
    return itemId === undefined ? [] : [{ itemId, kind }];
  });
}

/**
 * `entry`'s batch less every line its problems name, each of the cart's
 * lines named whole, as the problems number them by item; null unless it
 * was refused (422), a problem names a line and a line is left. Its reports
 * stay, for the cart to say they cannot go back. Under a key of its own
 * made from the batch's, so put in the cart twice, from two tabs, it is
 * the same batch, recorded once.
 */
export function restOf(entry: Entry): Body | null {
  const named = new Set(namedLines(entry).map(({ itemId }) => itemId));
  if (named.size === 0) {
    return null;
  }
  const { body } = entry;
  const lines = body.lines.filter((line) => !named.has(line.item_id));
  if (lines.length === 0) {
    return null;
  }
  const { changed_from: _, ...rest } = body;
  return {
    ...rest,
    key: uuidFrom(`rest ${batchId(body)}`, body.key),
    lines,
  };
}

/** The lines Record the rest takes off `entry`, each as "item: taken off.
 * why", in the problem's words, named from `catalogue`, retired items
 * too; each said once. */
export function takenOff(entry: Entry, catalogue: Catalogue): string[] {
  const { items } = indexOf(withRetired(catalogue));
  return [
    ...new Set(
      namedLines(entry).map(({ itemId, kind }) =>
        say(TAKEN_OFF, {
          name: nameOf(items.get(itemId)),
          why: problemWords(kind),
        }),
      ),
    ),
  ];
}
