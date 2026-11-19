import { describe, expect, test } from "vitest";
import { say } from "../shared/i18n";
import { CATALOGUE } from "../testing/catalogue";
import {
  firstOlder,
  KEY,
  keep,
  mixedOlder,
  OTHER_KEY,
  onlyOlder,
} from "../testing/saves";
import { toBatch } from "./batch";
import { addOne, freshCart, type Item } from "./cart";
import { type Older, readOlder } from "./older-builds";
import {
  goesBackWhole,
  ITEM_RETIRED as ITEM_RETIRED_PHRASE,
  LINES_LOST,
  lineWords,
  NO_LONGER_IN_CATALOGUE,
  RESCAN as RESCAN_PHRASE,
  RESIZED as RESIZED_PHRASE,
  toCart,
  unreadWords,
  withRetired,
} from "./older-to-cart";

/** In Ali's words (inventory-tng-0su), without "Sorry." (inventory-tng-93z). */
const ITEM_RETIRED = say(ITEM_RETIRED_PHRASE);
const RESCAN = say(RESCAN_PHRASE);
const RESIZED = say(RESIZED_PHRASE);

/** An older cart of item 100 holding `packages`, as read. */
function cartHolding(packages: Record<string, string>): Older {
  keep("inventory.cart.v1", {
    ...freshCart(),
    lines: [{ itemId: 100, packages, loose: "" }],
  });
  return firstOlder();
}

const retirePackages = (item: Item): Item => ({
  ...item,
  packages: item.packages.map((pack) => ({ ...pack, retired: true })),
});

/** Every package retired with some left: still offered, taking only. */
const PACKAGES_RETIRED = {
  ...CATALOGUE,
  items: CATALOGUE.items.map(retirePackages),
};

