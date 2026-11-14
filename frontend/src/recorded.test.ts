import { describe, expect, test } from "vitest";
import { freshCart } from "./cart";
import { type Body, type Recorded, wordsOf } from "./outbox";
import {
  changesShown,
  differenceOf,
  differences,
  differenceToCart,
  recordedLines,
  reportsAdded,
} from "./recorded";
import { CATALOGUE } from "./test-catalogue";

const KEY = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
const OPEN = "5d0c2f1e-7a7b-4f43-9c1a-2b3c4d5e6f70";

/** A move from the hub to the van holding `lines`. */
function move(lines: Body["lines"], fields: Partial<Body> = {}): Body {
  return {
    key: KEY,
    kind: "move",
    volunteer_id: 1,
    from_location_id: 10,
    to_location_id: 12,
    install_number: null,
    lines,
    ...fields,
  };
}

const boxes = (n: number) => ({
  item_id: 100,
  quantity: `${4 * n}.000`,
  package_id: 1000,
  package_size: "4",
});
const metres = (n: number) => ({ item_id: 101, quantity: String(n) });
const cut = (n: number) => ({ ...metres(n), open_package_key: OPEN });

const recorded = (batch: Body): Recorded => ({
  by: "elsewhere",
  transactionId: 7,
  batch,
});

/** `mine`, as a cart put back with what `was` holds, then changed, sends
 * it, unless it says what it was changed from. */
const putBack = (was: Body, mine: Body): Body => ({
  changed_from: was.lines,
  ...mine,
});

/** What changesShown says, as plain text, each note after its line. */
function shown(was: Body, mine: Body): string[] {
  return changesShown(recorded(was), putBack(was, mine), CATALOGUE).map(
    ({ words, note }) =>
      wordsOf(words) + (note === null ? "" : ` / ${wordsOf(note.bold)}`),
  );
}

