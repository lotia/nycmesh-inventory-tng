import { describe, expect, test } from "vitest";
import { freshCart, type Item } from "./cart";
import {
  compared,
  dropPart,
  goesBackWhole,
  LINES_LOST,
  linesSaid,
  lineWords,
  nextWhole,
  type Older,
  OlderStore,
  olderId,
  RESCAN as RESCAN_NOTE,
  RESIZED as RESIZED_NOTE,
  ITEM_RETIRED as RETIRED_NOTE,
  readOlder,
  removeOlder,
  tidyOlder,
  toCart,
  unreadWords,
  withRetired,
} from "./older";
import { KEY_REUSED, noteOf, SCAN_AGAIN_CHANGED } from "./outbox";
import { CATALOGUE } from "./test-catalogue";
import { refusing } from "./test-storage";

/** In Ali's words (inventory-tng-0su), without "Sorry." (inventory-tng-93z). */
const ITEM_RETIRED = "This item has been retired.";
const RESCAN = "Can't add these items. Please rescan.";
const RESIZED =
  "The package size has changed since this item was added. Discard and re-add the new package size.";

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

/** The only save kept, a move of cable but for `body` and `fields`, as
 * read. */
function only(body = {}, fields = {}): Older {
  keep("inventory.outbox.v3", [saveOf(fields, body)]);
  return readOlder()[0] as Older;
}

