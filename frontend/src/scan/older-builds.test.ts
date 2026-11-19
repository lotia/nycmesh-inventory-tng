import { describe, expect, test } from "vitest";
import { say } from "../shared/i18n";
import { KEY_REUSED, noteOf, SOME_NOT_RECORDED } from "../shared/outbox-notes";
import { CATALOGUE } from "../testing/catalogue";
import {
  KEY,
  keep,
  mixedOlder,
  OTHER_KEY,
  olderMove,
  onlyOlder,
} from "../testing/saves";
import { refusing } from "../testing/storage";
import { freshCart } from "./cart";
import {
  compared,
  dropPart,
  linesSaid,
  nextWhole,
  type Older,
  OlderStore,
  olderId,
  readOlder,
  removeOlder,
  tidyOlder,
} from "./older-builds";
import {
  goesBackWhole,
  ITEM_RETIRED as ITEM_RETIRED_PHRASE,
  toCart,
  unreadWords,
  withRetired,
} from "./older-to-cart";

/** In Ali's words (inventory-tng-0su), without "Sorry." (inventory-tng-93z). */
const ITEM_RETIRED = say(ITEM_RETIRED_PHRASE);

const listed = () =>
  readOlder().map(({ key, batch, refused }) => ({ key, batch, refused }));

