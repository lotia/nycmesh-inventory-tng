import { describe, expect, test } from "vitest";
import { freshCart } from "./cart";
import {
  goesBackWhole,
  isListed,
  nextWhole,
  type Older,
  OlderStore,
  olderId,
  readOlder,
  removeOlder,
  tidyOlder,
  toCart,
  withRetired,
} from "./older";
import { CATALOGUE } from "./test-catalogue";
import { refusing } from "./test-storage";

const KEY = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
const OTHER = "6f1e2d3c-4b5a-4968-8776-5a4b3c2d1e0f";

/** A batch as an older build kept it: a move of cable, waiting. */
function saveOf(fields: Record<string, unknown> = {}, body = {}) {
  return {
    body: {
      key: KEY,
      kind: "move",
      volunteer_id: 1,
      from_location_id: 10,
      to_location_id: 12,
      lines: [{ item_id: 101, quantity: "2" }],
      ...body,
    },
    what: "Move: 1 item from Hub to Van",
    queuedAt: 1_700_000_000_000,
    state: "waiting",
    waitingFor: null,
    note: null,
    transactionId: null,
    failures: 0,
    ...fields,
  };
}

function keep(key: string, value: unknown) {
  localStorage.setItem(key, JSON.stringify(value));
}

const listed = () =>
  readOlder().map(({ key, batch, refused }) => ({ key, batch, refused }));

describe("reading what older builds left", () => {
  test("saves waiting or refused are listed; saved ones are not", () => {
    keep("inventory.outbox.v3", [
      saveOf(),
      saveOf({ state: "refused", note: "Not saved." }, { key: OTHER }),
      saveOf({ state: "saved" }, { key: "c" }),
    ]);
    expect(listed()).toEqual([
      { key: "inventory.outbox.v3", batch: KEY, refused: null },
      { key: "inventory.outbox.v3", batch: OTHER, refused: "Not saved." },
    ]);
  });

  test("only the parts every version kept are read, and anything else is tolerated", () => {
    keep("inventory.outbox.v3", [
      saveOf(
        { queuedAt: "yesterday", what: 5, extra: { deep: [1] } },
        {
          kind: "teleport",
          volunteer_id: "Ada",
          install_number: 7,
          // A number, as v1 kept it; a line with no item; one from a
          // later version, with fields this build never knew.
          lines: [
            { item_id: 100, quantity: 3 },
            { quantity: "1" },
            { item_id: 101, quantity: "1.5", colour: "blue" },
          ],
          used_up: [OTHER],
        },
      ),
      "not a save",
      { state: "waiting" },
      null,
    ]);
    keep("inventory.cart.v1", { lines: "many" });
    expect(readOlder()).toEqual([
      {
        key: "inventory.outbox.v3",
        batch: KEY,
        kind: null,
        volunteerId: null,
        fromId: 10,
        toId: 12,
        installNumber: "7",
        at: null,
        refused: null,
        lines: [
          {
            item_id: 100,
            quantity: 3,
            package_id: null,
            package_size: null,
            open_package_key: null,
          },
          null,
          {
            item_id: 101,
            quantity: "1.5",
            package_id: null,
            package_size: null,
            open_package_key: null,
          },
        ],
        linesLost: false,
        reports: 1,
      },
    ]);
  });

  test("of v1 to v3, only the newest held is read, as each took over the one before", () => {
    keep("inventory.outbox.v1", [saveOf({}, { key: OTHER })]);
    keep("inventory.outbox.v2", [saveOf()]);
    expect(listed().map((each) => each.key)).toEqual(["inventory.outbox.v2"]);
  });

  test("a key kept as v4 keeps it is read too", () => {
    keep("inventory.outbox.v4", {
      build: "2026-11-12T10:00:00.000Z",
      entries: [saveOf()],
    });
    expect(listed()).toEqual([
      { key: "inventory.outbox.v4", batch: KEY, refused: null },
    ]);
  });

  test("a save held back as too old is listed, from an older key or this build's", () => {
    // Under this build's key, only those: the rest are the outbox's.
    const build = "2026-11-12T10:00:00.000Z";
    keep("inventory.outbox.v4", {
      build,
      entries: [saveOf({ state: "too_old", build })],
    });
    keep("inventory.outbox.v5", {
      build,
      entries: [
        saveOf({ state: "too_old", build }, { key: OTHER }),
        saveOf({ build }, { key: "c" }),
        saveOf({ state: "refused", note: "Not saved.", build }, { key: "d" }),
        saveOf({ state: "saved", build }, { key: "e" }),
      ],
    });
    expect(listed()).toEqual([
      { key: "inventory.outbox.v4", batch: KEY, refused: null },
      { key: "inventory.outbox.v5", batch: OTHER, refused: null },
    ]);
  });

  test("a cart with lines is listed after the saves; one without is not", () => {
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: { 1000: "1" }, loose: "" }],
      usedAt: 1_700_000_000_000,
    });
    keep("inventory.outbox.v3", [saveOf()]);
    expect(readOlder().map((each) => [each.key, each.batch, each.at])).toEqual([
      ["inventory.outbox.v3", KEY, 1_700_000_000_000],
      ["inventory.cart.v1", null, 1_700_000_000_000],
    ]);
    keep("inventory.cart.v1", freshCart());
    expect(readOlder()).toHaveLength(1);
  });

  test("this build's own keys are not older", () => {
    keep("inventory.outbox.v5", {
      build: "x",
      entries: [saveOf({ build: "x" })],
    });
    keep("inventory.cart.v2", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    });
    expect(readOlder()).toEqual([]);
  });
});

