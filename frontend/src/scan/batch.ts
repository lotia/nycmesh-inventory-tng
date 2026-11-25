/** The cart as a batch to record, or what is still to do, and a batch in words. */
import { msg } from "@lingui/core/macro";
import { v4 as uuid, v5 as uuidFrom } from "uuid";
import { countedWhole, wholeEnough } from "../amount-rules";
import { say, sayParts } from "../shared/i18n";
import type { Draft, Summary } from "../shared/outbox";
import type { Said } from "../shared/outbox-notes";
import {
  AMOUNT_PLACES,
  count,
  decimal,
  type OtherSeparator,
  otherSeparator,
  readBox,
  THOUSANDTHS_PER_UNIT,
} from "../shared/quantity";
import {
  type Cart,
  type Catalogue,
  ITEM_SAYS,
  type Item,
  type Packs,
  quantityOf,
  reconcile,
  resized,
  SIDES,
  separatorWords,
  takesFromOpen,
  unitChanged,
} from "./cart";
import { type Offered, takingOffered, usedUpOf } from "./offered";
import { indexOf } from "./search";

/** The server's limits (scan/batch_schemas.py), checked here too, so a
 * batch it would refuse is never queued and the cart is kept to fix it. */
const MAX_ID = 2 ** 31 - 1;
/** numeric(12,3): under a billion units, in thousandths. */
const MAX_AMOUNT = 10 ** 9 * THOUSANDTHS_PER_UNIT;
/** Lines in a batch (batch_schemas.MAX_LINES): a cart line may be several. */
const MAX_LINES = 500;

/** Why a cart cannot be recorded yet (toBatch). Those of a line start
 * with its item's name. */
export const AN_ITEM = msg`An item`;
export const OPEN_NOW_WHOLE = msg`{name}: enter what is on the open package, in whole units, or leave it blank.`;
export const OPEN_NOW_DECIMALS = msg`{name}: enter what is on the open package, up to {places} decimals, or leave it blank.`;
export const TOO_MUCH = msg`{name}: That is more than the inventory can count.`;
export const PICK_YOUR_NAME = msg`Pick your name.`;
export const PICK_FROM = msg`Pick where it comes from.`;
export const PICK_TO = msg`Pick where it goes.`;
export const SAME_PLACE = msg`From and to must be different places.`;
export const INSTALL_DIGITS = msg`An install number is digits only.`;
export const INSTALL_RANGE = msg`That install number is out of range.`;
export const ITEM_GONE = msg`An item in the cart is no longer in the catalogue. Remove it.`;
export const RETIRED_NOT_RECEIVED = msg`{name}: retired: taking only, so it can't be received.`;
export const PACKAGE_RETIRED_NOT_RECEIVED = msg`{name}, {package}: retired: taking only, so it can't be received.`;
export const ENTER_THE_COUNT = msg`{name}: enter the count`;
export const ENTER_AGAIN_IN = msg`{name}: enter the amount again in {unit}`;
export const NOTHING_FROM_OPEN = msg`{name}: Nothing is taken from an open package here.`;
export const OPEN_WHOLE = msg`{name}: Open whole packages: leave loose empty.`;
export const ADD_SOMETHING = msg`Add something to the cart.`;
export const TOO_MUCH_FOR_ONE = msg`That is too much for one batch: record some, then the rest.`;

/** A batch in words, for the outbox, by what it is (batchWords). */
export const ITEMS = msg({
  message: "{count, plural, one {# item} other {# items}}",
});
export const OPEN_PACKAGES = msg({
  message: "{count, plural, one {# open package} other {# open packages}}",
});
export const MOVE = msg({
  comment:
    "{from} and {to} are places of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "Move: {items} from {from} to {to}",
});
export const RECEIPT = msg({
  comment:
    "{to} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "Receipt: {items} to {to}",
});
export const INSTALL = msg({
  comment:
    "{from} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "Install: {items} from {from}",
});
export const INSTALL_NUMBERED = msg({
  comment:
    "{from} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "Install: {items} from {from} for install {number}",
});
export const OPENING = msg({
  comment:
    "{from} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "Opening: {items} at {from}",
});
export const REPORT = msg({
  comment:
    "{from} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "Used up: {packages} at {from}",
});
export const LENGTHS = msg({
  comment:
    "{from} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "What's on it: {packages} at {from}",
});
/** After a batch that takes from open packages, and says of some. */
export const SAID_USED_UP = msg`({packages} said used up)`;
export const SAID_LENGTHS = msg`(what is on {packages} said)`;

