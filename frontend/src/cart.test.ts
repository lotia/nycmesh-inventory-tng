import { describe, expect, test } from "vitest";
import { BUILD } from "./build";
import {
  addOne,
  askWho,
  type Cart,
  CartStore,
  freshCart,
  type Item,
  indexOf,
  keysToAsk,
  type Line,
  loadCart,
  offered,
  offeredAt,
  openingPackage,
  placed,
  quantityOf,
  reconcile,
  resized,
  STORAGE_KEY,
  sealedCounts,
  sealedHere,
  search,
  sized,
  storeCart,
  toBatch,
  unseen,
  withLine,
} from "./cart";
import type { Body, Draft } from "./outbox";

/** A batch with a key, as the outbox keeps it. */
let keys = 0;
function keyed(draft: Draft): Body {
  keys += 1;
  return {
    ...draft,
    key: `00000000-0000-4000-8000-${String(keys).padStart(12, "0")}`,
  };
}
const SHOWN = "99999999-9999-4999-8999-999999999999";

import { CATALOGUE, cable, radio, WITH_CAFE } from "./test-catalogue";

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
          {
            item_id: 100,
            quantity: "4",
            package_id: 1000,
            package_size: "4",
          },
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
        package_size: "305",
        quantity: "305",
        open_package_key: expect.any(String),
      },
      {
        item_id: 101,
        package_id: 1001,
        package_size: "305",
        quantity: "305",
        open_package_key: expect.any(String),
      },
      // The box is not: its radios become loose units.
      { item_id: 100, package_id: 1000, package_size: "4", quantity: "12" },
    ]);
    expect(sent[0]?.open_package_key).not.toBe(sent[1]?.open_package_key);
  });

  test("an opening counts only the items it opens", () => {
    const lines = [
      { itemId: 101, packages: { 1001: "1" }, loose: "" },
      { itemId: 100, packages: {}, loose: "" },
    ];
    expect(
      toBatch(cart({ kind: "open", lines, fromId: 10 }), CATALOGUE),
    ).toMatchObject({ what: "Opening: 1 item at Hub" });
  });

  test("taken from an open package: cut loose, or moved whole", () => {
    const key = "11111111-1111-4111-8111-111111111111";
    const other = "22222222-2222-4222-8222-222222222222";
    const lines = [
      {
        itemId: 101,
        packages: {},
        loose: "",
        open: {
          [key]: { amount: "20", whole: false },
          [other]: { amount: "120", whole: true },
        },
      },
    ];
    expect(toBatch(cart({ lines }), CATALOGUE)).toMatchObject({
      draft: {
        lines: [
          { item_id: 101, quantity: "20", open_package_key: key },
          {
            item_id: 101,
            quantity: "120",
            open_package_key: other,
            whole: true,
          },
        ],
      },
    });
    // At an install nothing moves whole: it is cut.
    const install = toBatch(
      cart({ kind: "install", lines, fromId: 10 }),
      CATALOGUE,
    );
    expect("draft" in install && install.draft.lines[1]).toEqual({
      item_id: 101,
      quantity: "120",
      open_package_key: other,
    });
    const receipt = toBatch(cart({ kind: "receipt", lines }), CATALOGUE);
    expect(receipt).toEqual({
      problems: ["Cat6 cable: Nothing is taken from an open package here."],
    });
    const bad = [
      {
        itemId: 101,
        packages: {},
        loose: "",
        open: { [key]: { amount: "x", whole: false } },
      },
    ];
    expect(toBatch(cart({ lines: bad }), CATALOGUE)).toEqual({
      problems: [
        "Cat6 cable: Enter how much from the open package, up to 3 decimals.",
      ],
    });
  });

  test("open packages offered: the catalogue's, and as waiting batches leave them", () => {
    const known = {
      id: 5,
      key: "11111111-1111-4111-8111-111111111111",
      item_id: 101,
      package_id: 1001,
      package_name: "Spool",
      location_id: 10,
      opened_at: "2026-11-03T10:00:00Z",
      left: "120.000",
      short_below: null,
    };
    const fresh = "33333333-3333-4333-8333-333333333333";
    const catalogue = { ...CATALOGUE, open_packages: [known] };
    const opening: Draft = {
      kind: "open",
      volunteer_id: 1,
      from_location_id: 10,
      to_location_id: 10,
      lines: [
        {
          item_id: 101,
          quantity: "305",
          package_id: 1001,
          package_size: "305",
          open_package_key: fresh,
        },
        // Sent already, so the catalogue's: not twice.
        {
          item_id: 101,
          quantity: "305",
          package_id: 1001,
          package_size: "305",
          open_package_key: known.key,
        },
        {
          item_id: 101,
          quantity: "305",
          package_id: 1001,
          package_size: "305",
        },
        // Counted in another size, or in none, as an older build's: the
        // server refuses them, so they open nothing to offer.
        {
          item_id: 101,
          quantity: "610",
          package_id: 1001,
          package_size: "610",
          open_package_key: "55555555-5555-4555-8555-555555555555",
        },
        {
          item_id: 101,
          quantity: "305",
          package_id: 1001,
          open_package_key: "66666666-6666-4666-8666-666666666666",
        },
      ],
    };
    const cut: Draft = {
      kind: "move",
      volunteer_id: 1,
      from_location_id: 10,
      to_location_id: 12,
      lines: [
        { item_id: 101, quantity: "20", open_package_key: known.key },
        { item_id: 101, quantity: "305", open_package_key: fresh, whole: true },
        { item_id: 101, quantity: "1" },
        // Opened on another phone: the catalogue's, once loaded again.
        {
          item_id: 101,
          quantity: "1",
          open_package_key: "44444444-4444-4444-8444-444444444444",
        },
      ],
    };
    const queuedAt = Date.parse("2026-11-04T09:00:00Z");
    const outbox = [
      { state: "waiting" as const, body: keyed(opening), queuedAt },
      { state: "waiting" as const, body: keyed(cut), queuedAt },
      // Saved, and the catalogue says it shows it.
      { state: "saved" as const, body: { ...cut, key: SHOWN }, queuedAt },
    ];
    const all = offered(
      { ...catalogue, saved: [SHOWN] },
      unseen(outbox, { ...catalogue, saved: [SHOWN] }),
    );
    expect(offeredAt(all, 101, 10)).toEqual([{ ...known, left: 100000 }]);
    expect(offeredAt(all, 101, 12)).toEqual([
      {
        id: 0,
        key: fresh,
        item_id: 101,
        package_id: 1001,
        package_name: "Spool",
        location_id: 12,
        opened_at: "2026-11-04T09:00:00.000Z",
        left: 305000,
        short_below: null,
      },
    ]);
  });

  test("an opening waiting of a package the catalogue has no more is named 'package'", () => {
    const opening: Draft = {
      kind: "open",
      volunteer_id: 1,
      from_location_id: 10,
      to_location_id: 10,
      lines: [
        {
          item_id: 101,
          quantity: "305",
          package_id: 1009,
          package_size: "305",
          open_package_key: "33333333-3333-4333-8333-333333333333",
        },
      ],
    };
    const [made] = offered(CATALOGUE, [
      { state: "waiting" as const, body: keyed(opening), queuedAt: 0 },
    ]);
    expect(made?.package_name).toBe("package");
    // Kept by an older build, in no size: refused, so offered nowhere.
    const sizeless = {
      ...opening,
      lines: opening.lines.map(({ package_size: _, ...line }) => line),
    };
    expect(
      offered(CATALOGUE, [
        { state: "waiting" as const, body: keyed(sizeless), queuedAt: 0 },
      ]),
    ).toEqual([]);
  });

  test("open packages said used up go with a cut, or alone as a report", () => {
    const key = "11111111-1111-4111-8111-111111111111";
    const line = (amount: string, extra = {}) => [
      {
        itemId: 101,
        packages: {},
        loose: "",
        open: { [key]: { amount, whole: false, usedUp: true, ...extra } },
      },
    ];
    expect(toBatch(cart({ lines: line("120") }), CATALOGUE)).toMatchObject({
      draft: {
        kind: "move",
        lines: [{ item_id: 101, quantity: "120", open_package_key: key }],
        used_up: [key],
      },
    });
    const alone = toBatch(cart({ lines: line("") }), CATALOGUE);
    expect(alone).toEqual({
      draft: {
        kind: "report",
        volunteer_id: 1,
        from_location_id: 10,
        lines: [],
        used_up: [key],
      },
      what: "Used up: 1 open package at Hub",
    });
    // A cart of nothing else, on an install with its number: a report
    // still, where it is, asking nothing of the install.
    const install = toBatch(
      cart({
        kind: "install",
        installNumber: "1234",
        toId: null,
        lines: line(""),
      }),
      CATALOGUE,
    );
    expect(install).toMatchObject({
      draft: { kind: "report", from_location_id: 10, used_up: [key] },
    });
    expect("draft" in install && install.draft.install_number).toBe(undefined);
    // A move with no To yet: a report needs none.
    const noTo = toBatch(cart({ toId: null, lines: line("") }), CATALOGUE);
    expect(noTo).toMatchObject({ draft: { kind: "report" } });
    // With a cut, the words say so.
    expect(toBatch(cart({ lines: line("120") }), CATALOGUE)).toMatchObject({
      what: "Move: 1 item from Hub to Van (1 open package said used up)",
    });
  });

  test("what is on an open package now goes with a cut, or alone", () => {
    const key = "11111111-1111-4111-8111-111111111111";
    const line = (taken: Record<string, unknown>) => [
      {
        itemId: 101,
        packages: {},
        loose: "",
        open: { [key]: { amount: "", whole: false, ...taken } },
      },
    ];
    expect(
      toBatch(cart({ lines: line({ amount: "20", now: "250" }) }), CATALOGUE),
    ).toMatchObject({
      draft: {
        kind: "move",
        lengths: [{ open_package_key: key, left: "250" }],
      },
      what: "Move: 1 item from Hub to Van (what is on 1 open package said)",
    });
    expect(toBatch(cart({ lines: line({ now: "12.5" }) }), CATALOGUE)).toEqual({
      draft: {
        kind: "report",
        volunteer_id: 1,
        from_location_id: 10,
        lines: [],
        lengths: [{ open_package_key: key, left: "12.5" }],
      },
      what: "What's on it: 1 open package at Hub",
    });
    // With used up too, and the words say both.
    expect(
      toBatch(cart({ lines: line({ now: "0", usedUp: true }) }), CATALOGUE),
    ).toMatchObject({
      what: "Used up: 1 open package at Hub (what is on 1 open package said)",
    });
    // Blank is no report; not a number is a problem.
    expect(toBatch(cart({ lines: line({ now: " " }) }), CATALOGUE)).toEqual({
      problems: ["Add something to the cart."],
    });
    // Counted each: whole units only.
    const radioLine = [
      {
        itemId: 100,
        packages: {},
        loose: "",
        open: { [key]: { amount: "", whole: false, now: "2.5" } },
      },
    ];
    expect(toBatch(cart({ lines: radioLine }), CATALOGUE)).toEqual({
      problems: [
        "LiteBeam 5AC: enter what is on the open package, in whole units, or leave it blank.",
      ],
    });
    expect(toBatch(cart({ lines: line({ now: "lots" }) }), CATALOGUE)).toEqual({
      problems: [
        "Cat6 cable: enter what is on the open package, up to 3 decimals, or leave it blank.",
      ],
    });
    // More than the server takes: said here, not refused with the cut.
    expect(
      toBatch(cart({ lines: line({ now: "1000000000" }) }), CATALOGUE),
    ).toEqual({
      problems: ["Cat6 cable: That is more than the inventory can count."],
    });
  });

  test("an open package said used up is offered no more", () => {
    const key = "11111111-1111-4111-8111-111111111111";
    const known = {
      id: 5,
      key,
      item_id: 101,
      package_id: 1001,
      package_name: "Spool",
      location_id: 10,
      opened_at: "2026-11-03T10:00:00Z",
      left: "120.000",
      short_below: null,
    };
    const report: Draft = {
      kind: "report",
      volunteer_id: 1,
      from_location_id: 10,
      lines: [],
      used_up: [key],
    };
    const catalogue = { ...CATALOGUE, open_packages: [known] };
    expect(
      offered(catalogue, [
        { state: "waiting" as const, body: keyed(report), queuedAt: 0 },
      ]),
    ).toEqual([]);
  });

  test("what is taken from an open package no longer offered is not sent", () => {
    const shown = "11111111-1111-4111-8111-111111111111";
    const gone = "22222222-2222-4222-8222-222222222222";
    const lines = [
      {
        itemId: 101,
        packages: {},
        loose: "",
        open: {
          [shown]: { amount: "5", whole: false },
          [gone]: { amount: "20", whole: false },
        },
      },
    ];
    const here = {
      id: 5,
      key: shown,
      item_id: 101,
      package_id: 1001,
      package_name: "Spool",
      location_id: 10,
      opened_at: "2026-11-03T10:00:00Z",
      left: 120_000,
      short_below: null,
    };
    // Offered elsewhere is not offered here.
    const elsewhere = { ...here, key: gone, location_id: 12 };
    const batch = toBatch(cart({ lines }), CATALOGUE, [here, elsewhere]);
    expect("draft" in batch && batch.draft.lines).toEqual([
      { item_id: 101, quantity: "5", open_package_key: shown },
    ]);
    // A cut of all it shows says it is used up, unless the volunteer
    // said not.
    const line = { itemId: 101, packages: {}, loose: "" };
    const all = [
      { ...line, open: { [shown]: { amount: "120", whole: false } } },
    ];
    expect(toBatch(cart({ lines: all }), CATALOGUE, [here])).toMatchObject({
      draft: { used_up: [shown] },
    });
    const kept = [
      {
        ...line,
        open: { [shown]: { amount: "120", whole: false, usedUp: false } },
      },
    ];
    const notSaid = toBatch(cart({ lines: kept }), CATALOGUE, [here]);
    expect("draft" in notSaid && notSaid.draft.used_up).toBe(undefined);
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
      draft: {
        lines: [
          {
            item_id: 101,
            quantity: "610",
            package_id: 1001,
            package_size: "305",
          },
        ],
      },
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

  test("a retired package's sealed stock can be taken but not received", () => {
    const retired = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 100
          ? {
              ...item,
              packages: item.packages.map((pack) => ({
                ...pack,
                retired: true,
              })),
            }
          : item,
      ),
    };
    const boxed = [{ itemId: 100, packages: { 1000: "1" }, loose: "" }];
    expect(toBatch(cart({ kind: "receipt", lines: boxed }), retired)).toEqual({
      problems: [
        "LiteBeam 5AC, Box: retired: taking only, so it can't be received.",
      ],
    });
    // A count that isn't one is said as such, not as retired.
    const unreadable = [{ itemId: 100, packages: { 1000: "x" }, loose: "" }];
    expect(
      toBatch(cart({ kind: "receipt", lines: unreadable }), retired),
    ).toEqual({ problems: ["LiteBeam 5AC: Count whole packages: Box."] });
    // And what else is wrong with the line, at once.
    const typo = [{ itemId: 100, packages: { 1000: "1" }, loose: "x" }];
    expect(toBatch(cart({ kind: "receipt", lines: typo }), retired)).toEqual({
      problems: [
        "LiteBeam 5AC, Box: retired: taking only, so it can't be received.",
        "LiteBeam 5AC: Enter the loose amount in each, up to 3 decimals.",
      ],
    });
    expect(toBatch(cart({ lines: boxed }), retired)).toMatchObject({
      draft: {
        lines: [
          { item_id: 100, quantity: "4", package_id: 1000, package_size: "4" },
        ],
      },
    });
    // Loose units of its item are received as ever.
    const loose = [{ itemId: 100, packages: {}, loose: "2" }];
    expect(
      toBatch(cart({ kind: "receipt", lines: loose }), retired),
    ).toMatchObject({ what: "Receipt: 1 item to Van" });
  });

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

  test("sealed counts follow the batches the catalogue does not show yet", () => {
    // The catalogue: 3 spools sealed at the hub, 1 in the van.
    const batch = (draft: Partial<Draft>) =>
      keyed({ kind: "move", volunteer_id: 1, lines: [], ...draft });
    const spools = (n: number, extra = {}) => ({
      item_id: 101,
      quantity: String(305 * n),
      package_id: 1001,
      package_size: "305",
      ...extra,
    });
    const at = (counts: Map<string, number>, place: number) =>
      counts.get(`1001@${place}`) ?? 0;
    const counts = sealedCounts(CATALOGUE, [
      // Two opened at the hub, each its own open package.
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "open",
          from_location_id: 10,
          to_location_id: 10,
          lines: [
            spools(1, {
              open_package_key: "11111111-1111-4111-8111-111111111111",
            }),
            spools(1, {
              open_package_key: "22222222-2222-4222-8222-222222222222",
            }),
          ],
        }),
      },
      // One moved from the van to the hub, and one received at the van;
      // a cut from an open package moves no sealed one.
      {
        state: "saved" as const,
        queuedAt: 0,
        body: batch({
          from_location_id: 12,
          to_location_id: 10,
          lines: [
            spools(1),
            {
              item_id: 101,
              quantity: "20",
              package_id: 1001,
              open_package_key: "33333333-3333-4333-8333-333333333333",
            },
          ],
        }),
      },
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "receipt",
          to_location_id: 12,
          lines: [spools(2)],
        }),
      },
      // One taken to an install from the hub, and loose cable: no spool.
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "install",
          from_location_id: 10,
          lines: [
            spools(1),
            { item_id: 101, quantity: "5" },
            // A package the catalogue has no more: not counted.
            spools(1, { package_id: 1009 }),
          ],
        }),
      },
    ]);
    expect(at(counts, 10)).toBe(3 - 2 + 1 - 1);
    expect(at(counts, 12)).toBe(1 - 1 + 2);
    // Counted in a size the spool had before a resize: refused, so none.
    const resized = sealedCounts(CATALOGUE, [
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "receipt",
          to_location_id: 12,
          lines: [spools(2, { package_size: "610" })],
        }),
      },
      // Stored by a build from before sizes were sent: refused, so none.
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "receipt",
          to_location_id: 12,
          lines: [spools(1, { package_size: null })],
        }),
      },
    ]);
    expect(at(resized, 12)).toBe(1);
    // As stored by another build, unreadable: skipped, never thrown.
    const unreadable = {
      state: "waiting" as const,
      queuedAt: 0,
      body: batch({
        from_location_id: 10,
        to_location_id: 12,
        lines: [
          spools(1, { quantity: "1.2345" }),
          {
            item_id: 101,
            quantity: "abc",
            open_package_key: "11111111-1111-4111-8111-111111111111",
          },
        ],
      }),
    };
    expect(at(sealedCounts(CATALOGUE, [unreadable]), 12)).toBe(1);
    expect(offered(CATALOGUE, [unreadable])).toEqual(offered(CATALOGUE, []));
    // The last sealed at the hub opened: the next opening there fills none.
    const opened = sealedCounts(CATALOGUE, [
      {
        state: "waiting" as const,
        queuedAt: 0,
        body: batch({
          kind: "open",
          from_location_id: 10,
          to_location_id: 10,
          lines: [spools(3)],
        }),
      },
    ]);
    expect(sealedHere(cable, 10, opened)).toBe(undefined);
    expect(sealedHere(cable, 12, opened)).toBe(cable.packages[0]);
  });

  test("unseen: batches waiting or saved that the catalogue does not show", () => {
    const entry = (state: "waiting" | "saved" | "refused" | "too_old") => ({
      state,
      queuedAt: 0,
      body: keyed({ kind: "move", volunteer_id: 1, lines: [] }),
    });
    const waiting = entry("waiting");
    const shown = entry("saved");
    const saved = entry("saved");
    const refused = entry("refused");
    // Listed with what older builds left (older.ts), never sent.
    const tooOld = entry("too_old");
    const outbox = [waiting, shown, saved, refused, tooOld];
    const catalogue = { ...CATALOGUE, saved: [shown.body.key] };
    expect(unseen(outbox, catalogue)).toEqual([waiting, saved]);
    // Asked about: saved ones first, then those waiting.
    expect(keysToAsk(outbox)).toEqual([
      shown.body.key,
      saved.body.key,
      waiting.body.key,
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

  test("search ignores accents, but shows the name as spelt", () => {
    const found = (text: string) =>
      search(WITH_CAFE, text).map((item) => item.name);
    for (const typed of [
      "cafe",
      "Café",
      "cafè",
      "CAFÉ",
      "cafe\u0301", // as typed decomposed
      "câbles patch", // the category
      "lodz", // the server's folding spells "ł" too
      "łódź", // as spelt
      "Łódź patch",
    ]) {
      expect(found(typed), typed).toEqual(["Café Łódź patch"]);
    }
    // A letter decomposing leaves alone, as spelt, with the accents left off.
    expect(found("łodz")).toEqual(["Café Łódź patch"]);
    // Each word on its own: one as spelt, one folded.
    expect(found("Câbles Cafe")).toEqual(["Café Łódź patch"]);
    // The category folded by the server, as another is spelt.
    expect(found("cables")).toEqual(["Cat6 cable", "Café Łódź patch"]);
  });

  test("search ignores compatibility forms, as an IME or a paste may give them", () => {
    expect(search(WITH_CAFE, "ｃａｆｅ").map((item) => item.name)).toEqual([
      "Café Łódź patch",
    ]);
    // With no folded text from the server, so only the page's stripping
    // finds it.
    const name = "Ｃａｆé ﬁlter 10µF";
    const odd = {
      ...CATALOGUE,
      items: [{ ...cable, id: 103, name, search_text: "" }],
    };
    for (const typed of ["cafe", "filter", "ﬁlter", "10µf", "10μF"]) {
      expect(
        search(odd, typed).map((item) => item.name),
        typed,
      ).toEqual([name]);
    }
  });

  test("a lone accent searches for nothing", () => {
    expect(search(CATALOGUE, "\u0301")).toEqual([]);
    expect(search(CATALOGUE, "beam \u0301").map((i) => i.id)).toEqual([100]);
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
    expect(resized(first(now), box)).toBe(false);
    expect(resized(first(now), { ...box, size: "8.000" })).toBe(true);
    // The same size, written otherwise, is no change.
    expect(reconcile(counted, boxOf("4"))).toBe(counted);
    // Cleared once, it is left as it is.
    expect(reconcile(now, boxOf("8.000"))).toBe(now);
  });

  test("a count is saved at the size it was counted at", () => {
    const counted = boxes("2", { 1000: "4.000" });
    const resizedRadio = { ...radio, packages: [{ ...box, size: "8.000" }] };
    expect(quantityOf(first(counted), resizedRadio)).toMatchObject({
      amount: 8000,
    });
    const batch = toBatch(counted, CATALOGUE);
    expect("draft" in batch && batch.draft.lines).toEqual([
      { item_id: 100, quantity: "8", package_id: 1000, package_size: "4" },
    ]);
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
    expect(toBatch(counted, boxOf("8.000"))).toEqual({
      problems: [`${radio.name}: enter the count`],
    });
    // Alone in the cart, said in place of "Add something to the cart."
    expect(toBatch(boxes("2", { 1000: "4.000" }), boxOf("8.000"))).toEqual({
      problems: [`${radio.name}: enter the count`],
    });
    // Counted again, at the size shown, it is saved.
    const again = cart({
      lines: [
        {
          itemId: 100,
          packages: { 1000: "1" },
          loose: "",
          sizes: { 1000: "8.000" },
        },
      ],
    });
    expect(toBatch(again, boxOf("8.000"))).toMatchObject({
      draft: {
        lines: [
          { item_id: 100, quantity: "8", package_id: 1000, package_size: "8" },
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
