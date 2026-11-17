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
import { msg } from "@lingui/core/macro";
import { type Signal, signal } from "@preact/signals";
import { v4 as uuid, v5 as uuidFrom } from "uuid";
import * as v from "valibot";
import type { Schemas } from "./api/client";
import { id } from "./api/replies";
import { BUILD } from "./build";
import { say, sayParts } from "./i18n";
import {
  BatchKey,
  BatchLine,
  type Draft,
  type Entry,
  fromOpen,
  type Said,
  type Summary,
} from "./outbox";
import { count, decimal, sent, thousandths } from "./quantity";
import { onStoredChange, readStored, writeStored } from "./stored";
import { groups, type Rules, respell } from "./units";

/** Versioned as the outbox's is (outbox.ts): each build reads its own.
 * v2's is stamped with the build that wrote it, may keep a batch key
 * (batchKey: made with its first line since inventory-tng-e2d; a build
 * before kept one only from a save put back), and keeps the size each
 * package was counted at (sizes). */
export const STORAGE_KEY = "inventory.cart.v2";
/** The server's limits (scan/batch_schemas.py), checked here too, so a
 * batch it would refuse is never queued and the cart is kept to fix it. */
const MAX_ID = 2 ** 31 - 1;
/** numeric(12,3): under a billion units, in thousandths. */
const MAX_AMOUNT = 10 ** 12;
/** Lines in a batch (batch_schemas.MAX_LINES): a cart line may be several. */
const MAX_LINES = 500;
/** Batch keys a catalogue is asked about (scan/catalogue_api.py, MOST_KEYS). */
export const MAX_KEYS_ASKED = 100;

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
   * empty: how long a batch took from then to saving (telemetry.ts). */
  startedAt: v.optional(v.number(), 0),
  /** New with every change to what the cart holds: how a tab tells whether
   * another tab has changed it since (CartStore). */
  revision: v.optional(v.string(), ""),
  /** The key it is to be recorded under: made with its first line, or the
   * key of what an older build left that filled it (older.ts), kept until
   * the cart is emptied. */
  batchKey: v.exactOptional(BatchKey),
  /** The lines, as a batch sends them, the cart held when put back under
   * its key (older.ts, toCart), kept with the key: what a volunteer then
   * takes off, and another tab recorded under the key meanwhile, is kept
   * for an administrator (outbox.ts, changed_from; inventory-tng-0gg). A
   * build before drops it from what it writes back: then nothing is kept. */
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
 * tab of an older one learns a newer one is out (build.ts). */
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
  /** Whether the browser kept the last write. If not, storage cannot tell
   * what another tab did, and this tab's cart is the one to save. */
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

  private write(next: Cart): boolean {
    this.cart.value = next;
    this.kept = storeCart(next, this.storage);
    return this.kept;
  }

  /** Change what the cart holds, each count typed at the size this tab's
   * catalogue shows (sized). False if the browser would not keep it. */
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

  /** Mark the cart used now, changing nothing in it. */
  touch(now = Date.now()): boolean {
    return this.write({ ...this.cart.value, usedAt: now });
  }

  /** The cart to save; or null when another tab has changed it since, and
   * this tab now shows that tab's cart instead. */
  forSaving(): Cart | null {
    const stored = loadCart(this.storage);
    if (this.kept && stored.revision !== this.cart.value.revision) {
      this.cart.value = reconcile(stored, this.catalogue);
      return null;
    }
    return this.cart.value;
  }

  /** A newer catalogue: choices it no longer has are cleared. */
  useCatalogue(catalogue: Catalogue): void {
    this.catalogue = catalogue;
    this.cart.value = reconcile(this.cart.value, catalogue);
  }

  /** Follow other tabs' changes. Returns what stops it. */
  listen(): () => void {
    return onStoredChange([STORAGE_KEY], () => {
      this.cart.value = this.stored();
    });
  }
}