describe("as a cart", () => {
  test("a batch's lines go back by item: whole packages as packages, the rest loose", () => {
    const { cart, gaps } = toCart(
      onlyOlder({
        lines: [
          { item_id: 100, quantity: "8.000", package_id: 1000 },
          { item_id: 100, quantity: "1" },
          // Not whole boxes of 4, so loose, for the cart to question.
          { item_id: 100, quantity: "2", package_id: 1000 },
          { item_id: 101, quantity: 3.5 },
        ],
      }),
      CATALOGUE,
    );
    expect(cart).toMatchObject({
      kind: "move",
      volunteerId: 1,
      fromId: 10,
      toId: 12,
      installNumber: "",
      lines: [
        { itemId: 100, packages: { 1000: "2" }, loose: "3", typed: true },
        { itemId: 101, packages: {}, loose: "3.5", typed: true },
      ],
    });
    expect(gaps).toEqual([]);
  });

  test("a save's cart keeps its batch key, to be sent under; a cart's has none", () => {
    expect(toCart(onlyOlder(), CATALOGUE).cart.batchKey).toBe(KEY);
    // A key no build made is not sent: the outbox gives a new one.
    expect(toCart(onlyOlder({ key: "c" }), CATALOGUE).cart).not.toHaveProperty(
      "batchKey",
    );
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    });
    const cart = readOlder().find((each) => each.batch === null) as Older;
    expect(toCart(cart, CATALOGUE).cart).not.toHaveProperty("batchKey");
  });

  test("one put back under its key keeps what it went back with, sent as what it was changed from; one without a key keeps none (inventory-tng-0gg)", () => {
    const { cart } = toCart(onlyOlder(), CATALOGUE);
    expect(cart.changedFrom).toEqual([{ item_id: 101, quantity: "2" }]);
    const batch = toBatch(addOne(cart, 101), CATALOGUE);
    expect(batch).toMatchObject({
      draft: { changed_from: [{ item_id: 101, quantity: "2" }] },
    });
    expect(toCart(onlyOlder({ key: "c" }), CATALOGUE).cart).not.toHaveProperty(
      "changedFrom",
    );
  });

  test("a cart a save filled keeps that save's key, to be sent under", () => {
    // As this build's cart (v2) keeps it, once a newer build reads it.
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
      batchKey: KEY,
    });
    expect(toCart(firstOlder(), CATALOGUE).cart.batchKey).toBe(KEY);
  });

  test.each([
    [
      "main's words",
      {
        note: "Not saved: its key was already used for a different batch. Please report this.",
      },
    ],
    ["this build's code", { reason: { code: "key_reused" } }],
    [
      "this build's code for one recorded elsewhere",
      { reason: { code: "recorded_elsewhere" } },
    ],
  ])(
    "a save refused for its key (%s) goes back without it, as the inventory holds it for another",
    (_name, why) => {
      const refused = onlyOlder({}, { state: "refused", ...why });
      expect(toCart(refused, CATALOGUE).cart).not.toHaveProperty("batchKey");
      // Refused for anything else, the key never took it: kept.
      const invalid = onlyOlder({}, { state: "refused", note: "Not saved." });
      expect(toCart(invalid, CATALOGUE).cart.batchKey).toBe(KEY);
    },
  );

  test("what the cart cannot hold is told, to do again", () => {
    const { cart, gaps } = toCart(
      onlyOlder({
        lines: [
          { item_id: 101, quantity: "5", open_package_key: OTHER_KEY },
          { item_id: "cable", quantity: "1" },
        ],
        lengths: [{ open_package_key: OTHER_KEY, left: "10" }],
      }),
      CATALOGUE,
    );
    // The take is not put back as its item's empty line.
    expect(cart.lines).toEqual([]);
    // Each line left out, by its item; a report by what it says.
    expect(gaps).toEqual([
      "Could not read 1 previously added item, scan it again.",
      `Cat6 cable: ${RESCAN}`,
      `What is on an open package: ${RESCAN}`,
    ]);
  });

  test("lines that cannot be read are counted, and an item gone kept", () => {
    const { cart, gaps } = toCart(
      onlyOlder({
        lines: [
          { item_id: 101, quantity: "lots" },
          { item_id: 101, quantity: 0.30000000000000004 },
          { item_id: 999, quantity: "2", open_package_key: OTHER_KEY },
        ],
      }),
      CATALOGUE,
    );
    // An unread line's item goes back, to count again; a take does not.
    expect(cart.lines.map((line) => line.itemId)).toEqual([101]);
    expect(gaps).toEqual([
      "Could not read 2 previously added items, scan them again.",
      `An item no longer in the catalogue: ${RESCAN}`,
    ]);
  });

  test("a package no longer offered is neither counted loose nor dropped, but told", () => {
    const { cart, gaps } = toCart(
      onlyOlder({
        lines: [
          // A box retired with none sealed left: 4 loose is not what
          // was moved.
          { item_id: 100, quantity: "4", package_id: 1004 },
          { item_id: 100, quantity: "2", package_id: 1003 },
          { item_id: 100, quantity: "8", package_id: 1000 },
          // Resized since: said apart, each once for the save.
          {
            item_id: 100,
            quantity: "4",
            package_id: 1000,
            package_size: 2,
          },
          // An item gone is named apart (withRetired), as it was.
          { item_id: 999, quantity: "2", package_id: 9999 },
        ],
      }),
      CATALOGUE,
    );
    expect(cart.lines).toEqual([
      { itemId: 100, packages: { 1000: "2" }, loose: "", typed: true },
      { itemId: 999, packages: {}, loose: "2", typed: true },
    ]);
    // In the order kept, each said once for its item.
    expect(gaps).toEqual([
      `LiteBeam 5AC: ${ITEM_RETIRED}`,
      `LiteBeam 5AC: ${RESIZED}`,
    ]);
  });

  test("an older cart's count of a package no longer offered is told, not kept unseen", () => {
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [
        {
          itemId: 100,
          packages: { 1000: "1", 1004: "2", 1005: "", 1006: "0" },
          loose: "1",
        },
        // An item gone keeps what it held, to be named as it is.
        { itemId: 999, packages: { 9999: "1" }, loose: "" },
      ],
    });
    const { cart, gaps } = toCart(firstOlder(), CATALOGUE);
    expect(cart.lines).toEqual([
      { itemId: 100, packages: { 1000: "1" }, loose: "1", typed: true },
      { itemId: 999, packages: { 9999: "1" }, loose: "", typed: true },
    ]);
    expect(gaps).toEqual([`LiteBeam 5AC: ${ITEM_RETIRED}`]);
    // None of one held, nothing is told; one unreadable is, as the cart
    // reads it as some.
    const told = (packages: Record<string, string>) =>
      toCart(cartHolding(packages), CATALOGUE).gaps;
    expect(told({ 1000: "1", 1004: "" })).toEqual([]);
    expect(told({ 1000: "1", 1004: "lots" })).toEqual([
      `LiteBeam 5AC: ${ITEM_RETIRED}`,
    ]);
  });

  test("an older cart's count of a package resized since it was counted is told, not put back at today's size", () => {
    // As a later build reads a v2 cart, which keeps the size each count
    // was made at: 2 boxes of 2 are 4, not 2 boxes of today's 4.
    const resized = (lines: unknown[]) => {
      keep("inventory.cart.v1", { ...freshCart(), lines });
      return toCart(firstOlder(), CATALOGUE);
    };
    const { cart, gaps, parts } = resized([
      {
        itemId: 100,
        packages: { 1000: "2" },
        sizes: { 1000: "2" },
        loose: "1",
      },
    ]);
    expect(cart.lines).toEqual([
      { itemId: 100, packages: {}, loose: "1", typed: true },
    ]);
    expect(gaps).toEqual([`LiteBeam 5AC: ${RESIZED}`]);
    expect(parts.map(({ words, note }) => [words, note])).toEqual([
      ["LiteBeam 5AC: 1", null],
      ["LiteBeam 5AC: 4", RESIZED],
    ]);
    expect(goesBackWhole(firstOlder(), CATALOGUE)).toBe(false);
    // A count unread is said as typed; one at today's size, or with no
    // size kept, goes back.
    expect(
      resized([
        { itemId: 100, packages: { 1000: "lots" }, sizes: { 1000: "2" } },
      ]).parts.map(({ words }) => words),
    ).toEqual(["LiteBeam 5AC: lots × Box"]);
    expect(
      resized([
        {
          itemId: 100,
          packages: { 1000: "2" },
          sizes: { 1000: "4.000" },
          loose: "",
        },
      ]),
    ).toMatchObject({
      cart: { lines: [{ itemId: 100, packages: { 1000: "2" } }] },
      gaps: [],
    });
  });

  test("a package retired with some left is still offered: counted, not told", () => {
    const { cart, gaps } = toCart(
      onlyOlder({ lines: [{ item_id: 100, quantity: "8", package_id: 1000 }] }),
      PACKAGES_RETIRED,
    );
    expect(cart.lines).toEqual([
      { itemId: 100, packages: { 1000: "2" }, loose: "", typed: true },
    ]);
    expect(gaps).toEqual([]);
  });

  test("lines that cannot be read even as a list are not counted", () => {
    const { cart, gaps } = toCart(onlyOlder({ lines: "many" }), CATALOGUE);
    expect(cart.lines).toEqual([]);
    expect(gaps).toEqual([say(LINES_LOST)]);
  });

  test("a package resized since is not counted again in today's size", () => {
    const { cart, gaps } = toCart(
      onlyOlder({
        lines: [
          // Two boxes of 2, which hold 4 now: neither one box nor 4
          // loose is what was moved.
          {
            item_id: 100,
            quantity: "4",
            package_id: 1000,
            package_size: "2",
          },
          // The size it was saved in is the size now.
          {
            item_id: 100,
            quantity: "8",
            package_id: 1000,
            package_size: 4,
          },
        ],
      }),
      CATALOGUE,
    );
    expect(cart.lines).toEqual([
      { itemId: 100, packages: { 1000: "2" }, loose: "", typed: true },
    ]);
    expect(gaps).toEqual([`LiteBeam 5AC: ${RESIZED}`]);
  });

  test("an opening's packages go back as packages to open", () => {
    const { cart } = toCart(
      onlyOlder({
        kind: "open",
        to_location_id: 10,
        lines: [
          {
            item_id: 101,
            quantity: "305",
            package_id: 1001,
            open_package_key: KEY,
          },
          {
            item_id: 101,
            quantity: "305",
            package_id: 1001,
            open_package_key: OTHER_KEY,
          },
        ],
      }),
      CATALOGUE,
    );
    expect(cart).toMatchObject({
      kind: "open",
      lines: [{ itemId: 101, packages: { 1001: "2" }, loose: "" }],
    });
  });

  test("a report, or no kind, keeps the cart's; places and names gone from the catalogue are cleared", () => {
    const { cart } = toCart(
      onlyOlder({ kind: "report", volunteer_id: 99, from_location_id: 98 }),
      CATALOGUE,
      { ...freshCart(), kind: "install", volunteerId: 2 },
    );
    expect(cart).toMatchObject({
      kind: "install",
      volunteerId: null,
      fromId: null,
    });
  });

  test("an older cart's lines go back as typed, and what it took from open packages is told", () => {
    keep("inventory.cart.v1", {
      ...freshCart(),
      kind: "install",
      installNumber: "123",
      lines: [
        {
          itemId: 101,
          packages: { 1001: "1" },
          loose: "2",
          open: {
            [KEY]: { amount: "3", whole: false },
            [OTHER_KEY]: { amount: "", whole: false },
          },
          picked: 4,
        },
        // Nothing said of an open package: no amount, or none readable.
        {
          itemId: 102,
          packages: {},
          loose: "",
          open: { [KEY]: "odd", [OTHER_KEY]: { amount: 3 } },
        },
        { itemId: 100, packages: { 1000: 7 }, loose: "1" },
        { packages: {} },
      ],
    });
    const { cart, gaps } = toCart(firstOlder(), CATALOGUE);
    expect(cart).toMatchObject({
      kind: "install",
      installNumber: "123",
      lines: [
        { itemId: 101, packages: { 1001: "1" }, loose: "2", typed: true },
        { itemId: 102, packages: {}, loose: "", typed: true },
        { itemId: 100, packages: {}, loose: "1", typed: true },
      ],
    });
    expect(gaps).toEqual([
      "Could not read 1 previously added item, scan it again.",
      `Cat6 cable: ${RESCAN}`,
    ]);
  });

  test.each([
    ["used up", { usedUp: true }],
    ["what is on it", { amount: "", now: "12" }],
    ["an amount it could not read", { amount: "lots" }],
  ])(
    "an older cart's line that said %s of an open package is told",
    (_name, said) => {
      keep("inventory.cart.v1", {
        ...freshCart(),
        lines: [
          { itemId: 101, packages: {}, loose: "", open: { [KEY]: said } },
        ],
      });
      const { cart, gaps } = toCart(firstOlder(), CATALOGUE);
      expect(gaps).toEqual([`Cat6 cable: ${RESCAN}`]);
      // All it held left out, so not put back as an empty line.
      expect(cart.lines).toEqual([]);
    },
  );
});

