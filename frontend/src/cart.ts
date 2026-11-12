/**
 * The batch being filled on this browser. Kept in localStorage so a reload
 * or a closed tab loses nothing, and so the next batch starts with the same
 * name, kind and places (this phone's last choices). Tabs share it.
 *
 * A cart has no idempotency key: the outbox gives the batch one when it is
 * queued, before anything is sent, so two tabs never share a key.
 */
import { type Signal, signal } from "@preact/signals";
import { v4 as uuid } from "uuid";
import * as v from "valibot";
import type { Schemas } from "./api/client";
import { id } from "./api/replies";
import type { Draft, Entry } from "./outbox";
import { count, decimal, sent, thousandths } from "./quantity";
import { onStoredChange, readStored, writeStored } from "./stored";

export const STORAGE_KEY = "inventory.cart.v1";
/** The server's limits (ledger.py), checked here too, so a batch it would
 * refuse is never queued and the cart is kept to fix it. */
const MAX_ID = 2 ** 31 - 1;
/** numeric(12,3): under a billion units, in thousandths. */
const MAX_AMOUNT = 10 ** 12;
/** Lines in a batch (ledger.MAX_LINES): a cart line may be several. */
const MAX_LINES = 500;
/** Batch keys a catalogue is asked about (catalogue.py, MOST_KEYS). */
const MAX_KEYS_ASKED = 100;

export type Catalogue = Schemas["Catalogue"];
export type Item = Schemas["ItemEntry"];

const LineSchema = v.object({
  itemId: id,
  /** Whole packages, by package id, as typed. */
  packages: v.record(v.string(), v.string()),
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
});

export type Line = v.InferOutput<typeof LineSchema>;
export type Cart = v.InferOutput<typeof CartSchema>;
export type Kind = Cart["kind"];

/** Which sides a kind has: a receipt comes from outside, an install goes to
 * one (the ledger's rule, in ledger.py). */
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

/** False if the browser would not keep it. */
export function storeCart(
  cart: Cart,
  storage: Storage = localStorage,
): boolean {
  return writeStored(storage, STORAGE_KEY, cart);
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

  /** Change what the cart holds. False if the browser would not keep it. */
  change(next: Cart, now = Date.now()): boolean {
    const startedAt = next.lines.length === 0 ? 0 : next.startedAt || now;
    return this.write({ ...next, revision: uuid(), usedAt: now, startedAt });
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
    return onStoredChange(STORAGE_KEY, () => {
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
  /** What search looks through, per item, in lower case. */
  haystacks: Map<number, string>;
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
        [
          item.name,
          item.manufacturer,
          item.part_number,
          item.old_code,
          item.category,
        ]
          .filter(Boolean)
          .join(" ")
          .toLowerCase(),
      ]),
    ),
    codes,
  };
  indexes.set(catalogue, index);
  return index;
}

/**
 * The cart with choices the catalogue no longer has (retired since it was
 * filled) cleared, so what the lists show and what is saved agree. Lines
 * stay: a line whose item has gone says so, for the volunteer to remove.
 */
