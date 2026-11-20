import { describe, expect, test } from "vitest";
import { say } from "../shared/i18n";
import type { Entry, Problem } from "../shared/outbox";
import { NOT_ACCEPTED, PROBLEMS } from "../shared/outbox-notes";
import { CATALOGUE } from "../testing/catalogue";
import { KEY, moveBody, outboxEntry } from "../testing/saves";
import { restOf, TAKEN_OFF, takenOff } from "./record-the-rest";

/** Radios in a box and loose (item 100, line 1), cable (101, line 2) and
 * a retired item the catalogue only names (103, line 3). */
const BODY = moveBody({
  lines: [
    { item_id: 100, quantity: "4", package_id: 1000, package_size: "4" },
    { item_id: 101, quantity: "2", package_id: null },
    { item_id: 100, quantity: "1", package_id: null },
    { item_id: 103, quantity: "3", package_id: null },
  ],
  used_up: ["8a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"],
});

/** BODY refused with `problems`. */
function refused(...problems: Problem[]): Entry {
  return outboxEntry({
    body: BODY,
    state: "refused",
    reason: { code: "refused", problems },
  });
}

const onLine = (n: number, kind = "retired_package_none_left"): Problem => ({
  names: { of: "line", n },
  kind,
});

describe("restOf", () => {
  test("takes off every line of each item a problem names, keeping the rest and its reports", () => {
    const rest = restOf(refused(onLine(1), onLine(3, "not_whole")));
    expect(rest?.lines).toEqual([BODY.lines[1]]);
    expect(rest?.used_up).toEqual(BODY.used_up);
    expect(rest?.volunteer_id).toBe(BODY.volunteer_id);
  });

  test("is under a key of its own, the same each time for the same batch", () => {
    const entry = refused(onLine(2));
    const key = restOf(entry)?.key;
    expect(key).not.toBe(KEY);
    expect(restOf(refused(onLine(2)))?.key).toBe(key);
    const other = outboxEntry({
      ...entry,
      body: { ...BODY, from_location_id: 11 },
    });
    expect(restOf(other)?.key).not.toBe(key);
  });

  test("keeps nothing it was changed from", () => {
    const entry = refused(onLine(2));
    const changed = { ...entry, body: { ...BODY, changed_from: BODY.lines } };
    expect(restOf(changed)).not.toHaveProperty("changed_from");
  });

  test.each<[string, Entry]>([
    [
      "not refused for what it held",
      outboxEntry({ state: "refused", reason: { code: "key_reused" } }),
    ],
    ["waiting", outboxEntry()],
    ["refused naming no line", refused({ names: null, kind: "not_found" })],
    [
      "refused naming a report",
      refused({ names: { of: "used_up", n: 1 }, kind: "not_found" }),
    ],
    ["refused naming a line it does not hold", refused(onLine(9))],
    ["refused naming every line", refused(onLine(1), onLine(2), onLine(3))],
  ])("none, %s", (_, entry) => {
    expect(restOf(entry)).toBeNull();
  });
});

describe("takenOff", () => {
  test("names each line taken off with why, in the problem's words, once each, a retired item too", () => {
    const entry = refused(onLine(1), onLine(1), onLine(3, "something new"), {
      names: null,
      kind: "not_found",
    });
    expect(takenOff(entry, CATALOGUE)).toEqual([
      say(TAKEN_OFF, {
        name: "LiteBeam 5AC",
        why: say(PROBLEMS.retired_package_none_left),
      }),
      say(TAKEN_OFF, {
        name: "NanoStation M5",
        why: say(NOT_ACCEPTED),
      }),
    ]);
  });

  test("says each of two items alike in words, as two no longer in the catalogue", () => {
    const entry = outboxEntry({
      body: moveBody({
        lines: [
          { item_id: 998, quantity: "1", package_id: null },
          { item_id: 999, quantity: "1", package_id: null },
          { item_id: 101, quantity: "2", package_id: null },
        ],
      }),
      state: "refused",
      reason: { code: "refused", problems: [onLine(1), onLine(2)] },
    });
    const [first, second, ...more] = takenOff(entry, CATALOGUE);
    expect(more).toEqual([]);
    expect(second).toBe(first);
  });
});