/** The cart as the batch endpoint takes it, less the key the outbox adds,
 * and its places' names for its summary (outbox.Summary). */
export type Batch = { draft: Draft; summary: Summary };

/**
 * The cart as a batch (Batch), or what still needs doing before it can be
 * saved.
 */
export function toBatch(
  filled: Cart,
  catalogue: Catalogue,
  all?: readonly Offered[],
): Batch | { problems: Said[]; refused?: OtherSeparator[] } {
  const reconciled = reconcile(filled, catalogue);
  // Only what the page offers to take from, where it comes from, and
  // nothing where its kind shows no open package (CartForm).
  const here = new Map(
    (takesFromOpen(reconciled.kind) ? (all ?? []) : [])
      .filter((each) => each.location_id === reconciled.fromId)
      .map((each) => [each.key, each]),
  );
  // What is typed for an open package no longer offered is left out.
  const cart =
    all === undefined
      ? reconciled
      : {
          ...reconciled,
          lines: reconciled.lines.map((line) => takingOffered(line, here)),
        };
  const index = indexOf(catalogue);
  // Open packages said to be used up: with what is taken, or alone, when
  // nothing else is in the cart: a report, at the From place.
  const usedUp = cart.lines.flatMap((line) =>
    Object.entries(line.open ?? {}).flatMap(([key, taken]) => {
      const open = here.get(key);
      const said = open === undefined ? taken.usedUp : usedUpOf(open, taken);
      return said ? [key] : [];
    }),
  );
  // What is really on open packages, said: with what is taken, or alone.
  const lengths: NonNullable<Draft["lengths"]> = [];
  // Their packages' names, as the cards show them (outbox.Summary.boxes).
  const boxes: (string | null)[] = [];
  // Each problem with the names in it quoted, as no number of a name is a
  // count the page wrote (outbox-notes.Said).
  const problems: Said[] = [];
  // Boxes refused for the other decimal separator than the page's, for
  // the page to count (CartForm).
  const refused: OtherSeparator[] = [];
  for (const line of cart.lines) {
    const item = index.items.get(line.itemId);
    const named = item === undefined ? say(AN_ITEM) : { quoted: item.name };
    const each = item !== undefined && countedWhole(item.unit);
    for (const [key, { now }] of Object.entries(line.open ?? {})) {
      if (now === undefined || now.trim() === "") {
        continue;
      }
      const left = readBox(now);
      const other = left === null ? otherSeparator(now) : null;
      if (other !== null) {
        refused.push(other);
        problems.push(
          sayParts(ITEM_SAYS, { name: named, what: separatorWords(other) }),
        );
      } else if (
        left === null ||
        (item !== undefined && !wholeEnough(item.unit, left))
      ) {
        problems.push(
          sayParts(each ? OPEN_NOW_WHOLE : OPEN_NOW_DECIMALS, {
            name: named,
            places: AMOUNT_PLACES,
          }),
        );
      } else if (left >= MAX_AMOUNT) {
        problems.push(sayParts(TOO_MUCH, { name: named }));
      } else {
        lengths.push({ open_package_key: key, left: decimal(left) });
        boxes.push(here.get(key)?.package_name ?? null);
      }
    }
  }
  const reporting =
    (usedUp.length > 0 || lengths.length > 0) &&
    cart.lines.every((line) => {
      const item = index.items.get(line.itemId);
      const quantity = item && quantityOf(line, item);
      return (
        quantity !== undefined && "amount" in quantity && quantity.amount === 0
      );
    });
  const kind: Draft["kind"] = reporting ? "report" : cart.kind;
  const sides = reporting ? { from: true, to: false } : SIDES[cart.kind];
  const fromId = sides.from ? cart.fromId : null;
  const toId = sides.to ? cart.toId : null;
  if (cart.volunteerId === null) {
    problems.push([say(PICK_YOUR_NAME)]);
  }
  if (sides.from && fromId === null) {
    problems.push([say(PICK_FROM)]);
  }
  if (sides.to && toId === null) {
    problems.push([say(PICK_TO)]);
  }
  if (fromId !== null && fromId === toId) {
    problems.push([say(SAME_PLACE)]);
  }
  const installNumber = kind === "install" ? cart.installNumber.trim() : "";
  const install = count(installNumber);
  if (install === null) {
    problems.push([say(INSTALL_DIGITS)]);
  } else if (installNumber !== "" && (install < 1 || install > MAX_ID)) {
    problems.push([say(INSTALL_RANGE)]);
  }
  const lines: Draft["lines"] = [];
  for (const line of cart.lines) {
    const item = index.items.get(line.itemId);
    if (item === undefined) {
      problems.push([say(ITEM_GONE)]);
      continue;
    }
    const name = { quoted: item.name };
    if (item.retired && cart.kind === "receipt") {
      problems.push(sayParts(RETIRED_NOT_RECEIVED, { name }));
      continue;
    }
    if (cart.kind === "receipt") {
      for (const pack of item.packages) {
        if (pack.retired && (count(line.packages[pack.id] ?? "") ?? 0) > 0) {
          problems.push(
            sayParts(PACKAGE_RETIRED_NOT_RECEIVED, {
              name,
              package: { quoted: pack.name },
            }),
          );
        }
      }
    }
    // Cleared as resized (reconcile), it is to be counted again, not
    // left out: saving the rest would record less than was moved.
    if (item.packages.some((pack) => resized(line, pack))) {
      problems.push(sayParts(ENTER_THE_COUNT, { name }));
      continue;
    }
    // Cleared as typed in another unit (reconcile): typed again in this
    // one, never saved in the old (Ali, 2026-11-23, inventory-tng-h7ua).
    if (unitChanged(line, item)) {
      problems.push(sayParts(ENTER_AGAIN_IN, { name, unit: item.unit }));
      continue;
    }
    const quantity = quantityOf(line, item);
    // Each of its lines says the unit it was typed in (cart.sized): one
    // counted in another since is refused, for the volunteer to count again
    // (Ali, 2026-11-23, inventory-tng-57u8).
    const unit = line.unit ?? item.unit;
    if ("problem" in quantity) {
      problems.push(sayParts(ITEM_SAYS, { name, what: quantity.problem }));
      if (quantity.refused !== undefined) {
        refused.push(quantity.refused);
      }
    } else if (quantity.amount >= MAX_AMOUNT) {
      problems.push(sayParts(TOO_MUCH, { name }));
    } else if (
      (cart.kind === "open" || cart.kind === "receipt") &&
      quantity.taken.size > 0
    ) {
      problems.push(sayParts(NOTHING_FROM_OPEN, { name }));
    } else if (cart.kind === "open" && quantity.loose > 0) {
      problems.push(sayParts(OPEN_WHOLE, { name }));
    } else if (cart.kind === "open" && quantity.amount > 0) {
      lines.push(...openings(item, unit, quantity.sealed, cart.batchKey));
    } else if (quantity.amount > 0) {
      // Each package counted on a line of its own, sealed, so stock keeps
      // them sealed where they arrive (docs/decisions/0020); then loose.
      for (const [packageId, { count, size }] of quantity.sealed) {
        lines.push(packageLine(item.id, unit, packageId, size, count * size));
      }
      if (quantity.loose > 0) {
        lines.push({
          item_id: item.id,
          quantity: decimal(quantity.loose),
          unit,
        });
      }
      // From an open package: cut loose, or, moving it, all of it whole.
      for (const [key, { amount, whole }] of quantity.taken) {
        lines.push({
          item_id: item.id,
          quantity: decimal(amount),
          open_package_key: key,
          ...(whole && cart.kind === "move" ? { whole: true } : {}),
          unit,
        });
      }
    }
  }
  if (
    lines.length + usedUp.length + lengths.length === 0 &&
    problems.length === 0
  ) {
    problems.push([say(ADD_SOMETHING)]);
  }
  if (lines.length > MAX_LINES) {
    problems.push([say(TOO_MUCH_FOR_ONE)]);
  }
  // The volunteer check again only narrows the type; it was reported above.
  if (problems.length > 0 || cart.volunteerId === null) {
    return refused.length > 0 ? { problems, refused } : { problems };
  }
  const draft: Draft = { kind, volunteer_id: cart.volunteerId, lines };
  if (cart.changedFrom !== undefined) {
    draft.changed_from = cart.changedFrom;
  }
  if (usedUp.length > 0) {
    draft.used_up = usedUp;
  }
  if (lengths.length > 0) {
    draft.lengths = lengths;
  }
  if (fromId !== null) {
    draft.from_location_id = fromId;
    if (kind === "open") {
      draft.to_location_id = fromId;
    }
  }
  if (toId !== null) {
    draft.to_location_id = toId;
  }
  if (installNumber !== "") {
    draft.install_number = Number(installNumber);
  }
  // Each kind names the places its batch must have (problems above).
  const place = (placeId: number | null) =>
    placeId === null ? null : String(index.places.get(placeId));
  const summary: Summary = { from: place(fromId), to: place(toId) };
  if (boxes.length > 0) {
    summary.boxes = boxes;
  }
  return { draft, summary };
}

