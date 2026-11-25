/**
 * The batch being filled on this browser. Kept in localStorage so a reload
 * or a closed tab loses nothing, and so the next batch starts with the same
 * name, kind and places (this phone's last choices). Tabs share it.
 *
 * A cart gets its idempotency key with its first line (batchKey), and Record
 * sends it under that key, so a tab of another build that took the cart
 * over, or a tab that had not heard it was recorded, sends it under the
 * same one, and the server takes it once. A cart filled from a save an
 * older build left keeps that save's key, as that build's tab may still be
 * sending it. Emptied or discarded, it drops its key: what fills it next
 * is another batch.
 */
import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { type Signal, signal } from "@preact/signals";
import { v4 as uuid } from "uuid";
import * as v from "valibot";
import { wholeEnough } from "../amount-rules";
import type { Schemas } from "../api/client";
import { id } from "../api/replies";
import { BUILD } from "../shared/build";
import { say, sayParts } from "../shared/i18n";
import {
  onStoredChange,
  readStored,
  removeStored,
  writeStored,
} from "../shared/local-storage";
import { BatchKey, BatchLine } from "../shared/outbox";
import type { Said } from "../shared/outbox-notes";
import {
  AMOUNT_PLACES,
  boxAmount,
  boxSeparator,
  count,
  type OtherSeparator,
  otherSeparator,
  readBox,
  THOUSANDTHS_PER_UNIT,
  thousandths,
} from "../shared/quantity";
import { indexOf } from "./search";
import { sealedKey } from "./unseen-batches";

/** Versioned as the outbox's is (shared/outbox.ts): each build reads its own.
 * v2's is stamped with the build that wrote it, may keep a batch key
 * (batchKey: made with its first line since inventory-tng-e2d; a build
 * before kept one only from a save put back), and keeps the size each
 * package was counted at (sizes). */
export const STORAGE_KEY = "inventory.cart.v2";

export type Catalogue = Schemas["Catalogue"];
export type Item = Schemas["ItemEntry"];
type Package = Item["packages"][number];

const LineSchema = v.object({
  itemId: id,
  /** Whole packages, by package id, as typed. */
  packages: v.record(v.string(), v.string()),
  /** The size each package was counted at, by package id, as the
   * catalogue gave it then (sized): what is saved. One the catalogue now
   * gives another size has its count cleared (reconcile) and is said
   * (resized) until a count is typed again. */
  sizes: v.exactOptional(v.record(v.string(), v.string())),
  /** Loose units, in the item's unit, as typed. */
  loose: v.string(),
  /** The item's unit its amounts were typed in, as the catalogue gave it
   * then (sized): what is saved, so a batch of an item counted in another
   * unit since is refused, not recorded in the new one (inventory-tng-57u8).
   * A line kept without, by a build from before, is in the unit shown now. */
  unit: v.exactOptional(v.string()),
  /** Taken from open packages, by key: how much, as typed, and whether
   * all of one moves with it, whole (docs/decisions/0020). */
  open: v.exactOptional(
    v.record(
      v.string(),
      v.object({
        amount: v.string(),
        whole: v.boolean(),
        /** Said to be used up, or not, by the volunteer, for an
         * administrator to confirm; unsaid, as the cut says (usedUpOf). */
        usedUp: v.exactOptional(v.boolean()),
        /** What the volunteer says is really on it now, as typed, for an
         * administrator to check (docs/decisions/0020, 2026-11-12). */
        now: v.exactOptional(v.string()),
      }),
    ),
  ),
  /** Typed into by the volunteer, or a package's label scanned: theirs,
   * never filled again for the place picked (refilled). */
  typed: v.exactOptional(v.boolean()),
  /** How many times the item was picked, by search or label, so a line
   * filled again opens as many (refilled). */
  picked: v.exactOptional(v.number()),
});

