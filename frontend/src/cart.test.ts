import { describe, expect, test } from "vitest";
import {
  addOne,
  askWho,
  type Cart,
  CartStore,
  freshCart,
  indexOf,
  loadCart,
  quantityOf,
  STORAGE_KEY,
  search,
  storeCart,
  toBatch,
} from "./cart";
import { CATALOGUE, cable, radio } from "./test-catalogue";

function cart(fields: Partial<Cart> = {}): Cart {
  return { ...freshCart(), volunteerId: 1, fromId: 10, toId: 12, ...fields };
}

describe("a fresh cart", () => {
  test("keeps the last cart's choices but not its contents", () => {
    const last = cart({ kind: "install", installNumber: "1234" });
    const next = freshCart(addOne(last, 100));
    expect(next).toMatchObject({
      kind: "install",
      volunteerId: 1,
      fromId: 10,
      toId: 12,
      installNumber: "",
      lines: [],
    });
  });
});

describe("storage", () => {
  test("a stored cart comes back", () => {
    const kept = addOne(cart(), 101, 1001);
    expect(storeCart(kept)).toBe(true);
    expect(loadCart()).toEqual(kept);
  });

  test.each(["not json", '{"kind": "move"}', "null"])(
    "storage holding %j gives a fresh cart",
    (value) => {
      localStorage.setItem(STORAGE_KEY, value);
      expect(loadCart().lines).toEqual([]);
    },
  );
});

describe("adding", () => {
  test("one of an item, then one more", () => {
    const twice = addOne(addOne(cart(), 100), 100);
    expect(twice.lines).toEqual([{ itemId: 100, packages: {}, loose: "2" }]);
  });

  test("a package counts whole packages", () => {
    const added = addOne(addOne(cart(), 101, 1001), 101, 1001);
    expect(added.lines).toEqual([
      { itemId: 101, packages: { 1001: "2" }, loose: "" },
    ]);
  });
});

describe("quantities", () => {
  test("packages and loose units add up exactly", () => {
    const line = { itemId: 101, packages: { 1001: "2" }, loose: "12.5" };
    expect(quantityOf(line, cable)).toEqual({
      amount: 622500,
      sealed: new Map([[1001, { count: 2, size: 305000 }]]),
      loose: 12500,
    });
  });

  test("a fraction of a package is refused", () => {
    const line = { itemId: 101, packages: { 1001: "0.5" }, loose: "" };
    expect(quantityOf(line, cable)).toEqual({
      problem: "Count whole packages: Spool.",
    });
  });

  test("an item counted in units takes whole numbers", () => {
    const line = { itemId: 100, packages: {}, loose: "1.5" };
    expect(quantityOf(line, radio)).toEqual({
      problem: "Counted in whole units.",
    });
  });

  test("each part of an item counted in units is whole", () => {
    // A box of 2.5, and a half loose: 3 in all, but each is a line.
    const halves = {
      ...radio,
      packages: [{ id: 7, name: "Half box", size: "2.5", track_opened: false }],
    };
    const line = { itemId: 100, packages: { 7: "1" }, loose: "0.5" };
    expect(quantityOf(line, halves)).toEqual({
      problem: "Counted in whole units.",
    });
  });

  test("an unreadable amount says what is wanted", () => {
    const line = { itemId: 101, packages: {}, loose: "lots" };
    expect(quantityOf(line, cable)).toEqual({
      problem: "Enter the loose amount in m, up to 3 decimals.",
    });
  });
});

