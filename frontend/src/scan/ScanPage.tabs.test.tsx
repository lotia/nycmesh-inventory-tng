import { fireEvent, screen } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { cart } from "../testing/cart";
import { CATALOGUE } from "../testing/catalogue";
import { answering, json, saved } from "../testing/fetch";
import {
  expectBold,
  fillMove,
  find,
  isCatalogue,
  open,
  pick,
  ready,
  save,
  saves,
  selected,
  server,
} from "../testing/scan-page";
import { NOTHING_IN_CART, TO, YOUR_NAME } from "./CartForm";
import { STORAGE_KEY as CART_KEY, freshCart, storeCart } from "./cart";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

describe("other tabs and a changing catalogue", () => {
  test("another tab's cart change shows here", async () => {
    open();
    await ready();
    storeCart({
      ...freshCart(),
      volunteerId: 2,
      fromId: 10,
      toId: 12,
      lines: [{ itemId: 101, packages: {}, loose: "4" }],
    });
    // Effects attach after the page paints, so send until it is heard.
    await vi.waitFor(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
      expect(screen.getByText("4 m in all")).toBeDefined();
    });
  });

  test("a cart another tab changed is shown again, not saved twice", async () => {
    const { api, sent } = server();
    open(api);
    await ready();
    fillMove();
    // Another tab saved it, and stored the next, empty cart.
    storeCart(cart());
    save();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "changed in another tab",
    );
    expect(screen.getByText(say(NOTHING_IN_CART))).toBeDefined();
    expect(saves(sent)).toEqual([]);
  });

  /** A cart counting 2 boxes of 4 radios, and 1 loose, when an
   * administrator makes the box, never used, a box of 8: the box count
   * once the page has taken the new catalogue. */
  async function resizedWhileCounted() {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let catalogue = CATALOGUE;
    const { api, sent } = answering((request) =>
      isCatalogue(request) ? json(catalogue, 200) : saved(),
    );
    open(api);
    await ready();
    fillMove();
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    fireEvent.input(screen.getByLabelText("Box (4 each)"), {
      target: { value: "2" },
    });
    expect(screen.getByText("9 each in all")).toBeDefined();
    catalogue = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 100
          ? {
              ...item,
              packages: item.packages.map((box) => ({ ...box, size: "8.000" })),
            }
          : item,
      ),
    };
    await vi.advanceTimersByTimeAsync(11 * 60_000);
    const count = (await screen.findByLabelText(
      "Box (8 each)",
    )) as HTMLInputElement;
    // Cleared once the page takes the catalogue, after it paints.
    await vi.waitFor(() => expect(count.value).toBe(""));
    return { sent, count };
  }

  test("a count of a package resized while the cart is open is cleared and said, until typed again", async () => {
    const { sent, count } = await resizedWhileCounted();
    const said = screen
      .getByText(/since this item was added/)
      .closest('[role="alert"]');
    // Not 2 boxes of 8: the one loose radio the item's pick added.
    expect(screen.getByText("1 each in all")).toBeDefined();
    expectBold(
      said,
      "The package size has changed to 8 since this item was added. Enter the count again.",
      ["8"],
    );
    // It stays through other changes.
    pick(say(TO), "11");
    expect(screen.getByText(/since this item was added/)).toBeDefined();
    fireEvent.input(count, { target: { value: "1" } });
    expect(screen.queryByText(/since this item was added/)).toBeNull();
    save();
    await screen.findByText(/^Recorded \(/);
    const body = await saves(sent)[0]?.json();
    expect(body.lines).toContainEqual({
      item_id: 100,
      quantity: "8",
      package_id: 1000,
      package_size: "8",
    });
  });

  test("saving while a resized count is said is refused, until it is counted again", async () => {
    const { sent, count } = await resizedWhileCounted();
    save();
    // Saving the rest would record less than was moved.
    expect(screen.getByText("LiteBeam 5AC: enter the count").tagName).toBe(
      "STRONG",
    );
    expect(screen.getByText(/since this item was added/)).toBeDefined();
    fireEvent.input(count, { target: { value: "1" } });
    await vi.advanceTimersByTimeAsync(1000);
    expect(saves(sent)).toEqual([]);
    save();
    await screen.findByText(/^Recorded \(/);
    expect(saves(sent)).toHaveLength(1);
  });

  test("a retired name or place in a stored cart shows as not picked", async () => {
    storeCart({ ...freshCart(), volunteerId: 99, toId: 98 });
    open();
    await ready();
    expect(selected(say(YOUR_NAME))).toBe("");
    expect(selected(say(TO))).toBe("");
  });
});
