import { describe, expect, test, vi } from "vitest";
import { BUILD } from "../shared/build";
import { say } from "../shared/i18n";
import { cart } from "../testing/cart";
import { CATALOGUE, cable, radio } from "../testing/catalogue";
import { saidAsText } from "../testing/said";
import { full, refusing } from "../testing/storage";
import { toBatch } from "./batch";
import {
  addOne,
  askWho,
  type Cart,
  CartStore,
  freshCart,
  type Item,
  type Line,
  loadCart,
  openingPackage,
  PACKAGE_NO_LONGER_USED,
  placed,
  placeSides,
  quantityOf,
  reconcile,
  resized,
  STORAGE_KEY,
  sealedHere,
  sized,
  storeCart,
  unitChanged,
  WHOLE_UNITS,
  withLine,
} from "./cart";
import { sealedCounts } from "./unseen-batches";

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

  test("a stored cart keeps the key of the save that filled it", () => {
    const key = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
    storeCart({ ...addOne(cart(), 101), batchKey: key });
    expect(loadCart().batchKey).toBe(key);
  });

  test("a stored cart is stamped with the build that wrote it", () => {
    storeCart(cart());
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "")).toEqual({
      ...cart(),
      build: BUILD,
    });
  });

  test("an older build's cart is not read", () => {
    localStorage.setItem(
      "inventory.cart.v1",
      JSON.stringify(addOne(cart(), 100)),
    );
    expect(loadCart()).toEqual(freshCart());
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
    expect(twice.lines).toEqual([
      { itemId: 100, packages: {}, loose: "2", picked: 2 },
    ]);
  });

  test("a package counts whole packages", () => {
    const added = addOne(addOne(cart(), 101, 1001), 101, 1001);
    expect(added.lines).toEqual([
      { itemId: 101, packages: { 1001: "2" }, loose: "", picked: 2 },
    ]);
  });
});

test("adding to one line of several leaves the others", () => {
  const two = addOne(addOne(cart(), 100), 101);
  expect(addOne(two, 101).lines).toEqual([
    { itemId: 100, packages: {}, loose: "1", picked: 1 },
    { itemId: 101, packages: {}, loose: "2", picked: 2 },
  ]);
});

describe("quantities", () => {
  test("packages and loose units add up exactly", () => {
    const line = { itemId: 101, packages: { 1001: "2" }, loose: "12.5" };
    expect(quantityOf(line, cable)).toEqual({
      amount: 622500,
      sealed: new Map([[1001, { count: 2, size: 305000 }]]),
      loose: 12500,
      taken: new Map(),
    });
  });

  test("a fraction of a package is refused", () => {
    const line = { itemId: 101, packages: { 1001: "0.5" }, loose: "" };
    expect(saidAsText(quantityOf(line, cable))).toEqual({
      problem: "Count whole packages: [Spool].",
    });
  });

  test("an item counted in units takes whole numbers", () => {
    const line = { itemId: 100, packages: {}, loose: "1.5" };
    expect(saidAsText(quantityOf(line, radio))).toEqual({
      problem: say(WHOLE_UNITS),
    });
  });

  test("each part of an item counted in units is whole", () => {
    // A box of 2.5, and a half loose: 3 in all, but each is a line.
    const halves = {
      ...radio,
      packages: [
        {
          id: 7,
          name: "Half box",
          size: "2.5",
          track_opened: false,
          retired: false,
          short_below: null,
          sealed: [],
        },
      ],
    };
    const line = { itemId: 100, packages: { 7: "1" }, loose: "0.5" };
    expect(saidAsText(quantityOf(line, halves))).toEqual({
      problem: say(WHOLE_UNITS),
    });
  });

  test("an unreadable amount says what is wanted", () => {
    const line = { itemId: 101, packages: {}, loose: "lots" };
    expect(saidAsText(quantityOf(line, cable))).toEqual({
      problem: "Enter the loose amount in m, up to 3 decimals.",
    });
  });
});

