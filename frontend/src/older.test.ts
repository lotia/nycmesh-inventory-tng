import { describe, expect, test } from "vitest";
import { freshCart } from "./cart";
import {
  isListed,
  type Older,
  OlderStore,
  readOlder,
  removeOlder,
  tidyOlder,
  toCart,
} from "./older";
import { CATALOGUE } from "./test-catalogue";

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
            open_package_key: null,
          },
          null,
          {
            item_id: 101,
            quantity: "1.5",
            package_id: null,
            open_package_key: null,
          },
        ],
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
    // What a later build reads, once v4 is older than its own.
    keep("inventory.outbox.v3", {
      build: "2026-11-12T10:00:00.000Z",
      entries: [saveOf()],
    });
    expect(listed()).toEqual([
      { key: "inventory.outbox.v3", batch: KEY, refused: null },
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
    keep("inventory.outbox.v4", { build: "x", entries: [saveOf()] });
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
    const full = {
      getItem: (key: string) => localStorage.getItem(key),
      setItem: () => {
        throw new DOMException("full", "QuotaExceededError");
      },
      removeItem: (key: string) => localStorage.removeItem(key),
    } as unknown as Storage;
    expect(removeOlder(readOlder()[0] as Older, full)).toBe(false);
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
    expect(gaps).toEqual([
      "Cat6 cable: 5 m was taken from an open package. Take it again.",
      "A line could not be read: add it again if it is missing.",
      "It also said an open package was used up, or what was on one: say so again on the open package.",
    ]);
  });

  test("lines that cannot be read are counted; an item gone is named so", () => {
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
      "An item no longer in the catalogue: 2 was taken from an open package. Take it again.",
      "2 lines could not be read: add them again if they are missing.",
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
      "Cat6 cable: take what was taken from an open package again.",
      "A line could not be read: add it again if it is missing.",
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
        "Cat6 cable: take what was taken from an open package again.",
      ]);
    },
  );
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
