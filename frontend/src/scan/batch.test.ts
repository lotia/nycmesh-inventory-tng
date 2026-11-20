import { describe, expect, test } from "vitest";
import { messages as pseudo } from "../locales/pseudo.po";
import { say, speak } from "../shared/i18n";
import { cart, shown } from "../testing/cart";
import { CATALOGUE, cable, openAt } from "../testing/catalogue";
import {
  ADD_SOMETHING,
  batchWords,
  INSTALL_DIGITS,
  INSTALL_RANGE,
  ITEM_GONE,
  ITEMS,
  MOVE,
  PICK_FROM,
  PICK_TO,
  PICK_YOUR_NAME,
  SAME_PLACE,
  TOO_MUCH_FOR_ONE,
  toBatch,
} from "./batch";

/** A batch with its summary in words, as the outbox says it now. */
function worded(batch: ReturnType<typeof toBatch>) {
  return "summary" in batch
    ? { draft: batch.draft, what: batchWords(batch.draft, batch.summary) }
    : batch;
}

describe("turning a cart into a batch", () => {
  test("a move sends both places, decimal quantities, and packages sealed", () => {
    const full = {
      ...cart(),
      lines: [
        { itemId: 100, packages: { 1000: "1" }, loose: "2" },
        { itemId: 101, packages: {}, loose: "0.25" },
      ],
    };
    expect(worded(toBatch(full, CATALOGUE))).toEqual({
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

  test("a batch keeps what its words are made from, its places named as they are now, and is worded as it is shown (inventory-tng-ar8)", () => {
    const lines = [{ itemId: 100, packages: {}, loose: "1" }];
    const batch = toBatch(cart({ lines }), CATALOGUE);
    if (!("summary" in batch)) throw new Error("the cart is recordable");
    expect(batch.summary).toEqual({ from: "Hub", to: "Van" });
    expect(batchWords(batch.draft, batch.summary)).toBe(
      "Move: 1 item from Hub to Van",
    );
    // The places as named when queued, whatever the catalogue says now.
    const renamed = { ...batch.summary, from: "Old hub" };
    expect(batchWords(batch.draft, renamed)).toBe(
      "Move: 1 item from Old hub to Van",
    );
    speak("pseudo", pseudo);
    const words = batchWords(batch.draft, batch.summary);
    expect(words).toBe(
      say(MOVE, { items: say(ITEMS, { count: 1 }), from: "Hub", to: "Van" }),
    );
    expect(words).not.toContain("Move");
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
    expect(shown(toBatch(many, { ...CATALOGUE, items }))).toEqual({
      problems: [say(TOO_MUCH_FOR_ONE)],
    });
  });

  test("a line that can't be counted is named", () => {
    const lines = [{ itemId: 100, packages: {}, loose: "1.5" }];
    expect(shown(toBatch(cart({ lines }), CATALOGUE))).toEqual({
      problems: ["[LiteBeam 5AC]: Counted in whole units."],
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
    expect(worded(opened)).toMatchObject({
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

  test("an opening's keys are made from its cart's, so the same cart is the same batch (inventory-tng-e2d)", () => {
    const lines = [{ itemId: 101, packages: { 1001: "2" }, loose: "" }];
    const keysOf = (batchKey: string) => {
      const opened = toBatch(
        cart({ kind: "open", lines, fromId: 10, batchKey }),
        CATALOGUE,
      );
      return "draft" in opened
        ? opened.draft.lines.map((line) => line.open_package_key)
        : [];
    };
    const key = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
    const keys = keysOf(key);
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatch(/^[0-9a-f-]{36}$/);
    expect(keys[0]).not.toBe(keys[1]);
    expect(keysOf(key)).toEqual(keys);
    expect(keysOf("5d2e8f10-3c4b-4a5d-9e6f-7a8b9c0d1e2f")).not.toContain(
      keys[0],
    );
  });

  test("an opening counts only the items it opens", () => {
    const lines = [
      { itemId: 101, packages: { 1001: "1" }, loose: "" },
      { itemId: 100, packages: {}, loose: "" },
    ];
    expect(
      worded(toBatch(cart({ kind: "open", lines, fromId: 10 }), CATALOGUE)),
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
    expect(shown(receipt)).toEqual({
      problems: ["[Cat6 cable]: Nothing is taken from an open package here."],
    });
    const bad = [
      {
        itemId: 101,
        packages: {},
        loose: "",
        open: { [key]: { amount: "x", whole: false } },
      },
    ];
    expect(shown(toBatch(cart({ lines: bad }), CATALOGUE))).toEqual({
      problems: [
        "[Cat6 cable]: Enter how much from the open package, up to 3 decimals.",
      ],
    });
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
    expect(worded(alone)).toEqual({
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
    expect(
      worded(toBatch(cart({ lines: line("120") }), CATALOGUE)),
    ).toMatchObject({
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
      worded(
        toBatch(cart({ lines: line({ amount: "20", now: "250" }) }), CATALOGUE),
      ),
    ).toMatchObject({
      draft: {
        kind: "move",
        lengths: [{ open_package_key: key, left: "250" }],
      },
      what: "Move: 1 item from Hub to Van (what is on 1 open package said)",
    });
    expect(
      worded(toBatch(cart({ lines: line({ now: "12.5" }) }), CATALOGUE)),
    ).toEqual({
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
      worded(
        toBatch(cart({ lines: line({ now: "0", usedUp: true }) }), CATALOGUE),
      ),
    ).toMatchObject({
      what: "Used up: 1 open package at Hub (what is on 1 open package said)",
    });
    // Blank is no report; not a number is a problem.
    expect(
      shown(toBatch(cart({ lines: line({ now: " " }) }), CATALOGUE)),
    ).toEqual({
      problems: [say(ADD_SOMETHING)],
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
    expect(shown(toBatch(cart({ lines: radioLine }), CATALOGUE))).toEqual({
      problems: [
        "[LiteBeam 5AC]: enter what is on the open package, in whole units, or leave it blank.",
      ],
    });
    expect(
      shown(toBatch(cart({ lines: line({ now: "lots" }) }), CATALOGUE)),
    ).toEqual({
      problems: [
        "[Cat6 cable]: enter what is on the open package, up to 3 decimals, or leave it blank.",
      ],
    });
    // More than the server takes: said here, not refused with the cut.
    expect(
      shown(toBatch(cart({ lines: line({ now: "1000000000" }) }), CATALOGUE)),
    ).toEqual({
      problems: ["[Cat6 cable]: That is more than the inventory can count."],
    });
  });

  test("what is taken from an open package no longer offered is not sent", () => {
    const offeredKey = "11111111-1111-4111-8111-111111111111";
    const gone = "22222222-2222-4222-8222-222222222222";
    const lines = [
      {
        itemId: 101,
        packages: {},
        loose: "",
        open: {
          [offeredKey]: { amount: "5", whole: false },
          [gone]: { amount: "20", whole: false },
        },
      },
    ];
    const here = openAt(5, 120_000, { key: offeredKey });
    // Offered elsewhere is not offered here.
    const elsewhere = { ...here, key: gone, location_id: 12 };
    const batch = toBatch(cart({ lines }), CATALOGUE, [here, elsewhere]);
    expect("draft" in batch && batch.draft.lines).toEqual([
      { item_id: 101, quantity: "5", open_package_key: offeredKey },
    ]);
    // A cut of all it shows says it is used up, unless the volunteer
    // said not.
    const line = { itemId: 101, packages: {}, loose: "" };
    const all = [
      { ...line, open: { [offeredKey]: { amount: "120", whole: false } } },
    ];
    expect(toBatch(cart({ lines: all }), CATALOGUE, [here])).toMatchObject({
      draft: { used_up: [offeredKey] },
    });
    const kept = [
      {
        ...line,
        open: { [offeredKey]: { amount: "120", whole: false, usedUp: false } },
      },
    ];
    const notSaid = toBatch(cart({ lines: kept }), CATALOGUE, [here]);
    expect("draft" in notSaid && notSaid.draft.used_up).toBe(undefined);
  });

  test("an opening opens whole packages only", () => {
    const lines = [{ itemId: 101, packages: { 1001: "1" }, loose: "3" }];
    expect(
      shown(toBatch(cart({ kind: "open", lines, fromId: 10 }), CATALOGUE)),
    ).toEqual({
      problems: ["[Cat6 cable]: Open whole packages: leave loose empty."],
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
    expect(worded(receipt)).toMatchObject({ what: "Receipt: 1 item to Van" });
    expect("draft" in receipt && receipt.draft.from_location_id).toBe(
      undefined,
    );
    const install = toBatch(
      cart({ kind: "install", installNumber: " 4242 ", lines, fromId: 11 }),
      CATALOGUE,
    );
    expect(worded(install)).toMatchObject({
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
    expect(shown(toBatch(cart({ kind: "receipt", lines }), retired))).toEqual({
      problems: [
        "[LiteBeam 5AC]: retired: taking only, so it can't be received.",
      ],
    });
    expect(worded(toBatch(cart({ lines }), retired))).toMatchObject({
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
    expect(
      shown(toBatch(cart({ kind: "receipt", lines: boxed }), retired)),
    ).toEqual({
      problems: [
        "[LiteBeam 5AC], [Box]: retired: taking only, so it can't be received.",
      ],
    });
    // A count that isn't one is said as such, not as retired.
    const unreadable = [{ itemId: 100, packages: { 1000: "x" }, loose: "" }];
    expect(
      shown(toBatch(cart({ kind: "receipt", lines: unreadable }), retired)),
    ).toEqual({ problems: ["[LiteBeam 5AC]: Count whole packages: [Box]."] });
    // And what else is wrong with the line, at once.
    const typo = [{ itemId: 100, packages: { 1000: "1" }, loose: "x" }];
    expect(
      shown(toBatch(cart({ kind: "receipt", lines: typo }), retired)),
    ).toEqual({
      problems: [
        "[LiteBeam 5AC], [Box]: retired: taking only, so it can't be received.",
        "[LiteBeam 5AC]: Enter the loose amount in each, up to 3 decimals.",
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
      worded(toBatch(cart({ kind: "receipt", lines: loose }), retired)),
    ).toMatchObject({ what: "Receipt: 1 item to Van" });
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
    expect(shown(toBatch(empty, CATALOGUE))).toEqual({
      problems: [say(PICK_YOUR_NAME), say(PICK_FROM), say(PICK_TO)],
    });
    expect(shown(toBatch(cart(), CATALOGUE))).toEqual({
      problems: [say(ADD_SOMETHING)],
    });
    const same = cart({
      toId: 10,
      lines: [{ itemId: 100, packages: {}, loose: "1" }],
    });
    expect(shown(toBatch(same, CATALOGUE))).toEqual({
      problems: [say(SAME_PLACE)],
    });
    const bad = cart({
      kind: "install",
      installNumber: "12a",
      lines: [{ itemId: 999, packages: {}, loose: "1" }],
    });
    expect(shown(toBatch(bad, CATALOGUE))).toEqual({
      problems: [say(INSTALL_DIGITS), say(ITEM_GONE)],
    });
  });
});

describe("what has changed since the cart was filled", () => {
  test("a retired name or place counts as none picked", () => {
    const lines = [{ itemId: 100, packages: {}, loose: "1" }];
    expect(
      shown(toBatch(cart({ volunteerId: 99, toId: 98, lines }), CATALOGUE)),
    ).toEqual({
      problems: [say(PICK_YOUR_NAME), say(PICK_TO)],
    });
  });
});

describe("the server's limits", () => {
  const lines = [{ itemId: 100, packages: {}, loose: "1" }];
  test.each(["0", "2147483648"])(
    "install number %s is out of range",
    (number) => {
      const install = cart({ kind: "install", installNumber: number, lines });
      expect(shown(toBatch(install, CATALOGUE))).toEqual({
        problems: [say(INSTALL_RANGE)],
      });
    },
  );

  test("a quantity too big to count is caught before saving", () => {
    const huge = cart({
      lines: [{ itemId: 101, packages: {}, loose: "1000000000" }],
    });
    expect(shown(toBatch(huge, CATALOGUE))).toEqual({
      problems: ["[Cat6 cable]: That is more than the inventory can count."],
    });
  });
});