/**
 * A batch's summary in words, in the language spoken now: its kind,
 * counts and install number from `body`, its items counted by its lines'
 * (one cart line each), its places as `summary` kept them (outbox.Summary).
 */
export function batchWords(
  body: Pick<
    Draft,
    "kind" | "install_number" | "lines" | "used_up" | "lengths"
  >,
  summary: Summary,
): string {
  const usedUp = body.used_up?.length ?? 0;
  const lengths = body.lengths?.length ?? 0;
  const packages = say(OPEN_PACKAGES, { count: usedUp });
  const measured = say(OPEN_PACKAGES, { count: lengths });
  const reporting = body.kind === "report";
  // A report of lengths alone says so; one of used up, as ever.
  const lengthsOnly = reporting && usedUp === 0;
  const number = body.install_number ?? null;
  const named = {
    items: say(ITEMS, {
      count: new Set(body.lines.map((line) => line.item_id)).size,
    }),
    packages: lengthsOnly ? measured : packages,
    from: summary.from ?? "",
    to: summary.to ?? "",
    number: number === null ? "" : String(number),
  };
  const head = lengthsOnly
    ? LENGTHS
    : body.kind === "install" && number !== null
      ? INSTALL_NUMBERED
      : {
          move: MOVE,
          receipt: RECEIPT,
          install: INSTALL,
          open: OPENING,
          report: REPORT,
        }[body.kind];
  const words = [say(head, named)];
  if (usedUp > 0 && !reporting) {
    words.push(say(SAID_USED_UP, { packages }));
  }
  if (lengths > 0 && !lengthsOnly) {
    words.push(say(SAID_LENGTHS, { packages: measured }));
  }
  return words.join(" ");
}

