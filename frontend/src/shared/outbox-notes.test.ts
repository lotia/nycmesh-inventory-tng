import { describe, expect, test } from "vitest";
import { moveBody } from "../testing/saves";
import { say } from "./i18n";
import { noteOf, PROBLEMS, recordedHeading, wordsOf } from "./outbox-notes";

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
});