describe("reading what older builds left", () => {
  test("saves waiting or refused are listed; saved ones are not", () => {
    keep("inventory.outbox.v3", [
      olderMove(),
      olderMove({ state: "refused", note: "Not saved." }, { key: OTHER_KEY }),
      olderMove({ state: "saved" }, { key: "c" }),
    ]);
    expect(listed()).toEqual([
      { key: "inventory.outbox.v3", batch: KEY, refused: null },
      {
        key: "inventory.outbox.v3",
        batch: OTHER_KEY,
        refused: { code: "refused", note: { bold: ["Not saved."], plain: [] } },
      },
    ]);
  });

  test("only the parts every version kept are read, and anything else is tolerated", () => {
    keep("inventory.outbox.v3", [
      olderMove(
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
          used_up: [OTHER_KEY],
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
    keep("inventory.outbox.v1", [olderMove({}, { key: OTHER_KEY })]);
    keep("inventory.outbox.v2", [olderMove()]);
    expect(listed().map((each) => each.key)).toEqual(["inventory.outbox.v2"]);
  });

  test("v4's saves, kept with their summaries in words, are listed as an older build's (inventory-tng-ar8)", () => {
    keep("inventory.outbox.v4", {
      build: "2026-11-12T10:00:00.000Z",
      entries: [olderMove({}, { key: OTHER_KEY })],
    });
    expect(listed()).toEqual([
      { key: "inventory.outbox.v4", batch: OTHER_KEY, refused: null },
    ]);
  });

  test("a save held back as too old is listed from this build's key, whose rest are the outbox's", () => {
    const build = "2026-11-12T10:00:00.000Z";
    keep("inventory.outbox.v5", {
      build,
      entries: [
        olderMove({ state: "too_old", build }, { key: OTHER_KEY }),
        olderMove({ build }, { key: "c" }),
        olderMove(
          { state: "refused", note: "Not saved.", build },
          { key: "d" },
        ),
        olderMove({ state: "saved", build }, { key: "e" }),
      ],
    });
    expect(listed()).toEqual([
      { key: "inventory.outbox.v5", batch: OTHER_KEY, refused: null },
    ]);
  });

  test("a cart with lines is listed after the saves; one without is not", () => {
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: { 1000: "1" }, loose: "" }],
      usedAt: 1_700_000_000_000,
    });
    keep("inventory.outbox.v3", [olderMove()]);
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
      entries: [olderMove({ build: "x" })],
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
    keep("inventory.outbox.v3", [olderMove({ state: "saved" })]);
    keep("inventory.cart.v1", freshCart());
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
  });

  test("those holding something to list stay, as they were", () => {
    const saves = JSON.stringify([olderMove(), olderMove({ state: "saved" })]);
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
    keep("inventory.outbox.v1", [olderMove()]);
    keep("inventory.outbox.v2", [olderMove({ state: "saved" })]);
    keep("inventory.outbox.v3", [
      olderMove({}, { key: OTHER_KEY }),
      "not a batch",
    ]);
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v1")).toBeNull();
    // Only saved ones, which v3 never took: nothing to lose either.
    expect(localStorage.getItem("inventory.outbox.v2")).toBeNull();
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("a save an older tab kept after a newer one took over stays, unlisted", () => {
    // v2 took over v1 only while it held nothing: OTHER_KEY came after.
    const v1 = JSON.stringify([olderMove(), olderMove({}, { key: OTHER_KEY })]);
    localStorage.setItem("inventory.outbox.v1", v1);
    keep("inventory.outbox.v2", [olderMove()]);
    tidyOlder();
    expect(localStorage.getItem("inventory.outbox.v1")).toBe(v1);
    expect(listed().map((each) => each.key)).toEqual(["inventory.outbox.v2"]);
  });

  test("this build's outbox key stays, whatever it holds", () => {
    const kept = JSON.stringify({
      build: "x",
      entries: [olderMove({ state: "saved", build: "x" })],
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
        olderMove(),
        olderMove({}, { key: OTHER_KEY }),
        olderMove({ state: "saved" }, { key: "c" }),
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
      olderMove(),
      olderMove({ state: "saved" }, { key: "c" }),
    ]);
    expect(removeOlder(readOlder()[0] as Older)).toBe("done");
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
  });

  test("one held back under this build's key goes from it, which stays with the rest", () => {
    const build = "2026-11-12T10:00:00.000Z";
    const waiting = olderMove({ build }, { key: OTHER_KEY });
    keep("inventory.outbox.v5", {
      build,
      entries: [olderMove({ state: "too_old", build }), waiting],
    });
    const [save] = readOlder();
    expect(removeOlder(save as Older)).toBe("done");
    expect(readOlder()).toEqual([]);
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v5") ?? ""),
    ).toEqual({ build, entries: [waiting] });
    // Its last: the key is still the outbox's.
    keep("inventory.outbox.v5", {
      build,
      entries: [olderMove({ state: "too_old", build })],
    });
    expect(removeOlder(readOlder()[0] as Older)).toBe("done");
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v5") ?? ""),
    ).toEqual({ build, entries: [] });
  });

  test("one gone meanwhile, sent by an older tab, is not removed again", () => {
    keep("inventory.outbox.v3", [olderMove()]);
    const [save] = readOlder();
    keep("inventory.outbox.v3", [olderMove({ state: "saved" })]);
    expect(compared(save as Older)).toBe("gone");
    expect(removeOlder(save as Older)).toBe("gone");
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("a save changed meanwhile, by an older tab, is still the one discarded, and goes", () => {
    keep("inventory.outbox.v3", [olderMove()]);
    const [save] = readOlder();
    keep("inventory.outbox.v3", [olderMove({ state: "refused", note: "No." })]);
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
    keep("inventory.outbox.v3", [
      olderMove(),
      olderMove({}, { key: OTHER_KEY }),
    ]);
    expect(removeOlder(readOlder()[0] as Older, refusing("setItem"))).toBe(
      "not_kept",
    );
  });
});

describe("a browser that will not let it at storage", () => {
  test("one that will not read lists nothing, and the page still loads", () => {
    keep("inventory.outbox.v1", [olderMove()]);
    keep("inventory.outbox.v3", [olderMove()]);
    const older = new OlderStore(refusing("getItem"));
    expect(older.listed.value).toEqual([]);
    expect(localStorage.getItem("inventory.outbox.v1")).not.toBeNull();
  });

  test("one that will not forget keeps each, and says so", () => {
    // v1's one save is in v3 too, so v1 would go on load.
    keep("inventory.outbox.v1", [olderMove()]);
    keep("inventory.outbox.v3", [
      olderMove({ state: "saved" }),
      olderMove({}, { key: OTHER_KEY }),
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

  test("why one was refused is read as a code, and said as kept, or from its code", () => {
    const refusedFor = (why: object) =>
      only(olderMove({ state: "refused", ...why })).refused;
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
        note: "Not saved. Line 1: no. Open package said used up, 2: gone What is on an open package, 1: x",
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
          " What is on an open package, 1: ",
          { quoted: "x" },
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
      note: null,
    });
    // A code, in this build's words.
    expect(refusedFor({ reason: { code: "key_reused" } })).toEqual({
      code: "key_reused",
      note: noteOf({ code: "key_reused" }),
    });
    expect(noteOf({ code: "key_reused" })).toEqual({ bold: [say(KEY_REUSED)] });
    expect(
      refusedFor({ reason: { code: "recorded_elsewhere" } })?.note,
    ).toEqual({ bold: [say(SOME_NOT_RECORDED)] });
  });
});

describe("line by line", () => {
  const stored = (key = "inventory.outbox.v3") =>
    JSON.parse(localStorage.getItem(key) ?? "null");

  test("a batch the inventory refused is said line by line in the same words, and one not read, as none", () => {
    const refused = olderMove(
      { state: "refused", note: "Not recorded. Line 1: no." },
      {
        lines: [
          { item_id: 100, quantity: "4", package_id: 1004 },
          { item_id: 101, quantity: "12", open_package_key: OTHER_KEY },
          { item_id: 100, quantity: "8", package_id: 1000, package_size: 4 },
          { item_id: 999, quantity: "5" },
          { item_id: "unreadable" },
        ],
        used_up: [OTHER_KEY],
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

  test("a line discarded goes alone, the save keeping its key and the rest as they were", () => {
    const older = mixedOlder();
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
    const other = olderMove({ build }, { key: OTHER_KEY });
    keep("inventory.outbox.v3", {
      build,
      entries: [
        other,
        olderMove(
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
        olderMove({}, { lines: [{ item_id: 101, quantity: "2" }] }),
      ],
    });
    keep("inventory.outbox.v3", [
      olderMove({}, { lines: "many", used_up: [KEY, OTHER_KEY] }),
    ]);
    const lost = readOlder()[0] as Older;
    const [report] = toCart(lost, CATALOGUE).parts;
    expect(dropPart(lost, report?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored()[0].body).toMatchObject({
      lines: "many",
      used_up: [OTHER_KEY],
    });
  });

  test("of an older cart's line, what it said of one open package goes, the rest kept", () => {
    const line = {
      itemId: 101,
      packages: { 1001: "1" },
      loose: "",
      open: { [KEY]: { amount: "3" }, [OTHER_KEY]: { usedUp: true } },
    };
    keep("inventory.cart.v1", { ...freshCart(), lines: [line] });
    const older = readOlder()[0] as Older;
    const taken = toCart(older, CATALOGUE).parts.find(
      (part) => part.words === "Cat6 cable: 3 m from an open package",
    );
    expect(dropPart(older, taken?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored("inventory.cart.v1").lines).toEqual([
      { ...line, open: { [OTHER_KEY]: { usedUp: true } } },
    ]);
  });

  test("an item's lines that go back are discarded together", () => {
    const older = onlyOlder({
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
    const older = onlyOlder({
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
      const older = onlyOlder({
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
      olderMove({}, { lines: "many", used_up: [KEY] }),
    ]);
    const older = readOlder()[0] as Older;
    const [report] = toCart(older, CATALOGUE).parts;
    expect(dropPart(older, report?.drop ?? [], CATALOGUE)).toBe("done");
    expect(stored()[0].body).toMatchObject({ lines: "many", used_up: [] });
  });

  test("an item retired since is listed as named, and goes back once discarded", () => {
    const older = onlyOlder({
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
    const older = mixedOlder();
    const { parts } = toCart(older, CATALOGUE);
    const drop = parts[0]?.drop ?? [];
    expect(dropPart(older, drop, CATALOGUE, refusing("setItem"))).toBe(
      "not_kept",
    );
    keep("inventory.outbox.v3", [olderMove({ state: "refused", note: "No." })]);
    const before = stored();
    expect(compared(older)).toBe("changed");
    expect(dropPart(older, drop, CATALOGUE)).toBe("changed");
    expect(stored()).toEqual(before);
  });

  test("the store lists again after a line goes", () => {
    mixedOlder();
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
  test("the oldest that goes back whole is next, one of no known time last, and those tried are skipped", () => {
    keep("inventory.outbox.v3", [
      olderMove({ queuedAt: null }, { key: "untimed" }),
      olderMove({ queuedAt: 3 }, { key: "newer" }),
      olderMove({ queuedAt: 1 }, { key: "unread", lines: "many" }),
      olderMove({ queuedAt: 2 }, { key: "older" }),
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
});

test("the list follows another tab's changes to the older keys", () => {
  keep("inventory.outbox.v3", [olderMove()]);
  const older = new OlderStore();
  const stop = older.listen();
  expect(older.listed.value).toHaveLength(1);
  keep("inventory.outbox.v3", [olderMove({ state: "saved" })]);
  window.dispatchEvent(
    new StorageEvent("storage", { key: "inventory.outbox.v3" }),
  );
  expect(older.listed.value).toEqual([]);
  stop();
});
