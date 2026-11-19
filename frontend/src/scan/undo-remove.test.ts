import { describe, expect, test } from "vitest";
import { type Cart, freshCart } from "./cart";
import { removeLine, undone } from "./undo-remove";

const line = (itemId: number) => ({ itemId, packages: {}, loose: "1" });
const FILLED: Cart = {
  ...freshCart(),
  lines: [line(1), line(2), line(3)],
  startedAt: 5,
  batchKey: "00000000-0000-4000-8000-000000000001",
};

/** The item's line taken out of `cart`, which has one. */
function taken(cart: Cart, itemId: number) {
  const result = removeLine(cart, itemId);
  expect(result).not.toBeNull();
  const { next, removed } = result as NonNullable<typeof result>;
  return { next, removed: { ...removed, revision: "r" } };
}

describe("removeLine and undone", () => {
  test("a line removed goes back where it was", () => {
    const { next, removed } = taken(FILLED, 2);
    expect(next.lines).toEqual([line(1), line(3)]);
    expect(undone(next, removed).lines).toEqual(FILLED.lines);
  });

  test("the last line put back takes back the key and start the cart had", () => {
    const { removed } = taken({ ...FILLED, lines: [line(1)] }, 1);
    const back = undone({ ...freshCart(), revision: "r" }, removed);
    expect(back).toMatchObject({
      lines: [line(1)],
      batchKey: FILLED.batchKey,
      startedAt: 5,
    });
  });

  test("an item with no line has nothing to remove", () => {
    expect(removeLine(FILLED, 9)).toBeNull();
  });
});