describe("turning a cart into a batch", () => {
  test("opening an item opens its one package sealed where it is opened", () => {
    const [box] = radio.packages;
    if (box === undefined) throw new Error("the radio comes in a box");
    // A retired one too, sealed only at the van (inventory-tng-f9h).
    const old = {
      ...box,
      id: 2,
      retired: true,
      sealed: [
        { location_id: 10, count: 1 },
        { location_id: 12, count: 1 },
      ],
    };
    const both = { ...radio, packages: [box, old] };
    const counts = sealedCounts({ ...CATALOGUE, items: [both] }, []);
    expect(sealedHere(radio, 10, counts)).toBe(box);
    expect(sealedHere(both, 12, counts)).toBe(old);
    // Both sealed there, or neither: none is picked.
    expect(sealedHere(both, 10, counts)).toBe(undefined);
    expect(sealedHere(both, 11, counts)).toBe(undefined);
    // No such item: none.
    expect(sealedHere(undefined, 10, counts)).toBe(undefined);
  });

  test("opening with no place picked opens an item's one current package", () => {
    const [box] = radio.packages;
    if (box === undefined) throw new Error("the radio comes in a box");
    const counts = sealedCounts(CATALOGUE, []);
    expect(openingPackage(radio, null, counts)).toBe(box);
    // A retired one beside it is not counted, with no place to say.
    const old = { ...box, id: 2, retired: true };
    expect(
      openingPackage({ ...radio, packages: [box, old] }, null, counts),
    ).toBe(box);
    const two = { ...radio, packages: [box, { ...box, id: 3 }] };
    expect(openingPackage(two, null, counts)).toBe(undefined);
    expect(openingPackage(undefined, null, counts)).toBe(undefined);
    // At a place, the one sealed there.
    expect(openingPackage(radio, 12, counts)).toBe(undefined);
    expect(openingPackage(radio, 10, counts)).toBe(box);
  });

  test("a place picked fills the lines not typed into", () => {
    const counts = sealedCounts(CATALOGUE, []);
    const lines = [
      // Picked twice with no place: two of its one package.
      { itemId: 100, packages: { 1000: "2" }, loose: "", picked: 2 },
      // Picked under another kind, loose, with a cut typed.
      {
        itemId: 101,
        packages: {},
        loose: "1",
        open: { k: { amount: "2", whole: false } },
      },
      // Typed: kept as it is.
      { itemId: 101, packages: { 1001: "5" }, loose: "", typed: true },
      // Gone from the catalogue.
      { itemId: 999, packages: { 7: "1" }, loose: "" },
    ];
    const opening = cart({ kind: "open", fromId: null, lines });
    const move = (fields: Partial<Cart>, from = opening) =>
      placed(from, { ...from, ...fields }, CATALOGUE, counts);
    const atVan = move({ fromId: 12 });
    expect(atVan.lines).toEqual([
      { itemId: 100, packages: {}, loose: "", picked: 2 },
      { itemId: 101, packages: { 1001: "1" }, loose: "" },
      lines[2],
      { itemId: 999, packages: {}, loose: "" },
    ]);
    // Through a place with none, the count picked stays.
    expect(move({ fromId: 10 }, atVan).lines[0]).toEqual({
      itemId: 100,
      packages: { 1000: "2" },
      loose: "",
      picked: 2,
    });
    // Nothing moved: as it was.
    expect(move({ volunteerId: 2 }).lines).toBe(lines);
    // Another kind: only what was taken from open packages goes, and each
    // line is kept as it is from then on.
    const moving = move({ kind: "move" });
    expect(moving.lines[1]).toEqual({
      itemId: 101,
      packages: {},
      loose: "1",
      typed: true,
    });
    expect(moving.lines[0]).toEqual({ ...lines[0], typed: true });
    // Back to opening: lines from another kind keep what they hold, a
    // place picked after too, so loose picked to move never become
    // packages to open.
    const reopened = move({ kind: "open" }, move({ fromId: 10 }, moving));
    expect(move({ fromId: 12 }, reopened).lines.slice(0, 2)).toEqual([
      { ...lines[0], typed: true },
      { itemId: 101, packages: {}, loose: "1", typed: true },
    ]);
  });

  test("a line for an item is added once, with nothing in it", () => {
    const empty = cart({ lines: [] });
    const once = withLine(empty, 100);
    expect(once.lines).toEqual([
      { itemId: 100, packages: {}, loose: "", picked: 1 },
    ]);
    // Picked again: counted, on the same line.
    expect(withLine(once, 100).lines).toEqual([
      { itemId: 100, packages: {}, loose: "", picked: 2 },
    ]);
  });
});