describe("tidying on load", () => {
  test("older keys with nothing to list go", () => {
    keep("inventory.outbox.v3", [saveOf({ state: "saved" })]);
    keep("inventory.cart.v1", freshCart());
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
  });

  test("those holding something to list stay, as they were", () => {
    const saves = JSON.stringify([saveOf(), saveOf({ state: "saved" })]);
    const cart = JSON.stringify({
      ...freshCart(),
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    });
    localStorage.setItem("inventory.outbox.v3", saves);
    localStorage.setItem("inventory.cart.v1", cart);
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v3")).toBe(saves);
    expect(localStorage.getItem("inventory.cart.v1")).toBe(cart);
  });

  test("of v1 to v3, those whose every save a newer one holds go", () => {
    keep("inventory.outbox.v1", [saveOf()]);
    keep("inventory.outbox.v2", [saveOf({ state: "saved" })]);
    keep("inventory.outbox.v3", [saveOf({}, { key: OTHER }), "not a batch"]);
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v1")).toBeNull();
    // Only saved ones, which v3 never took: nothing to lose either.
    expect(localStorage.getItem("inventory.outbox.v2")).toBeNull();
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("a save an older tab kept after a newer one took over stays, unlisted", () => {
    // v2 took over v1 only while it held nothing: OTHER came after.
    const v1 = JSON.stringify([saveOf(), saveOf({}, { key: OTHER })]);
    localStorage.setItem("inventory.outbox.v1", v1);
    keep("inventory.outbox.v2", [saveOf()]);
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v1")).toBe(v1);
    expect(listed().map((each) => each.key)).toEqual(["inventory.outbox.v2"]);
  });

  test("this build's outbox key stays, whatever it holds", () => {
    const kept = JSON.stringify({
      build: "x",
      entries: [saveOf({ state: "saved", build: "x" })],
    });
    localStorage.setItem("inventory.outbox.v5", kept);
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v5")).toBe(kept);
  });

  test("text no build wrote is left alone", () => {
    localStorage.setItem("inventory.outbox.v1", "[");
    localStorage.setItem("inventory.outbox.v3", "not json");
    localStorage.setItem("inventory.cart.v1", "{");
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v3")).toBe("not json");
    expect(localStorage.getItem("inventory.outbox.v1")).toBe("[");
    expect(localStorage.getItem("inventory.cart.v1")).toBe("{");
  });
});

describe("removing one", () => {
  test("a save goes from its key, which keeps the rest as it was", () => {
    const kept = {
      build: "2026-11-12T10:00:00.000Z",
      other: true,
      entries: [
        saveOf(),
        saveOf({}, { key: OTHER }),
        saveOf({ state: "saved" }, { key: "c" }),
      ],
    };
    keep("inventory.outbox.v3", kept);
    const [first] = readOlder();
    expect(removeOlder(first as Older)).toBe(true);
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v3") ?? ""),
    ).toEqual({
      ...kept,
      entries: kept.entries.slice(1),
    });
  });

  test("the last to list takes its key with it", () => {
    keep("inventory.outbox.v3", [
      saveOf(),
      saveOf({ state: "saved" }, { key: "c" }),
    ]);
    expect(removeOlder(readOlder()[0] as Older)).toBe(true);
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
  });

  test("one held back under this build's key goes from it, which stays with the rest", () => {
    const build = "2026-11-12T10:00:00.000Z";
    const waiting = saveOf({ build }, { key: OTHER });
    keep("inventory.outbox.v5", {
      build,
      entries: [saveOf({ state: "too_old", build }), waiting],
    });
    const [save] = readOlder();
    expect(removeOlder(save as Older)).toBe(true);
    expect(readOlder()).toEqual([]);
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v5") ?? ""),
    ).toEqual({ build, entries: [waiting] });
    // Its last: the key is still the outbox's.
    keep("inventory.outbox.v5", {
      build,
      entries: [saveOf({ state: "too_old", build })],
    });
    expect(removeOlder(readOlder()[0] as Older)).toBe(true);
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v5") ?? ""),
    ).toEqual({ build, entries: [] });
  });

  test("one gone meanwhile, sent by an older tab, is not removed again", () => {
    keep("inventory.outbox.v3", [saveOf()]);
    const [save] = readOlder();
    keep("inventory.outbox.v3", [saveOf({ state: "saved" })]);
    expect(isListed(save as Older)).toBe(false);
    expect(removeOlder(save as Older)).toBe(false);
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("a cart changed meanwhile, by an older tab, is not removed", () => {
    const cart = {
      ...freshCart(),
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    };
    keep("inventory.cart.v1", cart);
    const [listedCart] = readOlder();
    keep("inventory.cart.v1", { ...cart, toId: 12 });
    expect(removeOlder(listedCart as Older)).toBe(false);
    keep("inventory.cart.v1", cart);
    expect(removeOlder(listedCart as Older)).toBe(true);
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
  });

  test("a browser that will not keep the change says so", () => {
    keep("inventory.outbox.v3", [saveOf(), saveOf({}, { key: OTHER })]);
    expect(removeOlder(readOlder()[0] as Older, refusing("setItem"))).toBe(
      false,
    );
  });
});

describe("a browser that will not let it at storage", () => {
  test("one that will not read lists nothing, and the page still loads", () => {
    keep("inventory.outbox.v1", [saveOf()]);
    keep("inventory.outbox.v3", [saveOf()]);
    const older = new OlderStore(refusing("getItem"));
    expect(older.listed.value).toEqual([]);
    expect(localStorage.getItem("inventory.outbox.v1")).not.toBeNull();
  });

  test("one that will not forget keeps each, and says so", () => {
    // v1's one save is in v3 too, so v1 would go on load.
    keep("inventory.outbox.v1", [saveOf()]);
    keep("inventory.outbox.v3", [
      saveOf({ state: "saved" }),
      saveOf({}, { key: OTHER }),
    ]);
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    });
    const storage = refusing("removeItem");
    const older = new OlderStore(storage);
    expect(localStorage.getItem("inventory.outbox.v1")).not.toBeNull();
    for (const each of older.listed.value) {
      expect(removeOlder(each, storage)).toBe(false);
    }
    expect(older.listed.value).toHaveLength(2);
  });
});