const CartSchema = v.object({
  /** What a cart is filled for: the batch's kind, but a report, which
   * toBatch makes of a cart that only says open packages are used up. */
  kind: v.picklist(["move", "receipt", "install", "open"]),
  volunteerId: v.nullable(id),
  fromId: v.nullable(id),
  toId: v.nullable(id),
  installNumber: v.string(),
  lines: v.array(LineSchema),
  /** When the cart was last used, in milliseconds since 1970: what the name
   * check goes by (askWho). */
  usedAt: v.optional(v.number(), 0),
  /** When its first line went in, in milliseconds since 1970; 0 while
   * empty: how long a batch took from then to saving (shared/telemetry.ts). */
  startedAt: v.optional(v.number(), 0),
  /** New with every change to what the cart holds: how a tab tells whether
   * another tab has changed it since (CartStore). */
  revision: v.optional(v.string(), ""),
  /** The key it is to be recorded under: made with its first line, or the
   * key of what an older build left that filled it (older-builds.ts), kept
   * until the cart is emptied. */
  batchKey: v.exactOptional(BatchKey),
  /** The lines, as a batch sends them, the cart held when put back under
   * its key (older-to-cart.ts, toCart), kept with the key: what a
   * volunteer then takes off, and another tab recorded under the key
   * meanwhile, is kept for an administrator (shared/outbox.ts,
   * changed_from; inventory-tng-0gg). A build before drops it from what it
   * writes back: then nothing is kept. */
  changedFrom: v.exactOptional(v.array(BatchLine)),
});

export type Line = v.InferOutput<typeof LineSchema>;
export type Cart = v.InferOutput<typeof CartSchema>;
export type Kind = Cart["kind"];

/** Which sides a kind has: a receipt comes from outside, an install goes to
 * one (the ledger's rule, in scan/batch_schemas.py). */
export const SIDES: Record<Kind, Record<Side, boolean>> = {
  move: { from: true, to: true },
  receipt: { from: false, to: true },
  install: { from: true, to: false },
  // At one place, picked as From; the batch names it on both sides.
  open: { from: true, to: false },
};

/** Whether a cart of `kind` takes from the open packages where it comes
 * from, as their cards offer: one with a From place, but not an opening,
 * which opens sealed ones there. */
export function takesFromOpen(kind: Kind): boolean {
  return SIDES[kind].from && kind !== "open";
}

/** What a cart is filled for, in the words the page asks it in. */
export const KIND_WORDS: Record<Kind, MessageDescriptor> = {
  move: msg`Moving stock`,
  receipt: msg`Receiving stock`,
  install: msg`Taking it to an install`,
  open: msg`Opening packages`,
};

/** A new, empty cart that keeps the last one's choices. */
export function freshCart(last?: Cart): Cart {
  return {
    kind: last?.kind ?? "move",
    volunteerId: last?.volunteerId ?? null,
    fromId: last?.fromId ?? null,
    toId: last?.toId ?? null,
    installNumber: "",
    lines: [],
    usedAt: 0,
    startedAt: 0,
    revision: "",
  };
}

/**
 * Whether to ask if the name chosen is still the person holding the phone:
 * unused longer than administrators set (name_check_minutes, in the
 * catalogue; 0 never asks), it may be in someone else's hands
 * (docs/decisions/0011).
 */
export function askWho(
  cart: Cart,
  catalogue: Catalogue,
  now = Date.now(),
): boolean {
  const minutes = catalogue.settings.name_check_minutes;
  return (
    minutes > 0 &&
    cart.volunteerId !== null &&
    now - cart.usedAt > minutes * 60_000
  );
}

export function loadCart(storage: Storage = localStorage): Cart {
  const parsed = v.safeParse(CartSchema, readStored(storage, STORAGE_KEY));
  return parsed.success ? parsed.output : freshCart();
}

/** False if the browser would not keep it. Stamped with this build, so a
 * tab of an older one learns a newer one is out (shared/build.ts). */