/** Lookups worked out once per catalogue, not on every keystroke. */
type Index = {
  items: Map<number, Item>;
  /** Each volunteer's name, by id. */
  volunteers: Map<number, string>;
  /** Each place's name with the places it sits in, by id. */
  places: Map<number, string>;
  /** Every place, sorted by that name. */
  placeList: { id: number; name: string }[];
  /** What search looks through, per item, bare: its details as spelt, then
   * the server's folded text. */
  haystacks: Map<number, string>;
  /** The reading rules, by a number and its unit as typed (units.Rules). */
  rules: Rules;
  /** Label codes and items' old codes, upper case, to what they name. */
  codes: Map<string, Named>;
};

/** What a label or an old code names: an item, a package of one, a place. */
export type Named =
  | { kind: "item"; itemId: number; packageId?: number }
  | { kind: "place"; locationId: number };

/** A batch's two sides, of which a kind has one or both (SIDES). */
export type Side = "from" | "to";

const indexes = new WeakMap<Catalogue, Index>();

export function indexOf(catalogue: Catalogue): Index {
  const cached = indexes.get(catalogue);
  if (cached !== undefined) {
    return cached;
  }
  const byId = new Map(catalogue.locations.map((x) => [x.id, x]));
  const places = new Map<number, string>();
  for (const location of catalogue.locations) {
    const names: string[] = [];
    for (
      let at: typeof location | undefined = location;
      at !== undefined && names.length < 10;
      at = at.parent_id === null ? undefined : byId.get(at.parent_id)
    ) {
      names.unshift(at.name);
    }
    places.set(location.id, names.join(" › "));
  }
  const rules: Rules = new Map(
    catalogue.reading_rules.map((rule) => [rule.token, rule.reading]),
  );
  // Labels after old codes, so a label wins if the two ever clash.
  const codes: Index["codes"] = new Map();
  for (const item of catalogue.items) {
    if (item.old_code !== null) {
      codes.set(item.old_code.toUpperCase(), { kind: "item", itemId: item.id });
    }
  }
  for (const label of catalogue.labels) {
    const code = label.code.toUpperCase();
    if (label.location_id !== null) {
      codes.set(code, { kind: "place", locationId: label.location_id });
    } else if (label.item_id !== null) {
      codes.set(
        code,
        label.package_id === null
          ? { kind: "item", itemId: label.item_id }
          : {
              kind: "item",
              itemId: label.item_id,
              packageId: label.package_id,
            },
      );
    }
  }
  const index: Index = {
    items: new Map(catalogue.items.map((item) => [item.id, item])),
    volunteers: new Map(
      catalogue.volunteers.map((each) => [each.id, each.name]),
    ),
    places,
    placeList: [...places]
      .map(([placeId, name]) => ({ id: placeId, name }))
      .sort((a, b) => a.name.localeCompare(b.name)),
    haystacks: new Map(
      catalogue.items.map((item) => [
        item.id,
        // Each field bare alone, as typed and respelt, as the server keeps
        // each (models.held), so no field's number reads the next's start
        // as a unit; the server's text, kept so already, only in lower case.
        [
          ...[
            item.name,
            item.manufacturer,
            item.part_number,
            item.old_code,
            item.category,
            ...item.aliases,
          ].flatMap((field) => bares(field ?? "", rules)),
          item.search_text.toLowerCase(),
        ]
          .filter(Boolean)
          .join(" "),
      ]),
    ),
    codes,
    rules,
  };
  indexes.set(catalogue, index);
  return index;
}

/**
 * The cart with choices the catalogue no longer has (retired since it was
 * filled) cleared, so what the lists show and what is saved agree. Lines
 * stay: a line whose item has gone says so, for the volunteer to remove.
 * A count of a package the catalogue now gives another size than it was
 * counted at is cleared, keeping that size, so the line says so (resized)
 * until a count is typed again (inventory-tng-0qi).
 */