/**
 * An opening's lines (docs/decisions/0020): a package its item follows
 * once opened becomes an open package, one a line, under a key made here,
 * so a later batch in the outbox can name it before the server has seen
 * it; others become loose units, one line of all of them. Each key is made
 * from the cart's (`batchKey`), as the server makes those of a line sent
 * without one (scan/batch.py, _opening_keys), so the same cart recorded
 * again, by any tab, is the same batch, which the server takes once.
 */
function openings(
  item: Item,
  unit: string,
  sealed: Map<number, Packs>,
  batchKey: string | undefined,
): Draft["lines"] {
  const followed = new Set(
    item.packages.filter((pack) => pack.track_opened).map((pack) => pack.id),
  );
  return [...sealed].flatMap(([packageId, { count, size }]) =>
    followed.has(packageId)
      ? Array.from({ length: count }, (_, each) => ({
          ...packageLine(item.id, unit, packageId, size, size),
          open_package_key:
            batchKey === undefined
              ? uuid()
              : uuidFrom(`open ${item.id} ${packageId} ${each}`, batchKey),
        }))
      : [packageLine(item.id, unit, packageId, size, count * size)],
  );
}

/** A batch's line of sealed packages. The generated types leave its size
 * optional, as OpenAPI cannot say it is required with package_id, which the
 * server's Line does: this type says so, for each line packageLine builds,
 * as every package line here is. */
type PackageLine = Draft["lines"][number] & {
  package_id: number;
  package_size: string;
};

/** `amount` of the package `packageId`, in thousandths of `unit`, the
 * item's as typed, with the size it was counted in: one resized since is refused, not
 * recounted. */
function packageLine(
  itemId: number,
  unit: string,
  packageId: number,
  size: number,
  amount: number,
): PackageLine {
  return {
    item_id: itemId,
    quantity: decimal(amount),
    package_id: packageId,
    package_size: decimal(size),
    unit,
  };
}