export function storeCart(
  cart: Cart,
  storage: Storage = localStorage,
): boolean {
  return writeStored(storage, STORAGE_KEY, { ...cart, build: BUILD });
}

/**
 * The cart this browser is filling, shared by its tabs, as the outbox is.
 * A change to what it holds gets a new revision; marking it used changes
 * only the time. Save takes the cart only if no other tab has changed it
 * since this tab read it, so one cart is never saved twice.
 */
export class CartStore {
  readonly cart: Signal<Cart>;
  private catalogue: Catalogue;
  /** Whether a reload brings back this tab's cart (write). If not,
   * storage cannot tell what another tab did, and this tab's cart is the
   * one to save. */
  private kept = true;

  constructor(
    catalogue: Catalogue,
    private readonly storage: Storage = localStorage,
  ) {
    this.catalogue = catalogue;
    this.cart = signal(this.stored());
  }

  /** The stored cart, checked against the catalogue so the lists and the
   * cart agree on what is chosen. */
  private stored(): Cart {
    return reconcile(loadCart(this.storage), this.catalogue);
  }

  /**
   * Show and store `next`; false if a reload would not bring it back. A
   * write refused, the stored cart is removed, unless a reload brings it
   * back anyway, and the write tried once more, as a full disk may take
   * it then: storage holds this cart or none, so a reload never brings
   * back lines recorded or discarded since (Ali, inventory-tng-xm07). One
   * the browser would not remove may still hold old lines.
   */
  private write(next: Cart): boolean {
    this.cart.value = next;
    const kept = () => storeCart(next, this.storage) || this.reloadBrings(next);
    this.kept = kept() || (removeStored(this.storage, STORAGE_KEY) && kept());
    return this.kept;
  }

  /** Whether a reload, reading what is stored, brings back `cart`: the
   * same revision, only its time older (touch), or both empty. */
  private reloadBrings(cart: Cart): boolean {
    const stored = loadCart(this.storage);
    return (
      stored.revision === cart.revision ||
      (stored.lines.length === 0 && cart.lines.length === 0)
    );
  }

  /** Change what the cart holds, each count typed at the size this tab's
   * catalogue shows (sized). False if a reload would not bring it back
   * (write). */
  change(next: Cart, now = Date.now()): boolean {
    const empty = next.lines.length === 0;
    // Its key from its first line, so whichever tab records it sends that
    // key. Emptied, it is no longer that batch: what fills it next is new.
    const { batchKey, changedFrom, ...rest } = sized(
      this.cart.value,
      next,
      this.catalogue,
    );
    return this.write({
      ...rest,
      ...(empty
        ? {}
        : {
            batchKey: batchKey ?? uuid(),
            ...(changedFrom === undefined || batchKey === undefined
              ? {}
              : { changedFrom }),
          }),
      revision: uuid(),
      usedAt: now,
      startedAt: empty ? 0 : next.startedAt || now,
    });
  }

  /** Mark the cart used now, changing nothing in it. False if a reload
   * would not bring it back (write). */
  touch(now = Date.now()): boolean {
    return this.write({ ...this.cart.value, usedAt: now });
  }

  /** The cart to save; or null when another tab has changed it since, and
   * this tab now shows that tab's cart instead. An empty cart kept with
   * none stored is not taken as changed (reloadBrings). */
  forSaving(): Cart | null {
    if (this.kept && !this.reloadBrings(this.cart.value)) {
      this.cart.value = this.stored();
      return null;
    }
    return this.cart.value;
  }

  /** A newer catalogue: choices it no longer has are cleared. */
  useCatalogue(catalogue: Catalogue): void {
    this.catalogue = catalogue;
    this.cart.value = reconcile(this.cart.value, catalogue);
  }