/** An older cart of item 100 holding `packages`, as read. */
function cartHolding(packages: Record<string, string>): Older {
  keep("inventory.cart.v1", {
    ...freshCart(),
    lines: [{ itemId: 100, packages, loose: "" }],
  });
  return readOlder()[0] as Older;
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
      {
        key: "inventory.outbox.v3",
        batch: OTHER,
        refused: { code: "refused", note: { bold: ["Not saved."], plain: [] } },
      },
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
        batchKey: KEY,
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
        reports: { usedUp: 1, lengths: 0 },
      },
    ]);
  });

  test("of v1 to v3, only the newest held is read, as each took over the one before", () => {
    keep("inventory.outbox.v1", [saveOf({}, { key: OTHER })]);
    keep("inventory.outbox.v2", [saveOf()]);
    expect(listed().map((each) => each.key)).toEqual(["inventory.outbox.v2"]);
  });

  test("a save held back as too old is listed from this build's key, whose rest are the outbox's", () => {
    const build = "2026-11-12T10:00:00.000Z";
    keep("inventory.outbox.v4", {
      build,
      entries: [
        saveOf({ state: "too_old", build }, { key: OTHER }),
        saveOf({ build }, { key: "c" }),
        saveOf({ state: "refused", note: "Not saved.", build }, { key: "d" }),
        saveOf({ state: "saved", build }, { key: "e" }),
      ],
    });
    expect(listed()).toEqual([
      { key: "inventory.outbox.v4", batch: OTHER, refused: null },
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
    keep("inventory.outbox.v4", {
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
    localStorage.setItem("inventory.outbox.v4", kept);
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v4")).toBe(kept);
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
    expect(removeOlder(first as Older)).toBe("done");
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
    expect(removeOlder(readOlder()[0] as Older)).toBe("done");
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
  });

  test("one held back under this build's key goes from it, which stays with the rest", () => {
    const build = "2026-11-12T10:00:00.000Z";
    const waiting = saveOf({ build }, { key: OTHER });
    keep("inventory.outbox.v4", {
      build,
      entries: [saveOf({ state: "too_old", build }), waiting],
    });
    const [save] = readOlder();
    expect(removeOlder(save as Older)).toBe("done");
    expect(readOlder()).toEqual([]);
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v4") ?? ""),
    ).toEqual({ build, entries: [waiting] });
    // Its last: the key is still the outbox's.
    keep("inventory.outbox.v4", {
      build,
      entries: [saveOf({ state: "too_old", build })],
    });
    expect(removeOlder(readOlder()[0] as Older)).toBe("done");
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v4") ?? ""),
    ).toEqual({ build, entries: [] });
  });

  test("one gone meanwhile, sent by an older tab, is not removed again", () => {
    keep("inventory.outbox.v3", [saveOf()]);
    const [save] = readOlder();
    keep("inventory.outbox.v3", [saveOf({ state: "saved" })]);
    expect(compared(save as Older)).toBe("gone");
    expect(removeOlder(save as Older)).toBe("gone");
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("a save changed meanwhile, by an older tab, is still the one discarded, and goes", () => {
    keep("inventory.outbox.v3", [saveOf()]);
    const [save] = readOlder();
    keep("inventory.outbox.v3", [saveOf({ state: "refused", note: "No." })]);
    expect(compared(save as Older)).toBe("changed");
    expect(removeOlder(save as Older)).toBe("done");
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
  });

  test("a cart changed meanwhile, by an older tab, is not removed", () => {
    const cart = {
      ...freshCart(),
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    };
    keep("inventory.cart.v1", cart);
    const [listedCart] = readOlder();
    keep("inventory.cart.v1", { ...cart, toId: 12 });
    expect(compared(listedCart as Older)).toBe("changed");
    expect(removeOlder(listedCart as Older)).toBe("changed");
    keep("inventory.cart.v1", cart);
    expect(compared(listedCart as Older)).toBe("unchanged");
    expect(removeOlder(listedCart as Older)).toBe("done");
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
  });

  test("a browser that will not keep the change says so", () => {
    keep("inventory.outbox.v3", [saveOf(), saveOf({}, { key: OTHER })]);
    expect(removeOlder(readOlder()[0] as Older, refusing("setItem"))).toBe(
      "not_kept",
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
      expect(removeOlder(each, storage)).toBe("not_kept");
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

  test("a save's cart keeps its batch key, to be sent under; a cart's has none", () => {
    expect(toCart(only(saveOf()), CATALOGUE).cart.batchKey).toBe(KEY);
    // A key no build made is not sent: the outbox gives a new one.
    expect(
      toCart(only(saveOf({}, { key: "c" })), CATALOGUE).cart,
    ).not.toHaveProperty("batchKey");
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    });
    const cart = readOlder().find((each) => each.batch === null) as Older;
    expect(toCart(cart, CATALOGUE).cart).not.toHaveProperty("batchKey");
  });

  test("a cart a save filled keeps that save's key, to be sent under", () => {
    // As this build's cart (v2) keeps it, once a newer build reads it.
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
      batchKey: KEY,
    });
    expect(toCart(readOlder()[0] as Older, CATALOGUE).cart.batchKey).toBe(KEY);
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
      const refused = only(saveOf({ state: "refused", ...why }));
      expect(toCart(refused, CATALOGUE).cart).not.toHaveProperty("batchKey");
      // Refused for anything else, the key never took it: kept.
      const invalid = only(saveOf({ state: "refused", note: "Not saved." }));
      expect(toCart(invalid, CATALOGUE).cart.batchKey).toBe(KEY);
    },
  );

  test("why one was refused is read as a code, and said as kept, or from its code", () => {
    const refusedFor = (why: object) =>
      only(saveOf({ state: "refused", ...why })).refused;
    // Main's words: its key's, said as kept.
    const mainKey =
      "Not saved: its key was already used for a different batch. Please report this.";
    expect(refusedFor({ note: mainKey })).toEqual({
      code: "key_reused",
      note: { bold: [mainKey] },
    });
    // Main's refusal: "Not saved." in bold, each line or report it names
    // in the page's words, why in the server's.
    expect(
      refusedFor({
        note: "Not saved. Line 1: no. Open package said used up, 2: gone",
      }),
    ).toEqual({
      code: "refused",
      note: {
        bold: ["Not saved."],
        plain: [
          " Line 1: ",
          { quoted: "no." },
          " Open package said used up, 2: ",
          { quoted: "gone" },
        ],
      },
    });
    // Other words, bold whole; none, nothing.
    expect(refusedFor({ note: "No." })).toEqual({
      code: "refused",
      note: { bold: ["No."] },
    });
    expect(refusedFor({ note: null })).toEqual({
      code: "refused",
      note: { bold: [] },
    });
    // A code, in this build's words.
    expect(refusedFor({ reason: { code: "key_reused" } })).toEqual({
      code: "key_reused",
      note: noteOf({ code: "key_reused" }),
    });
    expect(noteOf({ code: "key_reused" })).toEqual({ bold: [KEY_REUSED] });
    expect(
      refusedFor({ reason: { code: "recorded_elsewhere" } })?.note,
    ).toEqual({ bold: [SCAN_AGAIN_CHANGED] });
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
    // An unread line's item goes back, to count again; a take does not.
    expect(cart.lines.map((line) => line.itemId)).toEqual([101]);
    expect(gaps).toEqual([
      "Could not read 2 previously added items, scan them again.",
      `An item no longer in the catalogue: ${RESCAN}`,
    ]);
  });

  test("a package no longer offered is neither counted loose nor dropped, but told", () => {
    const { cart, gaps } = toCart(
      only(
        saveOf(
          {},
          {
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
          },
        ),
      ),
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
    const { cart, gaps } = toCart(readOlder()[0] as Older, CATALOGUE);
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
      return toCart(readOlder()[0] as Older, CATALOGUE);
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
    expect(goesBackWhole(readOlder()[0] as Older, CATALOGUE)).toBe(false);
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
      only(
        saveOf(
          {},
          { lines: [{ item_id: 100, quantity: "8", package_id: 1000 }] },
        ),
      ),
      PACKAGES_RETIRED,
    );
    expect(cart.lines).toEqual([
      { itemId: 100, packages: { 1000: "2" }, loose: "", typed: true },
    ]);
    expect(gaps).toEqual([]);
  });

  test("lines that cannot be read even as a list are not counted", () => {
    const { cart, gaps } = toCart(
      only(saveOf({}, { lines: "many" })),
      CATALOGUE,
    );
    expect(cart.lines).toEqual([]);
    expect(gaps).toEqual([
      "Could not read previously added items, scan them again.",
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
    expect(gaps).toEqual([`LiteBeam 5AC: ${RESIZED}`]);
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
      const { cart, gaps } = toCart(readOlder()[0] as Older, CATALOGUE);
      expect(gaps).toEqual([`Cat6 cable: ${RESCAN}`]);
      // All it held left out, so not put back as an empty line.
      expect(cart.lines).toEqual([]);
    },
  );
});

describe("line by line", () => {
  const stored = (key = "inventory.outbox.v3") =>
    JSON.parse(localStorage.getItem(key) ?? "null");

  /** A save of a box retired with none left, cable taken from an open
   * package, a box resized since, two boxes still offered, and a report. */
  const mixed = () =>
    only({
      lines: [
        { item_id: 100, quantity: "4", package_id: 1004 },
        { item_id: 101, quantity: "12", open_package_key: OTHER },
        { item_id: 100, quantity: "4", package_id: 1000, package_size: 2 },
        { item_id: 100, quantity: "8", package_id: 1000 },
      ],
      used_up: [OTHER],
    });

  test("each line is listed in the order kept, what cannot go back with why", () => {
    const { parts, lost } = toCart(mixed(), CATALOGUE);
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
      "An item no longer in the catalogue",
    );
  });

  test("a batch the inventory refused is said line by line in the same words, and one not read, as none", () => {
    const refused = saveOf(
      { state: "refused", note: "Not recorded. Line 1: no." },
      {
        lines: [
          { item_id: 100, quantity: "4", package_id: 1004 },
          { item_id: 101, quantity: "12", open_package_key: OTHER },
          { item_id: 100, quantity: "8", package_id: 1000, package_size: 4 },
          { item_id: 999, quantity: "5" },
          { item_id: "unreadable" },
        ],
        used_up: [OTHER],
      },
    );
    // Numbered by item, as a 422 names its lines; one gone, with how much
    // (Ali, inventory-tng-93z).
    expect(linesSaid(refused, CATALOGUE)).toEqual([
      "1. LiteBeam 5AC: 4",
      "2. Cat6 cable: 12 m from an open package",
      "1. LiteBeam 5AC: 2 × Box",
      "3. An item no longer in the catalogue: 5",
      "Open package said used up",
    ]);
    expect(linesSaid({ body: "lost" }, CATALOGUE)).toEqual([]);
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
          open: { [OTHER]: { now: "9" } },
        },
      ],
    });
    const { cart, parts } = toCart(readOlder()[0] as Older, CATALOGUE);
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
    const save = only({
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
      only({
        lines: [
          { item_id: 103, quantity: "2", open_package_key: OTHER },
          { item_id: 101, quantity: "3", open_package_key: OTHER },
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
    expect([
      RETIRED_NOTE,
      RESCAN_NOTE,
      RESIZED_NOTE,
      LINES_LOST,
      unreadWords(1),
      unreadWords(2),
    ]).toEqual([
      ITEM_RETIRED,
      RESCAN,
      RESIZED,
      "Could not read previously added items, scan them again.",
      "Could not read 1 previously added item, scan it again.",
      "Could not read 2 previously added items, scan them again.",
    ]);
  });

  test.each([
    ["a package no longer offered", { package_id: 1004 }],
    ["a package resized since", { package_id: 1000, package_size: 2 }],
    ["a take from an open package", { open_package_key: OTHER }],
  ])("one holding only %s has nothing to put back", (_name, line) => {
    const { cart, parts } = toCart(
      only({ lines: [{ item_id: 100, quantity: "4", ...line }] }),
      CATALOGUE,
    );
    expect(cart.lines).toEqual([]);
    expect(parts).toHaveLength(1);
  });

  test("a line discarded goes alone, the save keeping its key and the rest as they were", () => {
    const older = mixed();
    const { parts } = toCart(older, CATALOGUE);
    const before = stored();
    expect(dropPart(older, parts[0]?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored()).toEqual([
      {
        ...before[0],
        body: { ...before[0].body, lines: before[0].body.lines.slice(1) },
      },
    ]);
    // The report too, by its place.
    const again = readOlder()[0] as Older;
    const report = toCart(again, CATALOGUE).parts.at(-1);
    expect(dropPart(again, report?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored()[0].body.used_up).toEqual([]);
    expect(stored()[0].body.key).toBe(KEY);
  });

  test("among others, kept as v4 keeps them, only its line goes; and a report of a save whose lines are lost", () => {
    const build = "2026-11-12T10:00:00.000Z";
    const other = saveOf({ build }, { key: OTHER });
    keep("inventory.outbox.v3", {
      build,
      entries: [
        other,
        saveOf(
          {},
          {
            lines: [
              { item_id: 100, quantity: "4", package_id: 1004 },
              { item_id: 101, quantity: "2" },
            ],
          },
        ),
      ],
    });
    const older = readOlder()[1] as Older;
    const [gone] = toCart(older, CATALOGUE).parts;
    expect(dropPart(older, gone?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored()).toEqual({
      build,
      entries: [
        other,
        saveOf({}, { lines: [{ item_id: 101, quantity: "2" }] }),
      ],
    });
    keep("inventory.outbox.v3", [
      saveOf({}, { lines: "many", used_up: [KEY, OTHER] }),
    ]);
    const lost = readOlder()[0] as Older;
    const [report] = toCart(lost, CATALOGUE).parts;
    expect(dropPart(lost, report?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored()[0].body).toMatchObject({ lines: "many", used_up: [OTHER] });
  });

  test("of an older cart's line, what it said of one open package goes, the rest kept", () => {
    const line = {
      itemId: 101,
      packages: { 1001: "1" },
      loose: "",
      open: { [KEY]: { amount: "3" }, [OTHER]: { usedUp: true } },
    };
    keep("inventory.cart.v1", { ...freshCart(), lines: [line] });
    const older = readOlder()[0] as Older;
    const taken = toCart(older, CATALOGUE).parts.find(
      (part) => part.words === "Cat6 cable: 3 m from an open package",
    );
    expect(dropPart(older, taken?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored("inventory.cart.v1").lines).toEqual([
      { ...line, open: { [OTHER]: { usedUp: true } } },
    ]);
  });

  test("an item's lines that go back are discarded together", () => {
    const older = only({
      lines: [
        { item_id: 100, quantity: "4", package_id: 1000 },
        { item_id: 101, quantity: "2" },
        { item_id: 100, quantity: "1" },
      ],
    });
    const { parts } = toCart(older, CATALOGUE);
    expect(parts.map((part) => part.words)).toEqual([
      "LiteBeam 5AC: 1 × Box, 1 loose",
      "Cat6 cable: 2 m",
    ]);
    expect(dropPart(older, parts[0]?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored()[0].body.lines).toEqual([{ item_id: 101, quantity: "2" }]);
  });

  test("of an older cart's line, only that part goes, and the line once it holds nothing", () => {
    const line = {
      itemId: 100,
      packages: { 1000: "1", 1004: "2" },
      loose: "3",
      picked: 4,
    };
    keep("inventory.cart.v1", { ...freshCart(), lines: [line] });
    const listedCart = readOlder()[0] as Older;
    const [back, gone] = toCart(listedCart, CATALOGUE).parts;
    expect(gone?.note).toBe(ITEM_RETIRED);
    expect(dropPart(listedCart, gone?.drop ?? [], CATALOGUE)).toBe("done");
    // No field added that it never had: an older build still reads it.
    expect(stored("inventory.cart.v1").lines).toEqual([
      { ...line, packages: { 1000: "1" } },
    ]);
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [line, { itemId: 101, packages: {}, loose: "1" }],
    });
    const two = readOlder()[0] as Older;
    const parts = toCart(two, CATALOGUE).parts;
    expect(back?.words).toBe("LiteBeam 5AC: 1 × Box, 3 loose");
    expect(dropPart(two, parts[0]?.drop ?? [], CATALOGUE)).toBe("done");
    // What it held besides was the package gone, still to discard.
    expect(stored("inventory.cart.v1").lines).toEqual([
      { ...line, packages: { 1004: "2" }, loose: "" },
      { itemId: 101, packages: {}, loose: "1" },
    ]);
    const left = readOlder()[0] as Older;
    expect(
      dropPart(left, toCart(left, CATALOGUE).parts[0]?.drop ?? [], CATALOGUE),
    ).toBe("done");
    expect(stored("inventory.cart.v1").lines).toEqual([
      { itemId: 101, packages: {}, loose: "1" },
    ]);
  });

  test("the last line listed discarded takes the save, or the cart, with it", () => {
    const older = only({
      lines: [{ item_id: 100, quantity: "4", package_id: 1004 }],
    });
    const { parts } = toCart(older, CATALOGUE);
    expect(parts).toHaveLength(1);
    expect(dropPart(older, parts[0]?.drop ?? [], CATALOGUE)).toBe("done");
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: { 1004: "2" }, loose: "" }],
    });
    const cart = readOlder()[0] as Older;
    expect(
      dropPart(cart, toCart(cart, CATALOGUE).parts[0]?.drop ?? [], CATALOGUE),
    ).toBe("done");
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
  });

  test.each([
    ["could not be read", [{ item_id: 100, quantity: "lots" }]],
    ["could not be read at all", [{ item_id: "lots" }]],
  ])(
    "one with lines that %s stays, saying so, once its last line listed is discarded",
    (_name, unread) => {
      const older = only({
        lines: [{ item_id: 100, quantity: "4", package_id: 1004 }, ...unread],
      });
      const [gone] = toCart(older, CATALOGUE).parts;
      expect(dropPart(older, gone?.drop ?? [], CATALOGUE)).toBe("done");
      expect(stored()[0].body.lines).toEqual(unread);
      const left = readOlder()[0] as Older;
      expect(toCart(left, CATALOGUE)).toMatchObject({
        lost: unreadWords(1),
        parts: [],
      });
    },
  );

  test("one whose lines are lost stays, saying so, once its last report is discarded", () => {
    keep("inventory.outbox.v3", [
      saveOf({}, { lines: "many", used_up: [KEY] }),
    ]);
    const older = readOlder()[0] as Older;
    const [report] = toCart(older, CATALOGUE).parts;
    expect(dropPart(older, report?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored()[0].body).toMatchObject({ lines: "many", used_up: [] });
  });

  test("an item retired since is listed as named, and goes back once discarded", () => {
    const older = only({
      lines: [
        { item_id: 103, quantity: "4", package_id: 1003 },
        { item_id: 100, quantity: "4", package_id: 1000 },
      ],
    });
    const named = withRetired(CATALOGUE);
    const [retired] = toCart(older, named).parts;
    expect(retired?.words).toBe("NanoStation M5: 2 × Pair");
    expect(dropPart(older, retired?.drop ?? [], CATALOGUE)).toBe("done");
    expect(goesBackWhole(readOlder()[0] as Older, CATALOGUE)).toBe(true);
  });

  test("one changed meanwhile, or a browser that will not keep the change, keeps it", () => {
    const older = mixed();
    const { parts } = toCart(older, CATALOGUE);
    const drop = parts[0]?.drop ?? [];
    expect(dropPart(older, drop, CATALOGUE, refusing("setItem"))).toBe(
      "not_kept",
    );
    keep("inventory.outbox.v3", [saveOf({ state: "refused", note: "No." })]);
    const before = stored();
    expect(compared(older)).toBe("changed");
    expect(dropPart(older, drop, CATALOGUE)).toBe("changed");
    expect(stored()).toEqual(before);
  });

  test("the store lists again after a line goes", () => {
    mixed();
    const store = new OlderStore();
    const [older] = store.listed.value;
    const { parts } = toCart(older as Older, CATALOGUE);
    expect(
      store.dropPart(older as Older, parts[0]?.drop ?? [], CATALOGUE),
    ).toBe("done");
    expect(store.listed.value[0]?.lines).toHaveLength(3);
  });
});

describe("put back without a tap", () => {
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
    const refused = only(
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

  test.each([
    ["a package retired, some left", retirePackages],
    [
      "an item retired, some left",
      (item: Item) => ({ ...item, retired: true }),
    ],
  ])("one with %s does not", (_name, retire) => {
    const retired = { ...CATALOGUE, items: CATALOGUE.items.map(retire) };
    const body = { lines: [{ item_id: 100, quantity: "4", package_id: 1000 }] };
    expect(goesBackWhole(only(body), CATALOGUE)).toBe(true);
    expect(goesBackWhole(only(body), retired)).toBe(false);
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