describe("as a cart", () => {
  const only = (save: unknown) => {
    keep("inventory.outbox.v3", [save]);
    return readOlder()[0] as Older;
  };

  test("a batch's lines go back by item: whole packages as packages, the rest loose", () => {
    const { cart, gaps } = toCart(
      only(
        saveOf(
          {},
          {
            lines: [
              { item_id: 100, quantity: "8.000", package_id: 1000 },
              { item_id: 100, quantity: "1" },
              // Not whole boxes of 4, so loose, for the cart to question.
              { item_id: 100, quantity: "2", package_id: 1000 },
              { item_id: 101, quantity: 3.5 },
            ],
          },
        ),
      ),
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

  test("what the cart cannot hold is told, to do again", () => {
    const { cart, gaps } = toCart(
      only(
        saveOf(
          {},
          {
            lines: [
              { item_id: 101, quantity: "5", open_package_key: OTHER },
              { item_id: "cable", quantity: "1" },
            ],
            lengths: [{ open_package_key: OTHER, left: "10" }],
          },
        ),
      ),
      CATALOGUE,
    );
    expect(cart.lines).toEqual([
      { itemId: 101, packages: {}, loose: "", typed: true },
    ]);
    // Each once for the save: the take and the report are two open items.
    expect(gaps).toEqual([
      "Sorry. Could not read 1 previously added item, scan it again.",
      "Sorry. Could not read 2 open items, scan them in again.",
    ]);
  });

  test("lines that cannot be read are counted, and an item gone kept", () => {
    const { cart, gaps } = toCart(
      only(
        saveOf(
          {},
          {
            lines: [
              { item_id: 101, quantity: "lots" },
              { item_id: 101, quantity: 0.30000000000000004 },
              { item_id: 999, quantity: "2", open_package_key: OTHER },
            ],
          },
        ),
      ),
      CATALOGUE,
    );
    expect(cart.lines.map((line) => line.itemId)).toEqual([101, 999]);
    expect(gaps).toEqual([
      "Sorry. Could not read 2 previously added items, scan them again.",
      "Sorry. Could not read 1 open item, scan it in again.",
    ]);
  });

  test("lines that cannot be read even as a list are not counted", () => {
    const { cart, gaps } = toCart(
      only(saveOf({}, { lines: "many" })),
      CATALOGUE,
    );
    expect(cart.lines).toEqual([]);
    expect(gaps).toEqual([
      "Sorry. Could not read previously added items, scan them again.",
    ]);
  });

  test("a package resized since is not counted again in today's size", () => {
    const { cart, gaps } = toCart(
      only(
        saveOf(
          {},
          {
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
          },
        ),
      ),
      CATALOGUE,
    );
    expect(cart.lines).toEqual([
      { itemId: 100, packages: { 1000: "2" }, loose: "", typed: true },
    ]);
    expect(gaps).toEqual([
      "Sorry. The package size has changed since this item was added. Discard and re-add the new package size.",
    ]);
  });

  test("an opening's packages go back as packages to open", () => {
    const { cart } = toCart(
      only(
        saveOf(
          {},
          {
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
                open_package_key: OTHER,
              },
            ],
          },
        ),
      ),
      CATALOGUE,
    );
    expect(cart).toMatchObject({
      kind: "open",
      lines: [{ itemId: 101, packages: { 1001: "2" }, loose: "" }],
    });
  });

  test("a report, or no kind, keeps the cart's; places and names gone from the catalogue are cleared", () => {
    const { cart } = toCart(
      only(
        saveOf({}, { kind: "report", volunteer_id: 99, from_location_id: 98 }),
      ),
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
            [OTHER]: { amount: "", whole: false },
          },
          picked: 4,
        },
        // Nothing said of an open package: no amount, or none readable.
        {
          itemId: 102,
          packages: {},
          loose: "",
          open: { [KEY]: "odd", [OTHER]: { amount: 3 } },
        },
        { itemId: 100, packages: { 1000: 7 }, loose: "1" },
        { packages: {} },
      ],
    });
    const { cart, gaps } = toCart(readOlder()[0] as Older, CATALOGUE);
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
      "Sorry. Could not read 1 previously added item, scan it again.",
      "Sorry. Could not read 1 open item, scan it in again.",
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
      expect(toCart(readOlder()[0] as Older, CATALOGUE).gaps).toEqual([
        "Sorry. Could not read 1 open item, scan it in again.",
      ]);
    },
  );
});