  /** Follow other tabs' changes, calling `followed` after each, told
   * whether it gave the cart another revision, and whether it holds
   * amounts typed in a unit this tab's catalogue does not show: another
   * tab's may be the newer, so they are kept, not cleared, until this
   * tab's is read again (useCatalogue). Storage then holds the cart this
   * tab shows, so it is kept. Returns what stops it. */
  listen(followed?: (revised: boolean, behind: boolean) => void): () => void {
    return onStoredChange([STORAGE_KEY], () => {
      const before = this.cart.value.revision;
      const stored = loadCart(this.storage);
      const { items } = indexOf(this.catalogue);
      const behind = stored.lines.some((line) => {
        const item = items.get(line.itemId);
        return (
          item !== undefined && unitChanged(line, item) && holdsAmounts(line)
        );
      });
      this.cart.value = reconcile(stored, this.catalogue, !behind);
      this.kept = true;
      followed?.(this.cart.value.revision !== before, behind);
    });
  }
}

/** A batch's two sides, of which a kind has one or both (SIDES). */
export type Side = "from" | "to";

/**
 * The sides a place may fill for the cart's kind, the one still empty first:
 * a volunteer is asked From or To, offered that one.
 */
export function placeSides(cart: Cart): Side[] {
  const sides = SIDES[cart.kind];
  const offered = (["from", "to"] as const).filter((side) => sides[side]);
  return offered.sort(
    (a, b) => Number(filled(cart, a)) - Number(filled(cart, b)),
  );
}

function filled(cart: Cart, side: Side): boolean {
  return (side === "from" ? cart.fromId : cart.toId) !== null;
}

/**
 * The cart with choices the catalogue no longer has (retired since it was
 * filled) cleared, so what the lists show and what is saved agree. Lines
 * stay: a line whose item has gone says so, for the volunteer to remove.
 * A count of a package the catalogue now gives another size than it was
 * counted at is cleared, keeping that size, so the line says so (resized)
 * until a count is typed again (inventory-tng-0qi); so is all a line holds
 * typed in a unit the item is no longer counted in (unitChanged), unless
 * `units` is false.
 */
export function reconcile(
  cart: Cart,
  catalogue: Catalogue,
  units = true,
): Cart {
  const { volunteers, places, items } = indexOf(catalogue);
  const place = (placeId: number | null) =>
    placeId !== null && places.has(placeId) ? placeId : null;
  const volunteerId =
    cart.volunteerId !== null && volunteers.has(cart.volunteerId)
      ? cart.volunteerId
      : null;
  const fromId = place(cart.fromId);
  const toId = place(cart.toId);
  const lines = cart.lines.map((line) =>
    recounted(line, items.get(line.itemId), units),
  );
  return volunteerId === cart.volunteerId &&
    fromId === cart.fromId &&
    toId === cart.toId &&
    lines.every((line, at) => line === cart.lines[at])
    ? cart
    : { ...cart, volunteerId, fromId, toId, lines };
}

/** Whether the line's count of this package is to be typed again: the
 * catalogue gives it another size than the count was made at, or the
 * count was cleared so (recounted), whatever size it gives now, as one
 * resized back would otherwise leave it blank unsaid. None of it counted,
 * there is nothing to count again. */
export function resized(line: Line, pack: Package): boolean {
  const counted = line.sizes?.[pack.id];
  const typed = line.packages[pack.id] ?? "";
  return (
    counted !== undefined &&
    (typed === "" ||
      (holdsSome(typed) && thousandths(counted) !== thousandths(pack.size)))
  );
}

/** Whether the line's amounts were typed in another unit than the item is
 * counted in now: they are cleared (reconcile), and the line says so
 * until they are typed again in the new unit (Ali, 2026-11-23,
 * inventory-tng-h7ua). */
export function unitChanged(
  line: Line,
  item: Item,
): line is Line & { unit: string } {
  return line.unit !== undefined && line.unit !== item.unit;
}

type Typed = Pick<Line, "packages" | "loose" | "open">;

/** What is typed on the line, each beside where: counts of packages,
 * loose, and what is taken from open packages. Not what is ticked, so a
 * used-up box ticked on a line cleared as typed in another unit leaves it
 * asking for the amount again (unitChanged). */
