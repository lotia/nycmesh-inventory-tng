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

describe("a refusal's problems, joined (inventory-tng-r87e)", () => {
  // retired_package_none_left alone ends in a full stop (Ali,
  // 2026-11-20): nothing is added after it, before or after another.
  const said = (...kinds: string[]) =>
    wordsOf(
      noteOf({
        code: "refused",
        problems: kinds.map((kind, at) => ({
          names: { of: "line" as const, n: at + 1 },
          kind,
        })),
      }).plain ?? [],
    );
  const noneLeft = say(PROBLEMS.retired_package_none_left);
  const retired = say(PROBLEMS.retired);

  test("its own stop is never doubled, before another or after", () => {
    expect(noneLeft.endsWith("count it.")).toBe(true);
    expect(said("retired_package_none_left", "retired")).toBe(
      ` Line 1: ${noneLeft} Line 2: ${retired}`,
    );
    expect(said("retired", "retired_package_none_left")).toBe(
      ` Line 1: ${retired} Line 2: ${noneLeft}`,
    );
    expect(said("retired_package_none_left", "retired")).not.toContain("..");
  });
});