describe("line by line", () => {
  test("each line is listed in the order kept, what cannot go back with why", () => {
    const { parts, lost } = toCart(mixedOlder(), CATALOGUE);
    expect(lost).toBeNull();
    expect(parts.map(({ words, note }) => [words, note])).toEqual([
      ["LiteBeam 5AC: 4", ITEM_RETIRED],
      ["Cat6 cable: 12 m from an open package", RESCAN],
      ["LiteBeam 5AC: 4", RESIZED],
      ["LiteBeam 5AC: 2 × Box", null],
      ["Open package said used up", RESCAN],
    ]);
  });

  test("an older cart's line of an item gone says how much it held", () => {
    expect(
      lineWords(
        { itemId: 999, packages: { 5: "2", 6: "0" }, loose: "1.5" },
        undefined,
      ),
    ).toBe(
      "An item no longer in the catalogue: 2 × a removed package or item, 1.5 loose",
    );
    expect(lineWords({ itemId: 999, packages: {}, loose: "" }, undefined)).toBe(
      say(NO_LONGER_IN_CATALOGUE),
    );
  });

  test("an older cart's line is listed in its parts: what goes back, a package gone, and each open package", () => {
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [
        {
          itemId: 100,
          packages: { 1000: "1", 1004: "2" },
          loose: "",
          open: { [KEY]: { usedUp: true } },
        },
        {
          itemId: 101,
          packages: {},
          loose: "",
          open: { [KEY]: { amount: "3" } },
        },
        {
          itemId: 101,
          packages: {},
          loose: "",
          open: { [OTHER_KEY]: { now: "9" } },
        },
      ],
    });
    const { cart, parts } = toCart(firstOlder(), CATALOGUE);
    expect(parts.map(({ words, note }) => [words, note ?? undefined])).toEqual([
      ["LiteBeam 5AC: 1 × Box", undefined],
      ["LiteBeam 5AC: 2 × a removed package or item", ITEM_RETIRED],
      ["LiteBeam 5AC: Open package said used up", RESCAN],
      ["Cat6 cable: 3 m from an open package", RESCAN],
      ["Cat6 cable: What is on an open package", RESCAN],
    ]);
    expect(cart.lines.map((line) => line.itemId)).toEqual([100]);
  });

  /** The catalogue, naming the radio's box retired with none left, 1004
   * (Item.retired_packages). */
  const NAMED = {
    ...CATALOGUE,
    items: CATALOGUE.items.map((item) =>
      item.id === 100
        ? {
            ...item,
            retired_packages: [{ id: 1004, name: "Old box", size: "2.000" }],
          }
        : item,
    ),
  };

  test("a package no longer offered is named where the catalogue names it, never put back", () => {
    const save = onlyOlder({
      lines: [
        { item_id: 100, quantity: "4", package_id: 1004 },
        // Counted at the size saved, not the size named now.
        { item_id: 100, quantity: "9", package_id: 1004, package_size: 3 },
        // Not whole ones of it: by its amount, as before.
        { item_id: 100, quantity: "3", package_id: 1004 },
        // Not named at all: by its amount, as before.
        { item_id: 100, quantity: "4", package_id: 1005 },
        { item_id: 100, quantity: "8", package_id: 1000 },
      ],
    });
    const { cart, parts, gaps } = toCart(save, NAMED);
    expect(parts.map(({ words, note }) => [words, note ?? undefined])).toEqual([
      ["LiteBeam 5AC: 2 × Old box", ITEM_RETIRED],
      ["LiteBeam 5AC: 3 × Old box", ITEM_RETIRED],
      ["LiteBeam 5AC: 3", ITEM_RETIRED],
      ["LiteBeam 5AC: 4", ITEM_RETIRED],
      ["LiteBeam 5AC: 2 × Box", undefined],
    ]);
    expect(gaps).toEqual([`LiteBeam 5AC: ${ITEM_RETIRED}`]);
    // Named only: never in the cart, nor offered.
    expect(cart.lines).toEqual([
      { itemId: 100, packages: { 1000: "2" }, loose: "", typed: true },
    ]);
    expect(goesBackWhole(save, NAMED)).toBe(false);
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: { 1004: "2", 1005: "1" }, loose: "" }],
    });
    expect(
      toCart(readOlder()[1] as Older, NAMED).parts.map(({ words }) => words),
    ).toEqual([
      "LiteBeam 5AC: 2 × Old box",
      "LiteBeam 5AC: 1 × a removed package or item",
    ]);
  });

  test("a note left behind names its item, one named only too", () => {
    const { gaps } = toCart(
      onlyOlder({
        lines: [
          { item_id: 103, quantity: "2", open_package_key: OTHER_KEY },
          { item_id: 101, quantity: "3", open_package_key: OTHER_KEY },
          { item_id: 101, quantity: "4", open_package_key: KEY },
        ],
      }),
      CATALOGUE,
    );
    // Retired with none left, so not offered: named from retired_items.
    expect(gaps).toEqual([
      `NanoStation M5: ${RESCAN}`,
      `Cat6 cable: ${RESCAN}`,
    ]);
  });

  test("notes are in Ali's words, without 'Sorry.'", () => {
    const notes = [ITEM_RETIRED, RESCAN, RESIZED, say(LINES_LOST)];
    expect(notes.filter((note) => note.includes("Sorry"))).toEqual([]);
    // Counted as a plural, one and more.
    expect([unreadWords(1), unreadWords(2)]).toEqual([
      "Could not read 1 previously added item, scan it again.",
      "Could not read 2 previously added items, scan them again.",
    ]);
  });

  test.each([
    ["a package no longer offered", { package_id: 1004 }],
    ["a package resized since", { package_id: 1000, package_size: 2 }],
    ["a take from an open package", { open_package_key: OTHER_KEY }],
  ])("one holding only %s has nothing to put back", (_name, line) => {
    const { cart, parts } = toCart(
      onlyOlder({ lines: [{ item_id: 100, quantity: "4", ...line }] }),
      CATALOGUE,
    );
    expect(cart.lines).toEqual([]);
    expect(parts).toHaveLength(1);
  });
});