describe("what has changed since the cart was filled", () => {
  test("counts for a retired package are not dropped silently", () => {
    const line = { itemId: 101, packages: { 9999: "3" }, loose: "1" };
    expect(saidAsText(quantityOf(line, cable))).toEqual({
      problem: say(PACKAGE_NO_LONGER_USED),
    });
    // A retired package left at nothing is no matter.
    expect(quantityOf({ ...line, packages: { 9999: "" } }, cable)).toEqual({
      amount: 1000,
      sealed: new Map(),
      loose: 1000,
      taken: new Map(),
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

describe("the cart store, shared by tabs", () => {
  const filled = () => addOne(cart(), 100);
  // As a change keeps them: typed in the radio's unit (sized).
  const filledLines = () =>
    filled().lines.map((line) => ({ ...line, unit: "each" }));

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

  test("a cart gets its batch key with its first line, keeps it while it holds lines, and drops it once empty (inventory-tng-e2d)", () => {
    const store = new CartStore(CATALOGUE);
    // Who and where alone are no batch yet.
    store.change(cart());
    expect(store.cart.value).not.toHaveProperty("batchKey");
    store.change(filled());
    const key = store.cart.value.batchKey;
    expect(key).toMatch(/^[0-9a-f-]{36}$/);
    expect(loadCart().batchKey).toBe(key);
    store.change(addOne(store.cart.value, 101));
    expect(store.cart.value.batchKey).toBe(key);
    store.change(freshCart(store.cart.value));
    expect(store.cart.value).not.toHaveProperty("batchKey");
    expect(loadCart()).not.toHaveProperty("batchKey");
    store.change(addOne(store.cart.value, 100));
    expect(store.cart.value.batchKey).toMatch(/^[0-9a-f-]{36}$/);
    expect(store.cart.value.batchKey).not.toBe(key);
  });

  test("what a cart was put back with is kept with its key, and dropped with it (inventory-tng-0gg)", () => {
    const key = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
    const changedFrom = [{ item_id: 100, quantity: "1", unit: "each" }];
    const store = new CartStore(CATALOGUE);
    store.change({ ...filled(), batchKey: key, changedFrom });
    store.change(addOne(store.cart.value, 101));
    expect(loadCart().changedFrom).toEqual(changedFrom);
    store.change(freshCart(store.cart.value));
    expect(loadCart()).not.toHaveProperty("changedFrom");
    // Without a key it is no batch put back.
    store.change({ ...filled(), changedFrom });
    expect(loadCart()).not.toHaveProperty("changedFrom");
  });

  test("a cart filled with a key keeps that key", () => {
    const key = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
    const store = new CartStore(CATALOGUE);
    store.change({ ...filled(), batchKey: key });
    expect(loadCart().batchKey).toBe(key);
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
    expect(here.forSaving()?.lines).toEqual(filledLines());
  });

  test("when the browser will not store it, this tab's cart is the one saved", () => {
    const store = new CartStore(CATALOGUE, refusing("setItem"));
    expect(store.change(filled())).toBe(false);
    expect(store.forSaving()?.lines).toEqual(filledLines());
  });

  describe("a write the browser refuses (inventory-tng-xm07)", () => {
    test("is tried again once the stored cart is removed, as a full disk may take it then", () => {
      const store = new CartStore(CATALOGUE, full());
      store.change(filled());
      const more = addOne(store.cart.value, 101);
      expect(store.change(more)).toBe(true);
      expect(loadCart().lines).toEqual([
        ...filledLines(),
        { ...more.lines[1], unit: "m" },
      ]);
    });

    test("refused again, leaves none stored: a reload brings an empty cart, never lines since recorded", () => {
      storeCart(filled());
      const store = new CartStore(CATALOGUE, refusing("setItem"));
      expect(store.change(addOne(store.cart.value, 101))).toBe(false);
      expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
      // Emptied, as after Record or Discard, with none stored: kept.
      expect(store.change(freshCart(store.cart.value))).toBe(true);
      expect(loadCart().lines).toEqual([]);
    });

    test("an emptied cart is not kept while the browser will not remove the lines stored", () => {
      storeCart(filled());
      const store = new CartStore(CATALOGUE, refusing("setItem", "removeItem"));
      expect(store.change(freshCart(store.cart.value))).toBe(false);
      expect(loadCart().lines).toEqual(filled().lines);
    });

    test("marking the cart used leaves the stored cart, already this one", () => {
      storeCart(filled());
      const store = new CartStore(CATALOGUE, refusing("setItem"));
      expect(store.touch()).toBe(true);
      expect(loadCart().lines).toEqual(filled().lines);
    });

    test("an emptied cart is kept while the browser holds an empty one it will not remove", () => {
      storeCart(freshCart());
      const store = new CartStore(CATALOGUE, refusing("setItem", "removeItem"));
      expect(store.change(freshCart(cart({ volunteerId: 2 })))).toBe(true);
    });

    test("an empty cart kept with none stored is the one saved, its choices kept", () => {
      const store = new CartStore(CATALOGUE, refusing("setItem"));
      const named = freshCart(cart({ volunteerId: 2 }));
      expect(store.change(named)).toBe(true);
      expect(store.forSaving()?.volunteerId).toBe(2);
      expect(store.cart.value.volunteerId).toBe(2);
    });

    test("an emptied cart is kept where the browser shows nothing stored", () => {
      const store = new CartStore(
        CATALOGUE,
        refusing("getItem", "setItem", "removeItem"),
      );
      expect(store.change(freshCart())).toBe(true);
      expect(store.change(filled())).toBe(false);
    });

    test("once another tab writes, the cart shown is kept, and saved only if unchanged since", () => {
      const store = new CartStore(CATALOGUE, refusing("setItem"));
      const followed = vi.fn();
      const stop = store.listen(followed);
      store.change(filled());
      const there = new CartStore(CATALOGUE);
      there.change(addOne(filled(), 101));
      window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
      expect(followed).toHaveBeenCalledOnce();
      expect(store.cart.value.lines).toEqual(there.cart.value.lines);
      expect(store.forSaving()?.revision).toBe(there.cart.value.revision);
      // Changed there again, unheard: this tab's cart is not saved.
      there.change(filled());
      expect(store.forSaving()).toBeNull();
      stop();
    });
  });

  test("a newer catalogue clears choices it no longer has", () => {
    const store = new CartStore(CATALOGUE);
    store.change(cart({ volunteerId: 2 }));
    store.useCatalogue({ ...CATALOGUE, volunteers: [{ id: 1, name: "Ada" }] });
    expect(store.cart.value.volunteerId).toBeNull();
  });
});

describe("a package resized while the cart counts it (inventory-tng-0qi)", () => {
  /** The test catalogue with the radio's box made a box of `size`. */
  const boxOf = (size: string) => ({
    ...CATALOGUE,
    items: CATALOGUE.items.map((item) =>
      item.id === radio.id
        ? {
            ...item,
            packages: item.packages.map((pack) => ({ ...pack, size })),
          }
        : item,
    ),
  });
  const box = radio.packages[0] as Item["packages"][number];
  const first = (each: Cart) => each.lines[0] as Line;
  const boxes = (typed: string, sizes?: Record<string, string>): Cart =>
    cart({
      lines: [
        {
          itemId: 100,
          packages: { 1000: typed },
          loose: "",
          ...(sizes === undefined ? {} : { sizes }),
        },
      ],
    });

  test("a count typed is kept with the size shown; one unchanged keeps its size", () => {
    const typed = sized(boxes(""), boxes("2"), CATALOGUE);
    expect(typed.lines[0]?.sizes).toEqual({ 1000: "4.000" });
    // Unchanged, under a catalogue that now says 8, it keeps the 4.
    const kept = sized(typed, { ...typed, kind: "receipt" }, boxOf("8.000"));
    expect(kept.lines[0]?.sizes).toEqual({ 1000: "4.000" });
    // Typed again, it is at the size shown then.
    const again = sized(kept, boxes("3", { 1000: "4.000" }), boxOf("8.000"));
    expect(again.lines[0]?.sizes).toEqual({ 1000: "8.000" });
    // Blank, it holds no size.
    expect(
      sized(again, boxes("", { 1000: "8.000" }), CATALOGUE).lines[0],
    ).not.toHaveProperty("sizes");
  });

  test("a count of an item the catalogue no longer has keeps its size, and a new one has none", () => {
    const gone = { ...CATALOGUE, items: [] };
    const counted = boxes("2", { 1000: "4.000" });
    expect(sized(counted, counted, gone).lines[0]?.sizes).toEqual({
      1000: "4.000",
    });
    expect(sized(counted, boxes("3"), gone).lines[0]).not.toHaveProperty(
      "sizes",
    );
  });

  test("a line new to the cart keeps the sizes it brings, as one put back by Undo does", () => {
    const counted = boxes("2", { 1000: "4.000" });
    expect(sized(cart(), counted, boxOf("8.000")).lines[0]?.sizes).toEqual({
      1000: "4.000",
    });
  });

  test("a count kept before sizes were is taken as counted at the size shown", () => {
    const before = boxes("2");
    expect(sized(before, before, CATALOGUE).lines[0]?.sizes).toEqual({
      1000: "4.000",
    });
  });

  test("a count at another size is cleared, keeping its size, and said", () => {
    const counted = boxes("2", { 1000: "4.000" });
    const now = reconcile(counted, boxOf("8.000"));
    expect(now.lines[0]).toEqual({
      itemId: 100,
      packages: { 1000: "" },
      loose: "",
      sizes: { 1000: "4.000" },
    });
    expect(resized(first(now), { ...box, size: "8.000" })).toBe(true);
    // Resized back, the count cleared is still to be typed again: blank,
    // it would record less than was moved.
    expect(resized(first(now), box)).toBe(true);
    expect(reconcile(now, CATALOGUE)).toBe(now);
    expect(saidAsText(toBatch(now, CATALOGUE))).toEqual({
      problems: [`[${radio.name}]: enter the count`],
    });
    expect(resized(first(counted), box)).toBe(false);
    // The same size, written otherwise, is no change.
    expect(reconcile(counted, boxOf("4"))).toBe(counted);
    // Cleared once, it is left as it is.
    expect(reconcile(now, boxOf("8.000"))).toBe(now);
  });

  test("a count of none of a package resized is neither cleared nor asked for", () => {
    const none = cart({
      lines: [
        {
          itemId: 100,
          packages: { 1000: "0" },
          loose: "1",
          sizes: { 1000: "4.000" },
        },
      ],
    });
    expect(reconcile(none, boxOf("8.000"))).toBe(none);
    expect(toBatch(none, boxOf("8.000"))).toMatchObject({
      draft: { lines: [{ item_id: 100, quantity: "1", unit: "each" }] },
    });
  });

  test("a count is saved at the size it was counted at", () => {
    const counted = boxes("2", { 1000: "4.000" });
    const resizedRadio = { ...radio, packages: [{ ...box, size: "8.000" }] };
    expect(quantityOf(first(counted), resizedRadio)).toMatchObject({
      amount: 8000,
    });
    const batch = toBatch(counted, CATALOGUE);
    expect("draft" in batch && batch.draft.lines).toEqual([
      {
        item_id: 100,
        quantity: "8",
        package_id: 1000,
        package_size: "4",
        unit: "each",
      },
    ]);
  });

  test("a line typed in a unit the item is no longer counted in is cleared, keeping that unit, until typed again (inventory-tng-h7ua)", () => {
    const inFeet = { ...CATALOGUE, items: [radio, { ...cable, unit: "ft" }] };
    const typed = cart({
      lines: [
        {
          itemId: 101,
          packages: { 1001: "1" },
          sizes: { 1001: "305.000" },
          loose: "50",
          unit: "m",
          open: { k: { amount: "2", whole: false, usedUp: true, now: "9" } },
        },
      ],
    });
    const line = first(reconcile(typed, inFeet));
    expect(line).toEqual({
      itemId: 101,
      packages: { 1001: "" },
      loose: "",
      unit: "m",
      open: { k: { amount: "", whole: false, now: "" } },
    });
    expect(unitChanged(line, { ...cable, unit: "ft" })).toBe(true);
    // Cleared once, it stays so: nothing typed in the old unit is left.
    expect(reconcile({ ...typed, lines: [line] }, inFeet).lines[0]).toBe(line);
    // Typed again, the line is in the new unit.
    const again = cart({ lines: [{ ...line, loose: "40" }] });
    expect(
      sized({ ...typed, lines: [line] }, again, inFeet).lines[0]?.unit,
    ).toBe("ft");
    // In the unit it was typed in, nothing changes.
    expect(reconcile(typed, CATALOGUE)).toBe(typed);
  });

  test("each to m clears a count of units too (inventory-tng-h7ua)", () => {
    const inMetres = { ...CATALOGUE, items: [{ ...radio, unit: "m" }, cable] };
    const typed = cart({
      lines: [{ itemId: 100, packages: {}, loose: "3", unit: "each" }],
    });
    expect(first(reconcile(typed, inMetres))).toMatchObject({
      loose: "",
      unit: "each",
    });
  });

  test("a line with nothing typed keeps no unit, so a unit changed says nothing of it", () => {
    const blank = cart({ lines: [{ itemId: 101, packages: {}, loose: "" }] });
    expect(sized(cart(), blank, CATALOGUE).lines[0]).not.toHaveProperty("unit");
    const typed = cart({ lines: [{ itemId: 101, packages: {}, loose: "5" }] });
    const emptied = sized(typed, blank, CATALOGUE);
    expect(emptied.lines[0]).not.toHaveProperty("unit");
  });

  test("a line keeps the unit it was typed in until its amounts change (inventory-tng-57u8)", () => {
    const typed = cart({
      lines: [{ itemId: 101, packages: {}, loose: "50", unit: "m" }],
    });
    const inFeet = { ...CATALOGUE, items: [{ ...cable, unit: "ft" }] };
    const touched = { ...typed, installNumber: "7" };
    expect(sized(typed, touched, inFeet).lines[0]?.unit).toBe("m");
    const retyped = cart({
      lines: [{ itemId: 101, packages: {}, loose: "40", unit: "m" }],
    });
    expect(sized(typed, retyped, inFeet).lines[0]?.unit).toBe("ft");
    // A line new to the cart is typed in the unit shown, unless it brings
    // its own, as one put back by Undo does.
    const added = cart({ lines: [{ itemId: 101, packages: {}, loose: "1" }] });
    expect(sized(cart(), added, inFeet).lines[0]?.unit).toBe("ft");
    expect(sized(cart(), typed, inFeet).lines[0]?.unit).toBe("m");
  });

  test("a cart with a count cleared as resized is not saved until counted again", () => {
    const counted = cart({
      lines: [
        { itemId: 101, packages: {}, loose: "4" },
        {
          itemId: 100,
          packages: { 1000: "2" },
          loose: "1",
          sizes: { 1000: "4.000" },
        },
      ],
    });
    // Not the rest without it: that would record less than was moved.
    expect(saidAsText(toBatch(counted, boxOf("8.000")))).toEqual({
      problems: [`[${radio.name}]: enter the count`],
    });
    // Alone in the cart, said in place of say(ADD_SOMETHING)
    expect(
      saidAsText(toBatch(boxes("2", { 1000: "4.000" }), boxOf("8.000"))),
    ).toEqual({
      problems: [`[${radio.name}]: enter the count`],
    });
    // Counted again, at the size shown, it is saved.
    expect(
      toBatch(boxes("1", { 1000: "8.000" }), boxOf("8.000")),
    ).toMatchObject({
      draft: {
        lines: [
          {
            item_id: 100,
            quantity: "8",
            unit: "each",
            package_id: 1000,
            package_size: "8",
          },
        ],
      },
    });
  });

  test("a tab with the newer catalogue clears another tab's count, until typed again", () => {
    const there = new CartStore(CATALOGUE);
    there.change(boxes("2"));
    expect(loadCart().lines[0]?.sizes).toEqual({ 1000: "4.000" });
    const here = new CartStore(boxOf("8.000"));
    const line = () => first(here.cart.value);
    expect(line().packages).toEqual({ 1000: "" });
    // Another change keeps it said.
    here.change({ ...here.cart.value, kind: "receipt" });
    expect(line().sizes).toEqual({ 1000: "4.000" });
    here.change(boxes("1", line().sizes));
    expect(line()).toMatchObject({
      packages: { 1000: "1" },
      sizes: { 1000: "8.000" },
    });
  });

  test("a package's label scanned after its count was cleared counts one at the size shown", () => {
    storeCart(boxes("2", { 1000: "4.000" }));
    const store = new CartStore(boxOf("8.000"));
    store.change(addOne(store.cart.value, 100, 1000));
    expect(first(store.cart.value)).toMatchObject({
      packages: { 1000: "1" },
      sizes: { 1000: "8.000" },
    });
  });
});

test("a place is offered for the side still empty first", () => {
  const cart = freshCart();
  expect(placeSides(cart)).toEqual(["from", "to"]);
  expect(placeSides({ ...cart, fromId: 10 })).toEqual(["to", "from"]);
  // A receipt has only a to side, an install only a from side.
  expect(placeSides({ ...cart, kind: "receipt" })).toEqual(["to"]);
  expect(placeSides({ ...cart, kind: "install" })).toEqual(["from"]);
});