describe("turning a cart into a batch", () => {
  test("a move sends both places, decimal quantities, and packages sealed", () => {
    const full = {
      ...cart(),
      lines: [
        { itemId: 100, packages: { 1000: "1" }, loose: "2" },
        { itemId: 101, packages: {}, loose: "0.25" },
      ],
    };
    expect(toBatch(full, CATALOGUE)).toEqual({
      draft: {
        kind: "move",
        volunteer_id: 1,
        from_location_id: 10,
        to_location_id: 12,
        lines: [
          { item_id: 100, quantity: "4", package_id: 1000 },
          { item_id: 100, quantity: "2" },
          { item_id: 101, quantity: "0.25" },
        ],
      },
      what: "Move: 2 items from Hub to Van",
    });
  });

  test("a batch of more lines than the server takes is held back", () => {
    const lines = Array.from({ length: 251 }, () => ({
      itemId: 101,
      packages: { 1001: "1" },
      loose: "1",
    }));
    // Each item twice: once a spool, once loose.
    const many = cart({
      lines: lines.map((line, number) => ({ ...line, itemId: 101 + number })),
    });
    const items = Array.from({ length: 251 }, (_, number) => ({
      ...cable,
      id: 101 + number,
      name: `Cable ${number}`,
    }));
    expect(toBatch(many, { ...CATALOGUE, items })).toEqual({
      problems: ["That is too much for one batch: save some, then the rest."],
    });
  });

  test("a line that can't be counted is named", () => {
    const lines = [{ itemId: 100, packages: {}, loose: "1.5" }];
    expect(toBatch(cart({ lines }), CATALOGUE)).toEqual({
      problems: ["LiteBeam 5AC: Counted in whole units."],
    });
  });

  test("an opening names its place on both sides; a followed package opens one a line", () => {
    const lines = [
      { itemId: 101, packages: { 1001: "2" }, loose: "" },
      { itemId: 100, packages: { 1000: "3" }, loose: "" },
    ];
    const opened = toBatch(
      cart({ kind: "open", lines, fromId: 10 }),
      CATALOGUE,
    );
    expect(opened).toMatchObject({
      draft: { kind: "open", from_location_id: 10, to_location_id: 10 },
      what: "Opening: 2 items at Hub",
    });
    const sent = "draft" in opened ? opened.draft.lines : [];
    // The spool is followed: one a line, each with a key of its own.
    expect(sent).toEqual([
      {
        item_id: 101,
        package_id: 1001,
        quantity: "305",
        open_package_key: expect.any(String),
      },
      {
        item_id: 101,
        package_id: 1001,
        quantity: "305",
        open_package_key: expect.any(String),
      },
      // The box is not: its radios become loose units.
      { item_id: 100, package_id: 1000, quantity: "12" },
    ]);
    expect(sent[0]?.open_package_key).not.toBe(sent[1]?.open_package_key);
  });

  test("an opening opens whole packages only", () => {
    const lines = [{ itemId: 101, packages: { 1001: "1" }, loose: "3" }];
    expect(
      toBatch(cart({ kind: "open", lines, fromId: 10 }), CATALOGUE),
    ).toEqual({
      problems: ["Cat6 cable: Open whole packages: leave loose empty."],
    });
  });

  test("packages alone send no loose line", () => {
    const lines = [{ itemId: 101, packages: { 1001: "2" }, loose: "" }];
    expect(toBatch(cart({ lines }), CATALOGUE)).toMatchObject({
      draft: { lines: [{ item_id: 101, quantity: "610", package_id: 1001 }] },
    });
  });

  test("a receipt has only a to; an install only a from, and its number", () => {
    const lines = [{ itemId: 100, packages: {}, loose: "1" }];
    const receipt = toBatch(cart({ kind: "receipt", lines }), CATALOGUE);
    expect(receipt).toMatchObject({ what: "Receipt: 1 item to Van" });
    expect("draft" in receipt && receipt.draft.from_location_id).toBe(
      undefined,
    );
    const install = toBatch(
      cart({ kind: "install", installNumber: " 4242 ", lines, fromId: 11 }),
      CATALOGUE,
    );
    expect(install).toMatchObject({
      draft: { from_location_id: 11, install_number: 4242 },
      what: "Install: 1 item from Hub › Shelf A for install 4242",
    });
    expect("draft" in install && install.draft.to_location_id).toBe(undefined);
  });

  test("a retired item can be taken but not received", () => {
    const retired = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 100 ? { ...item, retired: true } : item,
      ),
    };
    const lines = [{ itemId: 100, packages: {}, loose: "1" }];
    expect(toBatch(cart({ kind: "receipt", lines }), retired)).toEqual({
      problems: [
        "LiteBeam 5AC: retired: taking only, so it can't be received.",
      ],
    });
    expect(toBatch(cart({ lines }), retired)).toMatchObject({
      what: "Move: 1 item from Hub to Van",
    });
  });

  test("lines with nothing in them are left out", () => {
    const lines = [
      { itemId: 100, packages: {}, loose: "1" },
      { itemId: 101, packages: { 1001: "" }, loose: "" },
    ];
    const batch = toBatch(cart({ lines }), CATALOGUE);
    expect("draft" in batch && batch.draft.lines).toEqual([
      { item_id: 100, quantity: "1" },
    ]);
  });

  test("says everything still to do", () => {
    const empty = cart({ volunteerId: null, fromId: null, toId: null });
    expect(toBatch(empty, CATALOGUE)).toEqual({
      problems: [
        "Pick your name.",
        "Pick where it comes from.",
        "Pick where it goes.",
      ],
    });
    expect(toBatch(cart(), CATALOGUE)).toEqual({
      problems: ["Add something to the cart."],
    });
    const same = cart({
      toId: 10,
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    });
    expect(toBatch(same, CATALOGUE)).toEqual({
      problems: ["From and to must be different places."],
    });
    const bad = cart({
      kind: "install",
      installNumber: "12a",
      lines: [{ itemId: 999, packages: {}, loose: "1" }],
    });
    expect(toBatch(bad, CATALOGUE)).toEqual({
      problems: [
        "An install number is digits only.",
        "An item in the cart is no longer in the catalogue. Remove it.",
      ],
    });
  });
});

