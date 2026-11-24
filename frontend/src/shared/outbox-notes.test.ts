import { describe, expect, test } from "vitest";
import { moveBody } from "../testing/saves";
import { say } from "./i18n";
import {
  COUNT_WHOLE_LEFT,
  noteOf,
  PROBLEMS,
  recordedHeading,
  wordsOf,
} from "./outbox-notes";

describe("what another tab recorded (inventory-tng-0gg)", () => {
  test("what was recorded is headed by who recorded it: this tab, or another (Ali, inventory-tng-0gg, 2026-11-15, 1 and 6)", () => {
    const batch = moveBody();
    expect(recordedHeading({ by: "here", transactionId: 412, batch })).toBe(
      "Already recorded from this tab in transaction 412",
    );
    expect(
      recordedHeading({ by: "elsewhere", transactionId: 412, batch }),
    ).toBe("Recorded in another tab in transaction 412");
  });
});

describe("a refusal's problems (inventory-tng-61ys)", () => {
  const problems = (...kinds: string[]) =>
    (
      noteOf({
        code: "refused",
        problems: kinds.map((kind, at) => ({
          names: { of: "line" as const, n: at + 1 },
          kind,
        })),
      }).problems ?? []
    ).map(wordsOf);

  test("each is one of its own, never joined to another (Ali, 2026-11-20)", () => {
    const retired = say(PROBLEMS.retired);
    const noneLeft = say(PROBLEMS.retired_package_none_left);
    expect(problems("retired", "retired_package_none_left")).toEqual([
      `Line 1: ${retired}`,
      `Line 2: ${noneLeft}`,
    ]);
  });

  test("a retired package with none left never names a button (Ali, 2026-11-20, inventory-tng-3ukw)", () => {
    expect(say(PROBLEMS.retired_package_none_left)).toBe(
      "This package is retired, and the inventory has none left sealed. Ask an administrator to count it.",
    );
  });

  test("a fraction left in a box of pieces is refused by the box's name (Ali, 2026-11-24, inventory-tng-uwi3)", () => {
    const refused = (boxes?: (string | null)[]) =>
      noteOf(
        {
          code: "refused",
          problems: [1, 2, 3].map((n) => ({
            names: { of: "lengths" as const, n },
            kind: "not_whole",
          })),
        },
        true,
        boxes,
      ).problems;
    expect(say(COUNT_WHOLE_LEFT, { box: "Box of 500" })).toBe(
      "Box of 500: count whole pieces left.",
    );
    const generic = `What is on an open package, 3: ${say(PROBLEMS.not_whole)}`;
    // Named as kept, quoted; one with no name, or none kept, by number.
    expect(refused(["Box of 500", null])).toEqual([
      [{ quoted: "Box of 500" }, ": count whole pieces left."],
      [`What is on an open package, 2: ${say(PROBLEMS.not_whole)}`],
      [generic],
    ]);
    expect(refused()?.[2]).toEqual([generic]);
  });
});