export function reconcile(cart: Cart, catalogue: Catalogue): Cart {
  const { volunteers, places } = indexOf(catalogue);
  const place = (placeId: number | null) =>
    placeId !== null && places.has(placeId) ? placeId : null;
  const volunteerId =
    cart.volunteerId !== null && volunteers.has(cart.volunteerId)
      ? cart.volunteerId
      : null;
  const fromId = place(cart.fromId);
  const toId = place(cart.toId);
  return volunteerId === cart.volunteerId &&
    fromId === cart.fromId &&
    toId === cart.toId
    ? cart
    : { ...cart, volunteerId, fromId, toId };
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
): Item["packages"][number] | undefined {
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
  return {
    ...next,
    // Only a place picked or changed while opening: lines from another
    // kind keep what they hold.
    lines:
      next.kind === "open" && before.kind === "open"
        ? refilled(lines, catalogue, next.fromId, sealed)
        : lines,
  };
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
  placeId: number | null,
  sealed: ReadonlyMap<string, number>,
): Item["packages"][number] | undefined {
  if (placeId === null) {
    return undefined;
  }
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
  | { problem: string } {
  const current = new Set(item.packages.map((pack) => String(pack.id)));
  for (const [packageId, typed] of Object.entries(line.packages)) {
    if (!current.has(packageId) && (count(typed) ?? 1) !== 0) {
      return {
        problem:
          "A package counted here is no longer used. Remove the line and add it again.",
      };
    }
  }
  let amount = 0;
  const sealed = new Map<number, Packs>();
  for (const pack of item.packages) {
    const packs = count(line.packages[pack.id] ?? "");
    const size = thousandths(pack.size);
    if (packs === null || size === null) {
      return { problem: `Count whole packages: ${pack.name}.` };
    }
    if (packs > 0) {
      sealed.set(pack.id, { count: packs, size });
    }
    amount += packs * size;
  }
  const loose = thousandths(line.loose);
  if (loose === null) {
    return {
      problem: `Enter the loose amount in ${item.unit}, up to 3 decimals.`,
    };
  }
  amount += loose;
  const taken = new Map<string, { amount: number; whole: boolean }>();
  for (const [key, { amount: typed, whole }] of Object.entries(
    line.open ?? {},
  )) {
    const part = thousandths(typed);
    if (part === null) {
      return {
        problem: `Enter how much from the open package, up to 3 decimals.`,
      };
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
    return { problem: "Counted in whole units." };
  }
  return { amount, sealed, loose, taken };
}

const KINDS: Record<Draft["kind"], string> = {
  move: "Move",
  receipt: "Receipt",
  install: "Install",
  open: "Opening",
  report: "Used up",
};

/** What the page says beside a retired item, or package. */
export const RETIRED = "retired: taking only";

/**
 * The cart as the batch endpoint takes it, less the key the outbox adds,
 * and in words for the outbox; or what still needs doing before it can be
 * saved.
 */
export function toBatch(
  filled: Cart,
  catalogue: Catalogue,
  all?: readonly Offered[],
): { draft: Draft; what: string } | { problems: string[] } {
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
  const problems: string[] = [];
  for (const line of cart.lines) {
    const named = index.items.get(line.itemId)?.name ?? "An item";
    for (const [key, { now }] of Object.entries(line.open ?? {})) {
      if (now === undefined || now.trim() === "") {
        continue;
      }
      const left = thousandths(now);
      const each = index.items.get(line.itemId)?.unit === "each";
      if (left === null || (each && left % 1000 !== 0)) {
        problems.push(
          `${named}: enter what is on the open package${each ? ", in whole units" : ", up to 3 decimals"}, or leave it blank.`,
        );
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
    problems.push("Pick your name.");
  }
  if (sides.from && fromId === null) {
    problems.push("Pick where it comes from.");
  }
  if (sides.to && toId === null) {
    problems.push("Pick where it goes.");
  }
  if (fromId !== null && fromId === toId) {
    problems.push("From and to must be different places.");
  }
  const installNumber = kind === "install" ? cart.installNumber.trim() : "";
  const install = count(installNumber);
  if (install === null) {
    problems.push("An install number is digits only.");
  } else if (installNumber !== "" && (install < 1 || install > MAX_ID)) {
    problems.push("That install number is out of range.");
  }
  const lines: Draft["lines"] = [];
  let items = 0;
  for (const line of cart.lines) {
    const item = index.items.get(line.itemId);
    if (item === undefined) {
      problems.push(
        "An item in the cart is no longer in the catalogue. Remove it.",
      );
      continue;
    }
    if (item.retired && cart.kind === "receipt") {
      problems.push(`${item.name}: ${RETIRED}, so it can't be received.`);
      continue;
    }
    if (cart.kind === "receipt") {
      for (const pack of item.packages) {
        if (pack.retired && (count(line.packages[pack.id] ?? "") ?? 0) > 0) {
          problems.push(
            `${item.name}, ${pack.name}: ${RETIRED}, so it can't be received.`,
          );
        }
      }
    }
    const quantity = quantityOf(line, item);
    if ("problem" in quantity) {
      problems.push(`${item.name}: ${quantity.problem}`);
    } else if (quantity.amount >= MAX_AMOUNT) {
      problems.push(`${item.name}: That is more than the inventory can count.`);
    } else if (
      (cart.kind === "open" || cart.kind === "receipt") &&
      quantity.taken.size > 0
    ) {
      problems.push(
        `${item.name}: Nothing is taken from an open package here.`,
      );
    } else if (cart.kind === "open" && quantity.loose > 0) {
      problems.push(`${item.name}: Open whole packages: leave loose empty.`);
    } else if (cart.kind === "open" && quantity.amount > 0) {
      lines.push(...openings(item, quantity.sealed));
      items += 1;
    } else if (quantity.amount > 0) {
      // Each package counted on a line of its own, sealed, so stock keeps
      // them sealed where they arrive (docs/decisions/0020); then loose.
      for (const [packageId, { count, size }] of quantity.sealed) {
        lines.push({
          item_id: item.id,
          quantity: decimal(count * size),
          package_id: packageId,
        });
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
      items += 1;
    }
  }
  if (
    lines.length + usedUp.length + lengths.length === 0 &&
    problems.length === 0
  ) {
    problems.push("Add something to the cart.");
  }
  if (lines.length > MAX_LINES) {
    problems.push("That is too much for one batch: save some, then the rest.");
  }
  // The volunteer check again only narrows the type; it was reported above.
  if (problems.length > 0 || cart.volunteerId === null) {
    return { problems };
  }
  const packages = openPackages(usedUp.length);
  const measured = openPackages(lengths.length);
  // A report of lengths alone says so; one of used up, as ever.
  const lengthsOnly = reporting && usedUp.length === 0;
  const counted = items === 1 ? "1 item" : `${items} items`;
  const things = lengthsOnly ? measured : reporting ? packages : counted;
  const named = lengthsOnly ? "What's on it" : KINDS[kind];
  const words = [`${named}: ${things}`];
  const draft: Draft = { kind, volunteer_id: cart.volunteerId, lines };
  if (usedUp.length > 0) {
    draft.used_up = usedUp;
  }
  if (lengths.length > 0) {
    draft.lengths = lengths;
  }
  if (fromId !== null && (kind === "open" || kind === "report")) {
    draft.from_location_id = fromId;
    if (kind === "open") {
      draft.to_location_id = fromId;
    }
    words.push(`at ${index.places.get(fromId)}`);
  } else if (fromId !== null) {
    draft.from_location_id = fromId;
    words.push(`from ${index.places.get(fromId)}`);
  }
  if (toId !== null) {
    draft.to_location_id = toId;
    words.push(`to ${index.places.get(toId)}`);
  }
  if (installNumber !== "") {
    draft.install_number = Number(installNumber);
    words.push(`for install ${installNumber}`);
  }
  if (usedUp.length > 0 && !reporting) {
    words.push(`(${packages} said used up)`);
  }
  if (lengths.length > 0 && !lengthsOnly) {
    words.push(`(what is on ${measured} said)`);
  }
  return { draft, what: words.join(" ") };
}

function openPackages(count: number): string {
  return count === 1 ? "1 open package" : `${count} open packages`;
}

/**
 * An opening's lines (docs/decisions/0020): a package its item follows
 * once opened becomes an open package, one a line, under a key made here,
 * so a later batch in the outbox can name it before the server has seen
 * it; others become loose units, one line of all of them.
 */
function openings(item: Item, sealed: Map<number, Packs>): Draft["lines"] {
  const followed = new Set(
    item.packages.filter((pack) => pack.track_opened).map((pack) => pack.id),
  );
  return [...sealed].flatMap(([packageId, { count, size }]) => {
    const each = { item_id: item.id, package_id: packageId };
    return followed.has(packageId)
      ? Array.from({ length: count }, () => ({
          ...each,
          quantity: decimal(size),
          open_package_key: uuid(),
        }))
      : [{ ...each, quantity: decimal(count * size) }];
  });
}

/** A batch in the outbox, as offered and sealedCounts read it. */
type Pending = Pick<Entry, "state" | "body" | "queuedAt">;

/** The batch keys a catalogue is asked about (catalogue.py, MOST_KEYS):
 * its outbox's saved ones, then those waiting, oldest first. */
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
    (each) => each.state !== "refused" && !shown.has(each.body.key),
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
      const fromOpen = body.kind !== "open" && line.open_package_key != null;
      const size = packageId == null ? undefined : sizes.get(packageId);
      if (packageId == null || fromOpen || size === undefined) {
        continue;
      }
      // Rounded up, as the catalogue counts a part of one (catalogue.py).
      const n = Math.ceil(sent(String(line.quantity)) / size);
      add(packageId, body.from_location_id, -n);
      if (body.kind !== "open") {
        add(packageId, body.to_location_id, n);
      }
    }
  }
  return counts;
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
      const amount = sent(String(line.quantity));
      if (key == null) {
        continue;
      }
      if (draft.kind === "open") {
        if (!known.has(key)) {
          // A retired package is missing once the catalogue says none is
          // sealed: named once saved, from the catalogue's open package.
          const pack = items
            .get(line.item_id)
            ?.packages.find((each) => each.id === line.package_id);
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

/** Items whose name, maker, part number, code or category holds every word. */
export function search(catalogue: Catalogue, text: string, limit = 20): Item[] {
  const words = text.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) {
    return [];
  }
  const { haystacks } = indexOf(catalogue);
  return catalogue.items
    .filter((item) => {
      const haystack = haystacks.get(item.id) ?? "";
      return words.every((word) => haystack.includes(word));
    })
    .slice(0, limit);
}