describe("finding", () => {
  test("search matches every word, anywhere in an item's details", () => {
    expect(search(CATALOGUE, "ubiquiti beam").map((i) => i.id)).toEqual([100]);
    expect(search(CATALOGUE, "CABLES").map((i) => i.id)).toEqual([101]);
    expect(search(CATALOGUE, "gen2").map((i) => i.id)).toEqual([100]);
    expect(search(CATALOGUE, "")).toEqual([]);
    expect(search(CATALOGUE, "radio cable")).toEqual([]);
  });

  test("a place is named with the places it sits in", () => {
    expect(indexOf(CATALOGUE).places.get(11)).toBe("Hub › Shelf A");
    expect(indexOf(CATALOGUE).places.get(12)).toBe("Van");
  });
});

describe("what has changed since the cart was filled", () => {
  test("a retired name or place counts as none picked", () => {
    const lines = [{ itemId: 100, packages: {}, loose: "1" }];
    expect(
      toBatch(cart({ volunteerId: 99, toId: 98, lines }), CATALOGUE),
    ).toEqual({
      problems: ["Pick your name.", "Pick where it goes."],
    });
  });

  test("counts for a retired package are not dropped silently", () => {
    const line = { itemId: 101, packages: { 9999: "3" }, loose: "1" };
    expect(quantityOf(line, cable)).toEqual({
      problem:
        "A package counted here is no longer used. Remove the line and add it again.",
    });
    // A retired package left at nothing is no matter.
    expect(quantityOf({ ...line, packages: { 9999: "" } }, cable)).toEqual({
      amount: 1000,
      sealed: new Map(),
      loose: 1000,
    });
  });
});