describe("what differs", () => {
  test("each line by item and form: more, new, fewer, and gone", () => {
    expect(
      shown(move([boxes(2), metres(5)]), move([boxes(1), metres(25), cut(3)])),
    ).toEqual([
      "LiteBeam 5AC: 1 × Box fewer / Recorded 2 × Box, you changed it to 1. An administrator will correct it.",
      "Cat6 cable: 20 m more",
      "Cat6 cable: 3 m from an open package, new / Can't add these items. Please rescan.",
    ]);
    expect(shown(move([metres(5)]), move([]))).toEqual([
      "Cat6 cable: 5 m fewer / Recorded 5 m, you changed it to 0 m. An administrator will correct it.",
    ]);
    expect(shown(move([]), move([{ item_id: 100, quantity: "1" }]))).toEqual([
      "LiteBeam 5AC: 1, new",
    ]);
  });

  test("the same lines, in any order or way of writing them, differ in nothing", () => {
    const was = move([boxes(1), metres(5)]);
    expect(
      differences(
        was,
        putBack(
          was,
          move([
            { ...metres(5), quantity: 5 },
            { ...boxes(1), quantity: "4" },
          ]),
        ),
      ),
    ).toEqual([]);
  });

  test("moved between other places, every line differs", () => {
    const was = move([metres(5)]);
    const changed = differences(
      was,
      putBack(was, move([metres(5)], { to_location_id: 11 })),
    );
    expect(changed.map(({ recorded, mine }) => [recorded, mine])).toEqual([
      [0, 5000],
      [5000, 0],
    ]);
  });

  test("another volunteer alone moves nothing otherwise", () => {
    const was = move([metres(5)]);
    expect(
      differences(was, putBack(was, move([metres(5)], { volunteer_id: 2 }))),
    ).toEqual([]);
  });

  test("a report it makes anew is said, and never goes back", () => {
    const said = move([], { kind: "report", to_location_id: null });
    expect(
      reportsAdded(said, { ...said, used_up: [OPEN], lengths: null }),
    ).toEqual(["used_up"]);
    expect(
      shown(said, {
        ...said,
        lengths: [{ open_package_key: OPEN, left: "12" }],
      }),
    ).toEqual([
      "What is on an open package, new / Can't add these items. Please rescan.",
    ]);
  });

  test("a length said again as it was is no change; a quantity it cannot read counts as none", () => {
    const length = { open_package_key: OPEN, left: "12" };
    const said = move([], { kind: "report", lengths: [length] });
    expect(reportsAdded(said, { ...said, lengths: [length] })).toEqual([]);
    expect(recordedLines(recorded(said), CATALOGUE).map(wordsOf)).toEqual([
      "What is on an open package",
    ]);
    const was = move([metres(5)]);
    expect(
      differences(
        was,
        putBack(was, move([{ ...metres(5), quantity: "5.5555" }])),
      ),
    ).toEqual([
      {
        line: { ...metres(5), quantity: "5.5555" },
        kind: "move",
        recorded: 5000,
        mine: 0,
        changed: -5000,
      },
    ]);
  });

  test("a batch from before another tab's change, which says nothing it was changed from, changed nothing: that tab did", () => {
    const newer = move([boxes(2), metres(9)]);
    const older = move([boxes(1), metres(5)]);
    expect(differences(newer, older)).toEqual([]);
    expect(changesShown(recorded(newer), older, CATALOGUE)).toEqual([]);
    expect(differenceOf(older, newer)).toBeNull();
  });

  test("only what the volunteer changed since it went back: not what the other tab changed", () => {
    const back = move([boxes(1), metres(5)]);
    // The other tab added a box and took off 3 m, then recorded first.
    const other = move([boxes(2), metres(2)]);
    // This one took off 4 m, and kept the box as it went back.
    const mine = putBack(back, move([boxes(1), metres(1)]));
    expect(shown(other, mine)).toEqual([
      "Cat6 cable: 1 m fewer / Recorded 2 m, you changed it to 1 m. An administrator will correct it.",
    ]);
    // Kept as it went back while the other tab took off: nothing to add.
    const kept = putBack(back, move([boxes(1), metres(5)]));
    expect(shown(other, kept)).toEqual([]);
    expect(differenceOf(other, kept)).toBeNull();
  });

  test("an opening recorded, and a move sent, each line said of its own batch", () => {
    const opened = {
      ...boxes(1),
      open_package_key: OPEN,
    };
    const opening = move([opened], { kind: "open", to_location_id: 10 });
    expect(
      shown(opening, move([metres(1)])).map((said) => said.split(" / ")[0]),
    ).toEqual(["Cat6 cable: 1 m, new", "LiteBeam 5AC: 1 × Box fewer"]);
  });

  test("what was recorded, line by line, then its reports", () => {
    const was = recorded(move([boxes(2), cut(3)], { used_up: [OPEN] }));
    expect(recordedLines(was, CATALOGUE).map(wordsOf)).toEqual([
      "LiteBeam 5AC: 2 × Box",
      "Cat6 cable: 3 m from an open package",
      "Open package said used up",
    ]);
  });

  test("names and package names are never the page's counts", () => {
    const [line] = recordedLines(recorded(move([boxes(2)])), CATALOGUE);
    expect(line).toEqual([
      { quoted: "LiteBeam 5AC" },
      ": ",
      "2 × ",
      { quoted: "Box" },
    ]);
  });
});

describe("the difference", () => {
  test("only what it adds, not from an open package, under a new key, as the save was", () => {
    const mine = move([boxes(3), metres(10), cut(3)], {
      used_up: [OPEN],
      volunteer_id: 2,
    });
    const was = move([boxes(1), metres(12)]);
    const difference = differenceOf(was, putBack(was, mine));
    expect(difference).toMatchObject({
      kind: "move",
      volunteer_id: 2,
      from_location_id: 10,
      to_location_id: 12,
      lines: [
        { item_id: 100, quantity: "8", package_id: 1000, package_size: "4" },
      ],
    });
    expect(difference).not.toHaveProperty("used_up");
    expect(difference?.key).not.toBe(KEY);
    // Made from the save's key: added again, it is the same batch.
    expect(differenceOf(was, putBack(was, mine))?.key).toBe(difference?.key);
  });

  test("none when it adds nothing that can go back", () => {
    const was = move([metres(5)]);
    expect(
      differenceOf(was, putBack(was, move([metres(5), cut(1)]))),
    ).toBeNull();
    expect(differenceOf(was, putBack(was, move([metres(2)])))).toBeNull();
  });

  test("goes in a cart as a save put back does, under its key", () => {
    const was = move([boxes(1)]);
    const difference = differenceOf(was, putBack(was, move([boxes(3)])));
    expect(difference).not.toBeNull();
    const { cart, gaps } = differenceToCart(
      difference as Body,
      CATALOGUE,
      freshCart(),
    );
    expect(cart).toMatchObject({
      kind: "move",
      fromId: 10,
      toId: 12,
      batchKey: difference?.key,
      lines: [{ itemId: 100, packages: { 1000: "2" } }],
    });
    expect(gaps).toEqual([]);
  });
});
