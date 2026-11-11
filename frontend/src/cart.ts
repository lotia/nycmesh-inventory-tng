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
import { BatchBody, type Draft } from "./outbox";
import { count, decimal, thousandths } from "./quantity";
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
});

const CartSchema = v.object({
  kind: BatchBody.entries.kind,
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

/** A line's quantity in thousandths of the item's unit, and of it, how
 * much in each package counted and how much loose; or why it has none. */
export function quantityOf(
  line: Line,
  item: Item,
):
  | { amount: number; sealed: Map<number, number>; loose: number }
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
  const sealed = new Map<number, number>();
  for (const pack of item.packages) {
    const packs = count(line.packages[pack.id] ?? "");
    const size = thousandths(pack.size);
    if (packs === null || size === null) {
      return { problem: `Count whole packages: ${pack.name}.` };
    }
    if (packs > 0) {
      sealed.set(pack.id, packs * size);
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
  // Each part is a line of its own (toBatch), so each is whole.
  const parts = [loose, ...sealed.values()];
  if (item.unit === "each" && parts.some((part) => part % 1000 !== 0)) {
    return { problem: "Counted in whole units." };
  }
  return { amount, sealed, loose };
}

const KINDS: Record<Kind, string> = {
  move: "Move",
  receipt: "Receipt",
  install: "Install",
};

/** What the page says beside a retired item. */
export const RETIRED = "retired: taking only";

/**
 * The cart as the batch endpoint takes it, less the key the outbox adds,
 * and in words for the outbox; or what still needs doing before it can be
 * saved.
 */
export function toBatch(
  filled: Cart,
  catalogue: Catalogue,
): { draft: Draft; what: string } | { problems: string[] } {
  const cart = reconcile(filled, catalogue);
  const index = indexOf(catalogue);
  const sides = SIDES[cart.kind];
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
  const installNumber =
    cart.kind === "install" ? cart.installNumber.trim() : "";
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
    const quantity = quantityOf(line, item);
    if ("problem" in quantity) {
      problems.push(`${item.name}: ${quantity.problem}`);
    } else if (quantity.amount >= MAX_AMOUNT) {
      problems.push(`${item.name}: That is more than the inventory can count.`);
    } else if (quantity.amount > 0) {
      // Each package counted on a line of its own, sealed, so stock keeps
      // them sealed where they arrive (docs/decisions/0020); then loose.
      for (const [packageId, amount] of quantity.sealed) {
        lines.push({
          item_id: item.id,
          quantity: decimal(amount),
          package_id: packageId,
        });
      }
      if (quantity.loose > 0) {
        lines.push({ item_id: item.id, quantity: decimal(quantity.loose) });
      }
      items += 1;
    }
  }
  if (lines.length === 0 && problems.length === 0) {
    problems.push("Add something to the cart.");
  }
  if (lines.length > MAX_LINES) {
    problems.push("That is too much for one batch: save some, then the rest.");
  }
  // The volunteer check again only narrows the type; it was reported above.
  if (problems.length > 0 || cart.volunteerId === null) {
    return { problems };
  }
  const things = items === 1 ? "1 item" : `${items} items`;
  const words = [`${KINDS[cart.kind]}: ${things}`];
  const draft: Draft = {
    kind: cart.kind,
    volunteer_id: cart.volunteerId,
    lines,
  };
  if (fromId !== null) {
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
  return { draft, what: words.join(" ") };
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