function typedOn(line: Typed): [string, string][] {
  return [
    ...Object.entries(line.packages),
    ["loose", line.loose],
    ...Object.entries(line.open ?? {}).flatMap(
      ([key, { amount, now }]): [string, string][] => [
        [`${key} amount`, amount],
        [`${key} now`, now ?? ""],
      ],
    ),
  ];
}

/** Whether anything is typed on the line (typedOn). */
function holdsAmounts(line: Typed): boolean {
  return typedOn(line).some(([, typed]) => typed !== "");
}

/** The line with everything typed on it cleared, keeping the unit it was
 * typed in, so it says so (unitChanged). Its package counts go too: each
 * package's size is in the item's unit, and the server refuses a line in
 * the old one (inventory-tng-57u8). */
function cleared(line: Line): Line {
  const { sizes: _, open, ...rest } = line;
  return {
    ...rest,
    packages: Object.fromEntries(
      Object.keys(line.packages).map((packageId) => [packageId, ""]),
    ),
    loose: "",
    ...(open === undefined
      ? {}
      : {
          open: Object.fromEntries(
            Object.entries(open).map(([key, taken]) => [
              key,
              {
                amount: "",
                whole: false,
                ...(taken.now === undefined ? {} : { now: "" }),
              },
            ]),
          ),
        }),
  };
}

/** The line with its amounts typed in another unit since cleared, if
 * `units`, or its counts of packages resized since. */
function recounted(line: Line, item: Item | undefined, units: boolean): Line {
  if (item !== undefined && unitChanged(line, item)) {
    return units && holdsAmounts(line) ? cleared(line) : line;
  }
  const clear = (item?.packages ?? []).filter(
    (pack) => (line.packages[pack.id] ?? "") !== "" && resized(line, pack),
  );
  if (clear.length === 0) {
    return line;
  }
  const packages = { ...line.packages };
  for (const pack of clear) {
    packages[pack.id] = "";
  }
  return { ...line, packages };
}

/**
 * `next`, changed from `before`, with the size each package was counted
 * at: a count typed or added now, at the size the catalogue shows; one
 * unchanged, at the size it had, as is one of a line new to the cart that
 * brings its sizes (a line put back by Undo, undo-remove.ts). One kept
 * before sizes were (a cart an older build left, put back) is taken as
 * counted at the size shown now, as it was read before; a count blank has
 * none, but one cleared as resized keeps its size until typed again
 * (reconcile). So too the unit its amounts were typed in: the one it
 * has, until any of its amounts change (unitTypedIn); else the one shown
 * now, or none while nothing is typed on it.
 */
export function sized(before: Cart, next: Cart, catalogue: Catalogue): Cart {
  const { items } = indexOf(catalogue);
  const was = new Map(before.lines.map((line) => [line.itemId, line]));
  return {
    ...next,
    lines: next.lines.map(({ sizes: kept, ...line }) => {
      const packs = items.get(line.itemId)?.packages ?? [];
      const previous = was.get(line.itemId);
      const sizes: Record<string, string> = {};
      for (const [packageId, typed] of Object.entries(line.packages)) {
        const counted = kept?.[packageId];
        const pack = packs.find((each) => String(each.id) === packageId);
        if (
          counted !== undefined &&
          (previous === undefined || previous.packages[packageId] === typed)
        ) {
          sizes[packageId] = counted;
        } else if (typed !== "" && pack !== undefined) {
          sizes[packageId] = pack.size;
        }
      }
      const typedIn = unitTypedIn(previous, line, items.get(line.itemId));
      const { unit: _, ...unsized } = line;
      const inUnit =
        typedIn === undefined ? unsized : { ...unsized, unit: typedIn };
      return Object.keys(sizes).length === 0 ? inUnit : { ...inUnit, sizes };
    }),
  };
}

