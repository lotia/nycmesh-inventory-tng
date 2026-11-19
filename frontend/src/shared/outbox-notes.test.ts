import { describe, expect, test } from "vitest";
import { moveBody } from "../testing/saves";
import { recordedHeading } from "./outbox-notes";

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