describe("asking whose name it is", () => {
  const minutes = (name_check_minutes: number) => ({
    ...CATALOGUE,
    settings: { name_check_minutes },
  });
  const now = 1_000_000_000;
  const usedAgo = (ms: number) => cart({ volunteerId: 1, usedAt: now - ms });

  test("asks only once unused longer than administrators set", () => {
    expect(askWho(usedAgo(14 * 60_000), minutes(15), now)).toBe(false);
    expect(askWho(usedAgo(16 * 60_000), minutes(15), now)).toBe(true);
    expect(askWho(usedAgo(16 * 60_000), minutes(60), now)).toBe(false);
  });

  test("0 never asks", () => {
    expect(askWho(usedAgo(1e9), minutes(0), now)).toBe(false);
  });

  test("with no name chosen, there is nothing to ask", () => {
    const blank = { ...usedAgo(1e9), volunteerId: null };
    expect(askWho(blank, minutes(15), now)).toBe(false);
  });
});

describe("the server's limits", () => {
  const lines = [{ itemId: 100, packages: {}, loose: "1" }];
  test.each(["0", "2147483648"])(
    "install number %s is out of range",
    (number) => {
      const install = cart({ kind: "install", installNumber: number, lines });
      expect(toBatch(install, CATALOGUE)).toEqual({
        problems: ["That install number is out of range."],
      });
    },
  );

  test("a quantity too big to count is caught before saving", () => {
    const huge = cart({
      lines: [{ itemId: 101, packages: {}, loose: "1000000000" }],
    });
    expect(toBatch(huge, CATALOGUE)).toEqual({
      problems: ["Cat6 cable: That is more than the inventory can count."],
    });
  });
});

describe("the cart store, shared by tabs", () => {
  const filled = () => addOne(cart(), 100);

  test("a change gets a new revision; marking it used keeps the revision", () => {
    const store = new CartStore(CATALOGUE);
    store.change(filled(), 1);
    const changed = store.cart.value;
    expect(changed.revision).toMatch(/^[0-9a-f-]{36}$/);
    store.touch(2);
    expect(store.cart.value).toEqual({ ...changed, usedAt: 2 });
    expect(loadCart()).toEqual(store.cart.value);
  });

  test("a cart is started with its first line, and starts again once empty", () => {
    const store = new CartStore(CATALOGUE);
    store.change(filled(), 10);
    expect(store.cart.value.startedAt).toBe(10);
    store.change(addOne(store.cart.value, 100), 20);
    expect(store.cart.value.startedAt).toBe(10);
    store.change(freshCart(store.cart.value), 30);
    expect(store.cart.value.startedAt).toBe(0);
  });

  test("a cart another tab changed is not saved here, and is shown instead", () => {
    const here = new CartStore(CATALOGUE);
    const there = new CartStore(CATALOGUE);
    here.change(filled());
    there.useCatalogue(CATALOGUE);
    there.change(freshCart(), 5);
    expect(here.forSaving()).toBeNull();
    expect(here.cart.value.lines).toEqual([]);
    expect(here.forSaving()).toEqual(here.cart.value);
  });

  test("another tab only marking the cart used does not stop a save here", () => {
    const here = new CartStore(CATALOGUE);
    here.change(filled());
    const there = new CartStore(CATALOGUE);
    there.touch();
    expect(here.forSaving()?.lines).toEqual(filled().lines);
  });

  test("when the browser will not store it, this tab's cart is the one saved", () => {
    const refusing = {
      getItem: () => null,
      setItem: () => {
        throw new DOMException("full", "QuotaExceededError");
      },
    } as unknown as Storage;
    const store = new CartStore(CATALOGUE, refusing);
    expect(store.change(filled())).toBe(false);
    expect(store.forSaving()?.lines).toEqual(filled().lines);
  });

  test("a newer catalogue clears choices it no longer has", () => {
    const store = new CartStore(CATALOGUE);
    store.change(cart({ volunteerId: 2 }));
    store.useCatalogue({ ...CATALOGUE, volunteers: [{ id: 1, name: "Ada" }] });
    expect(store.cart.value.volunteerId).toBeNull();
  });
});
