import { fireEvent, render, screen } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { messages as english } from "../locales/en.po";
import { say, speak } from "../shared/i18n";
import { cart } from "../testing/cart";
import { CATALOGUE, cable, openAt, radio } from "../testing/catalogue";
import { saidAsText } from "../testing/said";
import { toBatch } from "./batch";
import {
  ALL_OF_IT,
  endingNote,
  OpenPackageCard,
  PRINTED_LENGTH,
  SHORT_END,
  TOO_FEW_LEFT,
  WRONG_LENGTH,
  WRONG_NUMBER,
} from "./OpenPackageCard";
import { afterCut, cutOf, usedUpOf } from "./offered";

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
      { amount: ".", whole: false },
      { amount: "120", whole: true },
      { amount: "", whole: false },
    ]) {
      expect(cutOf(open, taken)).toBe(null);
    }
  });

  test("things counted each are not cable", () => {
    expect(endingNote("used", "bag", "each")).toBe(
      "Taking these uses up the bag, so “Nothing usable left on it” is ticked. Untick it if any is left. An administrator confirms.",
    );
    expect(endingNote("over", "bag", "each")).toBe(
      "You're taking more than the bag shows, so “Nothing usable left on it” is ticked. It's recorded as you typed it, and an administrator will check the bag.",
    );
    // A box of pieces may be too short to use below a number left too.
    expect(endingNote("short", "bag", "each")).toBe(
      "After taking these, too few are left in the bag to use, so “Nothing usable left on it” is ticked. Untick it if you'll keep them.",
    );
  });
});

/** An open package of an item, shown as a card with `taken`. */
function card(
  item: typeof cable,
  taken: { amount: string; whole: boolean; now?: string },
  { left = 4000, moving = false, onTake = () => {} } = {},
) {
  const open = openAt(1, left, {
    key: "11111111-1111-4111-8111-111111111111",
    item_id: item.id,
    package_id: item.packages[0]?.id ?? 0,
    package_name: item.packages[0]?.name ?? "",
  });
  return render(
    <OpenPackageCard
      open={open}
      item={item}
      taken={taken}
      moving={moving}
      onTake={onTake}
    />,
  );
}

test("things counted each get no hint to read a printed length", () => {
  card(radio, { amount: "", whole: false, now: "" });
  expect(
    screen.getByLabelText(/^How many are left in the Box now/),
  ).toBeDefined();
  expect(screen.queryByText(say(PRINTED_LENGTH))).toBeNull();
});