describe("put back without a tap", () => {
  test("one that goes back whole does", () => {
    expect(goesBackWhole(onlyOlder(), CATALOGUE)).toBe(true);
  });

  test.each([
    ["a line unread", { lines: [{ item_id: 101, quantity: "lots" }] }],
    ["its lines unread", { lines: "many" }],
    ["nothing to put back", { lines: [] }],
    ["a report", { used_up: [OTHER_KEY] }],
    [
      "a take from an open package",
      { lines: [{ item_id: 101, quantity: "2", open_package_key: OTHER_KEY }] },
    ],
    [
      "a package resized",
      {
        lines: [
          { item_id: 100, quantity: "4", package_id: 1000, package_size: 2 },
        ],
      },
    ],
    ["an item retired, none left", { lines: [{ item_id: 103, quantity: 1 }] }],
    [
      "a package gone, which would go back loose",
      { lines: [{ item_id: 100, quantity: "4", package_id: 1003 }] },
    ],
  ])("one with %s does not", (_name, body) => {
    expect(goesBackWhole(onlyOlder(body), CATALOGUE)).toBe(false);
  });

  test("one the inventory refused does not, as its reason is said only while it is listed", () => {
    const refused = onlyOlder(
      {},
      { state: "refused", note: "Not saved. Line 1: too many." },
    );
    expect(goesBackWhole(refused, CATALOGUE)).toBe(false);
  });

  test("an older cart holding some of a package gone, or a count unread of one, does not; none of one, it does", () => {
    expect(goesBackWhole(cartHolding({ 1003: "2" }), CATALOGUE)).toBe(false);
    expect(goesBackWhole(cartHolding({ 1004: "lots" }), CATALOGUE)).toBe(false);
    expect(
      goesBackWhole(cartHolding({ 1000: "1", 1003: "", 1004: "0" }), CATALOGUE),
    ).toBe(true);
    // Some of a package retired with some left: offered, taking only.
    expect(goesBackWhole(cartHolding({ 1000: "1" }), PACKAGES_RETIRED)).toBe(
      false,
    );
    expect(goesBackWhole(cartHolding({ 1000: "lots" }), PACKAGES_RETIRED)).toBe(
      false,
    );
  });

  test.each([
    ["a package retired, some left", retirePackages],
    [
      "an item retired, some left",
      (item: Item) => ({ ...item, retired: true }),
    ],
  ])("one with %s does not", (_name, retire) => {
    const retired = { ...CATALOGUE, items: CATALOGUE.items.map(retire) };
    const body = { lines: [{ item_id: 100, quantity: "4", package_id: 1000 }] };
    expect(goesBackWhole(onlyOlder(body), CATALOGUE)).toBe(true);
    expect(goesBackWhole(onlyOlder(body), retired)).toBe(false);
  });
});

test("an item retired with none left is named, with its packages, never offered", () => {
  const older = onlyOlder({
    lines: [{ item_id: 103, quantity: "4", package_id: 1003 }],
  });
  expect(toCart(older, withRetired(CATALOGUE)).cart.lines).toEqual([
    { itemId: 103, packages: { 1003: "2" }, loose: "", typed: true },
  ]);
  expect(
    withRetired(CATALOGUE).items.find((item) => item.id === 103),
  ).toMatchObject({ name: "NanoStation M5", retired: true });
  expect(CATALOGUE.items.some((item) => item.id === 103)).toBe(false);
});