describe("put back without a tap", () => {
  const only = (body = {}) => {
    keep("inventory.outbox.v3", [saveOf({}, body)]);
    return readOlder()[0] as Older;
  };

  test("one that goes back whole does", () => {
    expect(goesBackWhole(only(), CATALOGUE)).toBe(true);
  });

  test.each([
    ["a line unread", { lines: [{ item_id: 101, quantity: "lots" }] }],
    ["its lines unread", { lines: "many" }],
    ["nothing to put back", { lines: [] }],
    ["a report", { used_up: [OTHER] }],
    [
      "a take from an open package",
      { lines: [{ item_id: 101, quantity: "2", open_package_key: OTHER }] },
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
    expect(goesBackWhole(only(body), CATALOGUE)).toBe(false);
  });

  test("one the inventory refused does not, as its reason is said only while it is listed", () => {
    keep("inventory.outbox.v3", [
      saveOf({ state: "refused", note: "Not saved. Line 1: too many." }),
    ]);
    expect(goesBackWhole(readOlder()[0] as Older, CATALOGUE)).toBe(false);
  });

  test("an older cart holding some of a package gone does not; none of one, or a count unread, it does", () => {
    const cartWith = (packages: Record<string, string>) => {
      keep("inventory.cart.v1", {
        ...freshCart(),
        lines: [{ itemId: 100, packages, loose: "", open: {} }],
      });
      return readOlder()[0] as Older;
    };
    expect(goesBackWhole(cartWith({ 1003: "2" }), CATALOGUE)).toBe(false);
    expect(
      goesBackWhole(cartWith({ 1000: "1", 1003: "", 1004: "lots" }), CATALOGUE),
    ).toBe(true);
  });

  test("one with a package retired, some left, does not", () => {
    const retired = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) => ({
        ...item,
        packages: item.packages.map((pack) => ({ ...pack, retired: true })),
      })),
    };
    const body = { lines: [{ item_id: 100, quantity: "4", package_id: 1000 }] };
    expect(goesBackWhole(only(body), CATALOGUE)).toBe(true);
    expect(goesBackWhole(only(body), retired)).toBe(false);
  });

  test("the oldest that goes back whole is next, one of no known time last, and those tried are skipped", () => {
    keep("inventory.outbox.v3", [
      saveOf({ queuedAt: null }, { key: "untimed" }),
      saveOf({ queuedAt: 3 }, { key: "newer" }),
      saveOf({ queuedAt: 1 }, { key: "unread", lines: "many" }),
      saveOf({ queuedAt: 2 }, { key: "older" }),
    ]);
    const listed = readOlder();
    expect(nextWhole(listed, CATALOGUE)?.batch).toBe("older");
    const tried = new Set(
      listed.filter((each) => each.batch !== "untimed").map(olderId),
    );
    expect(nextWhole(listed, CATALOGUE, tried)?.batch).toBe("untimed");
    expect(
      nextWhole(listed, CATALOGUE, new Set(listed.map(olderId))),
    ).toBeUndefined();
  });

  test("one with an item retired, some left, does not", () => {
    const retired = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 101 ? { ...item, retired: true } : item,
      ),
    };
    expect(goesBackWhole(only(), retired)).toBe(false);
  });
});

test("an item retired with none left is named, with its packages, never offered", () => {
  keep("inventory.outbox.v3", [
    saveOf({}, { lines: [{ item_id: 103, quantity: "4", package_id: 1003 }] }),
  ]);
  const older = readOlder()[0] as Older;
  expect(toCart(older, withRetired(CATALOGUE)).cart.lines).toEqual([
    { itemId: 103, packages: { 1003: "2" }, loose: "", typed: true },
  ]);
  expect(
    withRetired(CATALOGUE).items.find((item) => item.id === 103),
  ).toMatchObject({ name: "NanoStation M5", retired: true });
  expect(CATALOGUE.items.some((item) => item.id === 103)).toBe(false);
});

test("the list follows another tab's changes to the older keys", () => {
  keep("inventory.outbox.v3", [saveOf()]);
  const older = new OlderStore();
  const stop = older.listen();
  expect(older.listed.value).toHaveLength(1);
  keep("inventory.outbox.v3", [saveOf({ state: "saved" })]);
  window.dispatchEvent(
    new StorageEvent("storage", { key: "inventory.outbox.v3" }),
  );
  expect(older.listed.value).toEqual([]);
  stop();
});