test("an item in a length gets the hint once its field is asked for", () => {
  card(cable, { amount: "", whole: false }).unmount();
  expect(screen.queryByText(say(PRINTED_LENGTH))).toBeNull();
  card(cable, { amount: "", whole: false, now: "" });
  const field = screen.getByLabelText(/^What's on the Spool now/);
  expect(
    document.getElementById(field.getAttribute("aria-describedby") ?? "")
      ?.textContent,
  ).toBe(say(PRINTED_LENGTH));
});

test("moved whole, then shown at nothing, its box stays to untick", () => {
  const whole = { amount: "20", whole: true };
  card(cable, whole, { left: -4000, moving: true });
  expect(screen.getByLabelText(say(ALL_OF_IT))).toBeDefined();
});

test("showing nothing, nothing is offered to move whole", () => {
  card(cable, { amount: "", whole: false }, { left: 0, moving: true });
  expect(screen.queryByLabelText(say(ALL_OF_IT))).toBeNull();
});

test("what is on it, typed, is asked again once the box asks otherwise", () => {
  const onTake = vi.fn();
  const cutBox = () => screen.getByLabelText(/^Cut or take from the Spool/);
  // Typed as it is now: a cut typed since makes it "after this cut".
  const now = card(cable, { amount: "", whole: false, now: "3" }, { onTake });
  fireEvent.input(cutBox(), { target: { value: "1" } });
  expect(onTake).toHaveBeenLastCalledWith({
    amount: "1",
    whole: false,
    now: "",
  });
  now.unmount();
  // After a cut, another cut: still after this cut, kept.
  card(cable, { amount: "1", whole: false, now: "2" }, { onTake });
  fireEvent.input(cutBox(), { target: { value: "1.5" } });
  expect(onTake).toHaveBeenLastCalledWith({
    amount: "1.5",
    whole: false,
    now: "2",
  });
});

test("moved whole, what is on it as typed now is said still", () => {
  const onTake = vi.fn();
  card(cable, { amount: "", whole: false, now: "3" }, { moving: true, onTake });
  fireEvent.click(screen.getByLabelText(say(ALL_OF_IT)));
  expect(onTake).toHaveBeenLastCalledWith({
    amount: "4",
    whole: true,
    now: "3",
  });
});

test("what is on it is written as the locale writes numbers, a box takes it plain", () => {
  // German's separators, read with the English words.
  speak("de", english);
  const onTake = vi.fn();
  card(
    cable,
    { amount: "0.5", whole: false },
    { left: 1_234_567, moving: true, onTake },
  );
  expect(document.body.textContent).toContain("1.234,567 m");
  expect(document.body.textContent).toContain("1.234,067 m");
  fireEvent.click(screen.getByLabelText(say(ALL_OF_IT)));
  expect(onTake).toHaveBeenLastCalledWith({ amount: "1234.567", whole: true });
});

describe("an open box of 500 connectors, followed", () => {
  // Worded for pieces, with the number keypad, whole amounts only (Ali,
  // 2026-11-23, inventory-tng-uwi3).
  const connectors = {
    ...radio,
    id: 102,
    name: "RJ45 connectors",
    packages: [
      {
        id: 1002,
        name: "Box of 500",
        size: "500.000",
        track_opened: true,
        retired: false,
        short_below: null,
        sealed: [],
      },
    ],
  };

  test("its card says how many are left and taken, on the number keypad", () => {
    const onTake = vi.fn();
    const shown = card(
      connectors,
      { amount: "20", whole: false },
      { left: 300_000, onTake },
    );
    const take = screen.getByLabelText(
      "Take from the Box of 500 opened 2026-11-03",
    );
    expect(take.getAttribute("inputmode")).toBe("numeric");
    expect(
      (take.getAttribute("aria-describedby") ?? "")
        .split(" ")
        .map((id) => document.getElementById(id)?.textContent),
    ).toEqual(["Now: 300 left", "After taking 20: 280 left"]);
    expect(screen.getByText("After taking 20")).toBeDefined();
    expect(screen.queryByText(/ each/)).toBeNull();
    expect(
      screen.queryByRole("button", { name: say(WRONG_LENGTH) }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: say(WRONG_NUMBER) }));
    expect(onTake).toHaveBeenLastCalledWith({
      amount: "20",
      whole: false,
      now: "",
    });
    shown.unmount();
    card(
      connectors,
      { amount: "320", whole: false, now: "" },
      { left: 300_000 },
    );
    expect(screen.getByText("20 over")).toBeDefined();
    const left = screen.getByLabelText(
      "How many will be left in the Box of 500 after taking these, if you can tell",
    );
    expect(left.getAttribute("inputmode")).toBe("numeric");
    expect(screen.queryByText(say(PRINTED_LENGTH))).toBeNull();
  });

  test("a part of one said to be left is refused", () => {
    const key = "11111111-1111-4111-8111-111111111111";
    const catalogue = { ...CATALOGUE, items: [...CATALOGUE.items, connectors] };
    const lines = [
      {
        itemId: connectors.id,
        packages: {},
        loose: "",
        open: { [key]: { amount: "", whole: false, now: "2.5" } },
      },
    ];
    expect(saidAsText(toBatch(cart({ lines }), catalogue))).toEqual({
      problems: [
        "[RJ45 connectors]: enter what is on the open package, in whole units, or leave it blank.",
      ],
    });
  });

  test("too few left in it to use is flagged so, not as a short end", () => {
    // Ali, 2026-11-24, inventory-tng-uwi3.
    render(
      <OpenPackageCard
        open={openAt(1, 30_000, {
          item_id: connectors.id,
          package_id: 1002,
          package_name: "Box of 500",
          short_below: "50.000",
        })}
        item={connectors}
        taken={{ amount: "", whole: false }}
        moving={false}
        onTake={() => {}}
      />,
    );
    expect(screen.getByText(say(TOO_FEW_LEFT))).toBeDefined();
    expect(screen.queryByText(say(SHORT_END))).toBeNull();
  });
});