export function reconcile(cart: Cart, catalogue: Catalogue): Cart {
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
    recounted(line, items.get(line.itemId)),
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

/** The line with its counts of packages resized since cleared. */
function recounted(line: Line, item: Item | undefined): Line {
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
 * unchanged, at the size it had. One kept before sizes were (a cart an
 * older build left, put back) is taken as counted at the size shown now,
 * as it was read before; a count blank has none, but one cleared as
 * resized keeps its size until typed again (reconcile).
 */
export function sized(before: Cart, next: Cart, catalogue: Catalogue): Cart {
  const { items } = indexOf(catalogue);
  const was = new Map(before.lines.map((line) => [line.itemId, line]));
  return {
    ...next,
    lines: next.lines.map(({ sizes: kept, ...line }) => {
      const packs = items.get(line.itemId)?.packages ?? [];
      const sizes: Record<string, string> = {};
      for (const [packageId, typed] of Object.entries(line.packages)) {
        const counted = kept?.[packageId];
        const pack = packs.find((each) => String(each.id) === packageId);
        if (
          counted !== undefined &&
          was.get(line.itemId)?.packages[packageId] === typed
        ) {
          sizes[packageId] = counted;
        } else if (typed !== "" && pack !== undefined) {
          sizes[packageId] = pack.size;
        }
      }
      return Object.keys(sizes).length === 0 ? line : { ...line, sizes };
    }),
  };
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

/** A line with one more of a package of its item, or loose. */
function bumped(line: Line, packageId?: number): Line {
  return packageId === undefined
    ? { ...line, loose: decimal((thousandths(line.loose) ?? 0) + 1000) }
    : {
        ...line,
        packages: {
          ...line.packages,
          [packageId]: String((count(line.packages[packageId] ?? "") ?? 0) + 1),
        },
      };
}

/** The cart with this item picked once more: its line, made with nothing
 * in it if it has none, counts the pick (Line.picked). */
export function withLine(cart: Cart, itemId: number): Cart {
  const made = cart.lines.some((line) => line.itemId === itemId)
    ? cart
    : { ...cart, lines: [...cart.lines, { itemId, packages: {}, loose: "" }] };
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

function sealedKey(packageId: number, placeId: number): string {
  return `${packageId}@${placeId}`;
}

/** Whole packages of one size: how many, and each one's size in
 * thousandths of the item's unit. */
type Packs = { count: number; size: number };

/** Whether a typed count of packages holds some: blank is none, and one
 * that cannot be read is some, to be questioned rather than dropped. */
export function holdsSome(typed: string): boolean {
  return (count(typed) ?? 1) !== 0;
}

/** A line's quantity in thousandths of the item's unit, and of it, the
 * packages counted, by package, and how much loose; or why it has none. */

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
  | { problem: Said } {
  const current = new Set(item.packages.map((pack) => String(pack.id)));
  for (const [packageId, typed] of Object.entries(line.packages)) {
    if (!current.has(packageId) && holdsSome(typed)) {
      return { problem: [say(PACKAGE_NO_LONGER_USED)] };
    }
  }
  let amount = 0;
  const sealed = new Map<number, Packs>();
  for (const pack of item.packages) {
    const packs = count(line.packages[pack.id] ?? "");
    // As counted: one resized since is refused when saved, not recounted.
    const size = thousandths(line.sizes?.[pack.id] ?? pack.size);
    if (packs === null || size === null) {
      return {
        problem: sayParts(COUNT_WHOLE_PACKAGES, {
          package: { quoted: pack.name },
        }),
      };
    }
    if (packs > 0) {
      sealed.set(pack.id, { count: packs, size });
    }
    amount += packs * size;
  }
  const loose = thousandths(line.loose);
  if (loose === null) {
    return { problem: [say(LOOSE_DECIMALS, { unit: item.unit })] };
  }
  amount += loose;
  const taken = new Map<string, { amount: number; whole: boolean }>();
  for (const [key, { amount: typed, whole }] of Object.entries(
    line.open ?? {},
  )) {
    const part = thousandths(typed);
    if (part === null) {
      return { problem: [say(OPEN_DECIMALS)] };
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
  if (item.unit === "each" && parts.some((part) => part % 1000 !== 0)) {
    return { problem: [say(WHOLE_UNITS)] };
  }
  return { amount, sealed, loose, taken };
}

export const PACKAGE_NO_LONGER_USED = msg`A package counted here is no longer used. Remove the line and add it again.`;
export const COUNT_WHOLE_PACKAGES = msg`Count whole packages: {package}.`;
export const LOOSE_DECIMALS = msg`Enter the loose amount in {unit}, up to 3 decimals.`;
export const OPEN_DECIMALS = msg`Enter how much from the open package, up to 3 decimals.`;
export const WHOLE_UNITS = msg`Counted in whole units.`;

/** What the page says beside a retired item, or package. */
export const RETIRED = msg`retired: taking only`;

/** A line in words: its item's name, then what of it, or what is wrong. */
export const ITEM_SAYS = msg({
  message: "{name}: {what}",
  comment: "An item's name, then how much of it, or what is wrong with it.",
});

/** Why a cart cannot be recorded yet (toBatch). Those of a line start
 * with its item's name. */
export const AN_ITEM = msg`An item`;
export const OPEN_NOW_WHOLE = msg`{name}: enter what is on the open package, in whole units, or leave it blank.`;
export const OPEN_NOW_DECIMALS = msg`{name}: enter what is on the open package, up to 3 decimals, or leave it blank.`;
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
export const MOVE = msg`Move: {items} from {from} to {to}`;
export const RECEIPT = msg`Receipt: {items} to {to}`;
export const INSTALL = msg`Install: {items} from {from}`;
export const INSTALL_NUMBERED = msg`Install: {items} from {from} for install {number}`;
export const OPENING = msg`Opening: {items} at {from}`;
export const REPORT = msg`Used up: {packages} at {from}`;
export const LENGTHS = msg`What's on it: {packages} at {from}`;
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
): Batch | { problems: Said[] } {
  const reconciled = reconcile(filled, catalogue);
  // Only what the page offers to take from, where it comes from.
  const here = new Map(
    (all ?? [])
      .filter((each) => each.location_id === reconciled.fromId)
      .map((each) => [each.key, each]),
  );
  const cart = all === undefined ? reconciled : withOffered(reconciled, here);
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
  // Each problem with the names in it quoted, as no number of a name is a
  // count the page wrote (outbox.Said).
  const problems: Said[] = [];
  for (const line of cart.lines) {
    const item = index.items.get(line.itemId);
    const named = item === undefined ? say(AN_ITEM) : { quoted: item.name };
    const each = item?.unit === "each";
    for (const [key, { now }] of Object.entries(line.open ?? {})) {
      if (now === undefined || now.trim() === "") {
        continue;
      }
      const left = thousandths(now);
      if (left === null || (each && left % 1000 !== 0)) {
        problems.push(
          sayParts(each ? OPEN_NOW_WHOLE : OPEN_NOW_DECIMALS, { name: named }),
        );
      } else if (left >= MAX_AMOUNT) {
        problems.push(sayParts(TOO_MUCH, { name: named }));
      } else {
        lengths.push({ open_package_key: key, left: decimal(left) });
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
    const quantity = quantityOf(line, item);
    if ("problem" in quantity) {
      problems.push(sayParts(ITEM_SAYS, { name, what: quantity.problem }));
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
      lines.push(...openings(item, quantity.sealed, cart.batchKey));
    } else if (quantity.amount > 0) {
      // Each package counted on a line of its own, sealed, so stock keeps
      // them sealed where they arrive (docs/decisions/0020); then loose.
      for (const [packageId, { count, size }] of quantity.sealed) {
        lines.push(packageLine(item.id, packageId, size, count * size));
      }
      if (quantity.loose > 0) {
        lines.push({ item_id: item.id, quantity: decimal(quantity.loose) });
      }
      // From an open package: cut loose, or, moving it, all of it whole.
      for (const [key, { amount, whole }] of quantity.taken) {
        lines.push({
          item_id: item.id,
          quantity: decimal(amount),
          open_package_key: key,
          ...(whole && cart.kind === "move" ? { whole: true } : {}),
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
    return { problems };
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
  return { draft, summary: { from: place(fromId), to: place(toId) } };
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
 * without one (ledger._opening_keys), so the same cart recorded again, by
 * any tab, is the same batch, which the server takes once.
 */
function openings(
  item: Item,
  sealed: Map<number, Packs>,
  batchKey: string | undefined,
): Draft["lines"] {
  const followed = new Set(
    item.packages.filter((pack) => pack.track_opened).map((pack) => pack.id),
  );
  return [...sealed].flatMap(([packageId, { count, size }]) =>
    followed.has(packageId)
      ? Array.from({ length: count }, (_, each) => ({
          ...packageLine(item.id, packageId, size, size),
          open_package_key:
            batchKey === undefined
              ? uuid()
              : uuidFrom(`open ${item.id} ${packageId} ${each}`, batchKey),
        }))
      : [packageLine(item.id, packageId, size, count * size)],
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

/** `amount` of the package `packageId`, in thousandths of the item's unit,
 * with the size it was counted in: one resized since is refused, not
 * recounted. */
function packageLine(
  itemId: number,
  packageId: number,
  size: number,
  amount: number,
): PackageLine {
  return {
    item_id: itemId,
    quantity: decimal(amount),
    package_id: packageId,
    package_size: decimal(size),
  };
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

/** offered's open packages of an item at a place, most left first, so
 * those showing nothing, or less, come last (docs/decisions/0020). */
export function offeredAt(
  all: readonly Offered[],
  itemId: number,
  placeId: number | null,
): Offered[] {
  return all
    .filter((each) => each.item_id === itemId && each.location_id === placeId)
    .sort((a, b) => b.left - a.left);
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
  const shortBelow =
    open.short_below === null ? null : thousandths(open.short_below);
  const short = shortBelow !== null && after < shortBelow;
  return { after, ending: short ? "short" : null };
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

/** The cart with what it takes from open packages no longer offered left
 * out: reported or closed elsewhere, or moved, since it was typed, its
 * field is gone from the page, so what was typed there goes too. */
function withOffered(cart: Cart, here: ReadonlyMap<string, unknown>): Cart {
  return {
    ...cart,
    lines: cart.lines.map((line) => ({
      ...line,
      open: Object.fromEntries(
        Object.entries(line.open ?? {}).filter(([key]) => here.has(key)),
      ),
    })),
  };
}

/** Items whose name, maker, part number, code or category holds every word
 * of what was typed, as typed or respelt (`bares`), or every group of it,
 * each as typed or respelt (units `groups`: `100 feet 48V` finds `Cable
 * 100ft 48 VDC`): in the details as spelt or the server's folded text
 * (docs/decisions/0021, 2026-11-12, and Ali, 2026-11-15). */
export function search(catalogue: Catalogue, text: string, limit = 20): Item[] {
  const { haystacks, rules } = indexOf(catalogue);
  const forms = bares(text, rules)
    .map((form) =>
      // A lone accent is no word: not a search matching everything.
      form.split(/\s+/).filter((word) => word !== ""),
    )
    .filter((words) => words.length > 0);
  if (forms.length === 0) {
    return [];
  }
  const grouped = groups(text.normalize("NFKD"), rules)
    .map((group) => [...new Set(group.map(unmarked))].filter(Boolean))
    .filter((group) => group.length > 0);
  return catalogue.items
    .filter((item) => {
      const haystack = haystacks.get(item.id) ?? "";
      const holds = (form: string) => haystack.includes(form);
      return (
        forms.some((words) => words.every(holds)) ||
        (grouped.length > 1 && grouped.every((group) => group.some(holds)))
      );
    })
    .slice(0, limit);
}

/** `text` bare as typed, its units as they are, then bare with each unit
 * right after a number spelt as the server spells it (`respell`) if that
 * differs, as the server keeps and matches it (models.folds): so "feet",
 * "48 v" in "48 vdc" and a half-typed "10 μ" find what they did before
 * units were read; `rules`, the reading rules, read first. Bare is in
 * lower case, decomposed by compatibility, and without combining marks:
 * "é" is "e", fullwidth "ｃａｆｅ" is "cafe", "ﬁ" is "fi" and, respelt,
 * "10 µF" is "10uf"; "ł", "ø", "æ" and "ß", which do not decompose, stay
 * as they are, and so do Greek letters, but in units. */
export function bares(text: string, rules?: Rules): string[] {
  const decomposed = text.normalize("NFKD");
  const typed = unmarked(decomposed);
  const respelling = respell(decomposed, rules);
  if (respelling === decomposed) return [typed];
  const respelt = unmarked(respelling);
  return respelt === typed ? [typed] : [typed, respelt];
}

const unmarked = (decomposed: string): string =>
  decomposed.replace(/\p{M}/gu, "").toLowerCase();