/** The unit `line`'s amounts were typed in (sized): the one it has, until
 * any of them change from `previous`; else the item's, or none while
 * nothing is typed on it. */
function unitTypedIn(
  previous: Line | undefined,
  line: Omit<Line, "sizes">,
  item: Item | undefined,
): string | undefined {
  if (
    line.unit !== undefined &&
    (previous === undefined ||
      JSON.stringify(typedOn(previous)) === JSON.stringify(typedOn(line)))
  ) {
    return line.unit;
  }
  return holdsAmounts(line) ? item?.unit : undefined;
}

/** Whether one more loose of this item cannot be added, as its loose box
 * holds what reads as no amount (bumped leaves it as typed). */
export function looseUnread(cart: Cart, itemId: number): boolean {
  const line = cart.lines.find((each) => each.itemId === itemId);
  return line !== undefined && readBox(line.loose) === null;
}

/** Add one of this item, or of this package of it, to the cart. */
export function addOne(cart: Cart, itemId: number, packageId?: number): Cart {
  return withLineChanged(withLine(cart, itemId), itemId, (line) =>
    bumped(line, packageId),
  );
}

/** The cart with its line for this item changed by `change`. */
export function withLineChanged(
  cart: Cart,
  itemId: number,
  change: (line: Line) => Line,
): Cart {
  return {
    ...cart,
    lines: cart.lines.map((line) =>
      line.itemId === itemId ? change(line) : line,
    ),
  };
}

/** A line with one more of a package of its item, or loose. Loose that
 * reads as no amount, such as one typed with the other decimal separator,
 * is left as typed, for Record to refuse, never counted as none. */
function bumped(line: Line, packageId?: number): Line {
  if (packageId === undefined) {
    const loose = readBox(line.loose);
    return loose === null
      ? line
      : { ...line, loose: boxAmount(loose + THOUSANDTHS_PER_UNIT) };
  }
  return {
    ...line,
    packages: {
      ...line.packages,
      [packageId]: String((count(line.packages[packageId] ?? "") ?? 0) + 1),
    },
  };
}

/** The cart with this item picked once more: its line, made with nothing
 * in it at the top if it has none (newest first; one it has stays where
 * it is), counts the pick (Line.picked). */
export function withLine(cart: Cart, itemId: number): Cart {
  const made = cart.lines.some((line) => line.itemId === itemId)
    ? cart
    : { ...cart, lines: [{ itemId, packages: {}, loose: "" }, ...cart.lines] };
  return withLineChanged(made, itemId, (line) => ({
    ...line,
    picked: (line.picked ?? 0) + 1,
  }));
}

/** The package opening an item opens, for the volunteer to change: with
 * no place yet, its one current package; at a place, sealedHere's. */
export function openingPackage(
  item: Item | undefined,
  placeId: number | null,
  sealed: ReadonlyMap<string, number>,
): Package | undefined {
  if (placeId !== null) {
    return sealedHere(item, placeId, sealed);
  }
  const current = (item?.packages ?? []).filter((each) => !each.retired);
  return current.length === 1 ? current[0] : undefined;
}

/**
 * `next`, changed from `before`, put right where its place or kind moved:
 * what was taken from open packages was where it came from, as it was, so
 * none of it is kept; and, opening, the lines the volunteer has not typed
 * into are filled for the place picked or changed (refilled).
 */
export function placed(
  before: Cart,
  next: Cart,
  catalogue: Catalogue,
  sealed: ReadonlyMap<string, number>,
): Cart {
  if (next.kind === before.kind && next.fromId === before.fromId) {
    return next;
  }
  const lines = next.lines.map(({ open: _, ...line }) => line);
  // Only a place picked or changed while opening is filled again: lines
  // from another kind keep what they hold, there and after, as typed, so
  // loose units picked to move never become packages to open.
  if (next.kind !== before.kind) {
    return { ...next, lines: lines.map((line) => ({ ...line, typed: true })) };
  }
  return next.kind === "open"
    ? { ...next, lines: refilled(lines, catalogue, next.fromId, sealed) }
    : { ...next, lines };
}

