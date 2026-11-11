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
import type { Draft } from "./outbox";
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
        /** Said to be used up, for an administrator to confirm. */
        usedUp: v.exactOptional(v.boolean()),
      }),
    ),
  ),
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
  const existing = cart.lines.find((line) => line.itemId === itemId);
  const line: Line = existing ?? { itemId, packages: {}, loose: "" };
  const bumped: Line =
    packageId === undefined
      ? { ...line, loose: decimal((thousandths(line.loose) ?? 0) + 1000) }
      : {
          ...line,
          packages: {
            ...line.packages,
            [packageId]: String(
              (count(line.packages[packageId] ?? "") ?? 0) + 1,
            ),
          },
        };
  return {
    ...cart,
    lines: existing
      ? cart.lines.map((each) => (each === existing ? bumped : each))
      : [...cart.lines, bumped],
  };
}

/** An item's one package, as opening the item opens one: its one current
 * package, beside any retired, else its one package; or none. */
export function onlyPackage(
  item: Item | undefined,
): Item["packages"][number] | undefined {
  const packs = item?.packages ?? [];
  const current = packs.filter((each) => !each.retired);
  const pool = current.length > 0 ? current : packs;
  return pool.length === 1 ? pool[0] : undefined;
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
  offeredKeys?: ReadonlySet<string>,
): { draft: Draft; what: string } | { problems: string[] } {
  const cart = withOffered(reconcile(filled, catalogue), offeredKeys);
  const index = indexOf(catalogue);
  // Open packages said to be used up: with what is taken, or alone, when
  // nothing else is in the cart: a report, at the From place.
  const usedUp = cart.lines.flatMap((line) =>
    Object.entries(line.open ?? {}).flatMap(([key, taken]) =>
      taken.usedUp ? [key] : [],
    ),
  );
  const reporting =
    usedUp.length > 0 &&
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
  const problems: string[] = [];
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
  if (lines.length === 0 && usedUp.length === 0 && problems.length === 0) {
    problems.push("Add something to the cart.");
  }
  if (lines.length > MAX_LINES) {
    problems.push("That is too much for one batch: save some, then the rest.");
  }
  // The volunteer check again only narrows the type; it was reported above.
  if (problems.length > 0 || cart.volunteerId === null) {
    return { problems };
  }
  const packages =
    usedUp.length === 1 ? "1 open package" : `${usedUp.length} open packages`;
  const things = reporting
    ? packages
    : items === 1
      ? "1 item"
      : `${items} items`;
  const words = [`${KINDS[kind]}: ${things}`];
  const draft: Draft = { kind, volunteer_id: cart.volunteerId, lines };
  if (usedUp.length > 0) {
    draft.used_up = usedUp;
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
  return { draft, what: words.join(" ") };
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

export type OpenPackage = Catalogue["open_packages"][number];
/** An open package a cart can take from, as it will be once the batches
 * waiting are saved: where it is, and what is left on it, in thousandths. */
export type Offered = Omit<OpenPackage, "left"> & { left: number };

/**
 * The open packages carts can take from: the catalogue's, and those opened
 * in batches still waiting to be sent, under the keys this page made, so
 * they can be taken from before the server has seen them; each as the
 * waiting batches leave it, cut from or moved whole (docs/decisions/0020).
 * Worked out once for every line of a cart (offeredAt).
 */
export function offered(
  catalogue: Catalogue,
  outbox: readonly { state: string; body: Draft; queuedAt: number }[],
): Offered[] {
  const byKey = new Map<string, Offered>(
    catalogue.open_packages.map((each) => [
      each.key,
      { ...each, left: sent(each.left) },
    ]),
  );
  const known = new Set(byKey.keys());
  for (const { state, body: draft, queuedAt } of outbox) {
    if (state !== "waiting") {
      continue;
    }
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
          byKey.set(key, {
            id: 0,
            key,
            item_id: line.item_id,
            package_id: line.package_id ?? 0,
            location_id: draft.from_location_id ?? 0,
            opened_at: new Date(queuedAt).toISOString(),
            left: amount,
            short: false,
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

/** The keys of offered's open packages at a place, of any item. */
export function keysAt(
  all: readonly Offered[],
  placeId: number | null,
): Set<string> {
  return new Set(
    all.filter((each) => each.location_id === placeId).map((each) => each.key),
  );
}

/** offered's open packages of an item at a place. */
export function offeredAt(
  all: readonly Offered[],
  itemId: number,
  placeId: number | null,
): Offered[] {
  return all.filter(
    (each) => each.item_id === itemId && each.location_id === placeId,
  );
}

/** The cart with what it takes from open packages no longer offered left
 * out: reported or closed elsewhere, or moved, since it was typed, its
 * field is gone from the page, so what was typed there goes too. */
function withOffered(cart: Cart, keys?: ReadonlySet<string>): Cart {
  if (keys === undefined) {
    return cart;
  }
  return {
    ...cart,
    lines: cart.lines.map((line) => ({
      ...line,
      open: Object.fromEntries(
        Object.entries(line.open ?? {}).filter(([key]) => keys.has(key)),
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
