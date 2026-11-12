import { render, screen } from "@testing-library/preact";
import { describe, expect, test } from "vitest";
import { afterCut, cutOf, usedUpOf } from "./cart";
import {
  endingNote,
  OpenPackageCard,
  PRINTED_LENGTH,
  packageWord,
} from "./OpenPackageCard";
import { cable, radio } from "./test-catalogue";

describe("an open package's card", () => {
  const open = { left: 120_000, short_below: "15" };

  test("what a cut leaves, and why nothing usable would be", () => {
    expect(afterCut(open, 20_000)).toEqual({ after: 100_000, ending: null });
    expect(afterCut(open, 110_000)).toEqual({ after: 10_000, ending: "short" });
    expect(afterCut(open, 120_000)).toEqual({ after: 0, ending: "used" });
    expect(afterCut(open, 121_000)).toEqual({ after: -1000, ending: "over" });
    expect(afterCut({ ...open, short_below: null }, 110_000).ending).toBe(null);
  });

  test("used up follows the cut until the volunteer says", () => {
    const all = { amount: "120", whole: false };
    expect(usedUpOf(open, all)).toBe(true);
    expect(usedUpOf(open, { ...all, usedUp: false })).toBe(false);
    // What it shows grew: the cut no longer ends it.
    expect(usedUpOf({ ...open, left: 200_000 }, all)).toBe(false);
    // Nothing cut, not yet a number, or all of it moved: no cut.
    for (const taken of [
      { amount: "0", whole: false },
      { amount: "120.", whole: false },
      { amount: "120", whole: true },
      { amount: "", whole: false },
    ]) {
      expect(cutOf(open, taken)).toBe(null);
    }
  });

  test("a package's word keeps a name of its own", () => {
    const word = (name: string) => packageWord({ package_name: name });
    expect(word("Spool")).toBe("spool");
    expect(word("Box of 305 m")).toBe("box of 305 m");
    expect(word("UniFi Box")).toBe("UniFi Box");
    expect(word("RJ45 bag")).toBe("RJ45 bag");
  });

  test("things counted each are not cable", () => {
    expect(endingNote("used", "bag", "each")).toBe(
      "Your cut uses up the bag, so it's ticked. Untick it if any is left. An administrator confirms.",
    );
  });
});

/** An open package of an item, shown as a card with `taken`. */
function card(
  item: typeof cable,
  taken: { amount: string; whole: boolean; now?: string },
  { left = 4000, moving = false } = {},
) {
  const open = {
    id: 1,
    key: "11111111-1111-4111-8111-111111111111",
    item_id: item.id,
    package_id: item.packages[0]?.id ?? 0,
    package_name: item.packages[0]?.name ?? "",
    location_id: 10,
    opened_at: "2026-11-03T10:00:00Z",
    left,
    short_below: null,
  };
  return render(
    <OpenPackageCard
      open={open}
      item={item}
      taken={taken}
      moving={moving}
      onTake={() => {}}
    />,
  );
}

test("things counted each get no hint to read a printed length", () => {
  card(radio, { amount: "", whole: false, now: "" });
  expect(screen.getByLabelText(/^What's on the box now/)).toBeDefined();
  expect(screen.queryByText(PRINTED_LENGTH)).toBeNull();
});

test("an item in a length gets the hint once its field is asked for", () => {
  card(cable, { amount: "", whole: false }).unmount();
  expect(screen.queryByText(PRINTED_LENGTH)).toBeNull();
  card(cable, { amount: "", whole: false, now: "" });
  const field = screen.getByLabelText(/^What's on the spool now/);
  expect(
    document.getElementById(field.getAttribute("aria-describedby") ?? "")
      ?.textContent,
  ).toBe(PRINTED_LENGTH);
});

test("moved whole, then shown at nothing, its box stays to untick", () => {
  const whole = { amount: "20", whole: true };
  card(cable, whole, { left: -4000, moving: true });
  expect(screen.getByLabelText("All of it, moved still open")).toBeDefined();
});

test("showing nothing, nothing is offered to move whole", () => {
  card(cable, { amount: "", whole: false }, { left: 0, moving: true });
  expect(screen.queryByLabelText("All of it, moved still open")).toBeNull();
});