/** An opening's lines the volunteer has not typed into, filled again for
 * the place now picked (openingPackage): as many as were added, of the
 * package opened there, or of none (inventory-tng-prp). */
function refilled(
  lines: readonly Line[],
  catalogue: Catalogue,
  placeId: number | null,
  sealed: ReadonlyMap<string, number>,
): Line[] {
  const { items } = indexOf(catalogue);
  return lines.map((line) => {
    if (line.typed) {
      return line;
    }
    const pack = openingPackage(items.get(line.itemId), placeId, sealed);
    return {
      ...line,
      packages:
        pack === undefined ? {} : { [pack.id]: String(line.picked ?? 1) },
      // An opening has none loose (toBatch).
      loose: "",
    };
  });
}

/** The package opening an item at a place opens: its one package with
 * some sealed there (sealedCounts), current or retired; none if several
 * are, or none. */
export function sealedHere(
  item: Item | undefined,
  placeId: number,
  sealed: ReadonlyMap<string, number>,
): Package | undefined {
  const here = (item?.packages ?? []).filter(
    (each) => (sealed.get(sealedKey(each.id, placeId)) ?? 0) > 0,
  );
  return here.length === 1 ? here[0] : undefined;
}

/** Whole packages of one size: how many, and each one's size in
 * thousandths of the item's unit. */
export type Packs = { count: number; size: number };

/** Whether a typed count of packages holds some: blank is none, and one
 * that cannot be read is some, to be questioned rather than dropped. */
export function holdsSome(typed: string): boolean {
  return (count(typed) ?? 1) !== 0;
}

/** Which of a cart line's boxes a refusal is about, for CartLine to mark:
 * the loose box, a package's count by its id, or what is taken from an
 * open package by its key. */
export type Box =
  | { kind: "loose" }
  | { kind: "package"; packageId: number }
  | { kind: "open"; key: string };

/** Why a line has no quantity: the first problem, the box it is about if
 * it is one box, and, when another problem comes first but the loose box
 * cannot be read either, the loose box's own words, so the box CartForm's
 * NOT_ADDED sends the volunteer to is always marked (inventory-tng-weww). */
export type Refusal = {
  problem: Said;
  refused?: OtherSeparator;
  box?: Box;
  looseProblem?: Said;
};

/** A line's quantity in thousandths of the item's unit, and of it, the
 * packages counted, by package, and how much loose; or why it has none
 * (Refusal). */
export function quantityOf(
  line: Line,
  item: Item,
):
  | {
      amount: number;
      sealed: Map<number, Packs>;
      loose: number;
      taken: Map<string, { amount: number; whole: boolean }>;
    }
  | Refusal {
  const loose = readBox(line.loose);
  const looseWords = () =>
    boxProblem(
      line.loose,
      say(LOOSE_DECIMALS, { unit: item.unit, places: AMOUNT_PLACES }),
    );
  /** Refused for `said`, about `box`, the loose box's words beside. */
  const refuse = (
    said: { problem: Said; refused?: OtherSeparator },
    box?: Box,
  ): Refusal => ({
    ...said,
    ...(box && { box }),
    ...(loose === null && { looseProblem: looseWords().problem }),
  });
  const current = new Set(item.packages.map((pack) => String(pack.id)));
  for (const [packageId, typed] of Object.entries(line.packages)) {
    if (!current.has(packageId) && holdsSome(typed)) {
      return refuse({ problem: [say(PACKAGE_NO_LONGER_USED)] });
    }
  }
  let amount = 0;
  const sealed = new Map<number, Packs>();
  for (const pack of item.packages) {
    const packs = count(line.packages[pack.id] ?? "");
    // As counted: one resized since is refused when saved, not recounted.
    const size = thousandths(line.sizes?.[pack.id] ?? pack.size);
    if (packs === null || size === null) {
      return refuse(
        {
          problem: sayParts(COUNT_WHOLE_PACKAGES, {
            package: { quoted: pack.name },
          }),
        },
        { kind: "package", packageId: pack.id },
      );
    }
    if (packs > 0) {
      sealed.set(pack.id, { count: packs, size });
    }
    amount += packs * size;
  }
  if (loose === null) {
    return { ...looseWords(), box: { kind: "loose" } };
  }
  amount += loose;
  const taken = new Map<string, { amount: number; whole: boolean }>();
  for (const [key, { amount: typed, whole }] of Object.entries(
    line.open ?? {},
  )) {
    const part = readBox(typed);
    if (part === null) {
      return refuse(
        boxProblem(typed, say(OPEN_DECIMALS, { places: AMOUNT_PLACES })),
        {
          kind: "open",
          key,
        },
      );
    }
    if (part > 0) {
      taken.set(key, { amount: part, whole });
    }
    amount += part;
  }
  // Each part is a line of its own (toBatch), so each is whole.
  const parts = [
    loose,
    ...[...sealed.values()].map((p) => p.count * p.size),
    ...[...taken.values()].map((t) => t.amount),
  ];
  const inWholeUnits = (part: number) => wholeEnough(item.unit, part);
  if (!parts.every(inWholeUnits)) {
    // The box typed in a fraction; a package's part is its size's, no box.
    const fraction = { problem: [say(WHOLE_UNITS)] };
    if (!inWholeUnits(loose)) {
      return refuse(fraction, { kind: "loose" });
    }
    const cut = [...taken].find(([, t]) => !inWholeUnits(t.amount))?.[0];
    return refuse(
      fraction,
      cut === undefined ? undefined : { kind: "open", key: cut },
    );
  }
  return { amount, sealed, loose, taken };
}

export const PACKAGE_NO_LONGER_USED = msg`A package counted here is no longer used. Remove the line and add it again.`;
export const COUNT_WHOLE_PACKAGES = msg`Count whole packages: {package}.`;
export const LOOSE_DECIMALS = msg`Enter the loose amount in {unit}, up to {places} decimals.`;
export const OPEN_DECIMALS = msg`Enter how much from the open package, up to {places} decimals.`;
export const WHOLE_UNITS = msg`Counted in whole units.`;
/** A box refused for the decimal separator its page does not use: the
 * other read as thousands (shared/quantity.Unreadable), which naming
 * the decimal one would turn into a thousandth of what was meant; else
 * the one to use, named (Ali, 2026-11-23, inventory-tng-qnwz). As the
 * server's web/forms.THOUSANDS and use_separator say. */
export const THOUSANDS = msg`Type thousands without a separator: 1000.`;
export const USE_SEPARATOR = msg`Use “{separator}” for decimals, such as {example}.`;

/** What a box says of the other decimal separator than its page's. */
export function separatorWords(refused: OtherSeparator): string {
  const separator = boxSeparator();
  return refused === "thousands"
    ? say(THOUSANDS)
    : say(USE_SEPARATOR, { separator, example: `0${separator}5` });
}

/** What a box says of what is typed in it, which it refused: `otherwise`,
 * unless it was for the other decimal separator than its page's, said as
 * separatorWords, and which it was. */
function boxProblem(
  typed: string,
  otherwise: string,
): { problem: Said; refused?: OtherSeparator } {
  const refused = otherSeparator(typed);
  return refused === null
    ? { problem: [otherwise] }
    : { problem: [separatorWords(refused)], refused };
}

/** What the page says beside a retired item, or package. */
export const RETIRED = msg`retired: taking only`;

/** A line in words: its item's name, then what of it, or what is wrong. */
export const ITEM_SAYS = msg({
  message: "{name}: {what}",
  comment: "An item's name, then how much of it, or what is wrong with it.",
});
