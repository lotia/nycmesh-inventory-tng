import { fireEvent, screen } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { telemetry } from "../shared/telemetry";
import {
  find,
  open,
  pick,
  ready,
  save,
  saves,
  server,
  storedCart,
} from "../testing/scan-page";
import { DISCARD_CART, YOUR_NAME } from "./CartForm";
import { REMOVE } from "./CartLine";
import {
  STORAGE_KEY as CART_KEY,
  type Cart,
  freshCart,
  storeCart,
} from "./cart";
import { DISCARD } from "./Notes";
import { NO_LONGER_IN_CATALOGUE } from "./older-to-cart";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

/** A move by Ada, from the hub to the van, of a radio counted in boxes
 * and loose, a cable, and an item the catalogue no longer has. */
const FILLED: Cart = {
  ...freshCart(),
  volunteerId: 1,
  fromId: 10,
  toId: 12,
  lines: [
    {
      itemId: 100,
      packages: { 1000: "2" },
      sizes: { 1000: "4.000" },
      loose: "1",
      typed: true,
    },
    { itemId: 101, packages: {}, loose: "3.5", picked: 2 },
    { itemId: 99, packages: {}, loose: "1" },
  ],
  usedAt: Date.now(),
  startedAt: 1_000,
  revision: "filled",
  batchKey: "00000000-0000-4000-8000-000000000001",
};

/** The cart filled, and the page open on it. */
async function opened(cart: Cart = FILLED) {
  storeCart(cart);
  const { api, sent } = server();
  open(api);
  await ready();
  return sent;
}

const removeButtons = () =>
  screen.getAllByRole("button", { name: say(REMOVE) });
const removeAt = (at: number) =>
  fireEvent.click(removeButtons()[at] as Element);
const undoButton = () => screen.queryByRole("button", { name: "Undo" });
const removedEvents = (record: { mock: { calls: unknown[][] } }) =>
  record.mock.calls
    .map(([event]) => (event as { kind: string }).kind)
    .filter((kind) => kind === "line_removed" || kind === "removal_undone");

describe("Undo after Remove (Ali, inventory-tng-5uy, 2026-11-13)", () => {
  test("Remove says what went, Undo focused; Undo puts it back where it was, as it was, Remove focused", async () => {
    const record = vi.spyOn(telemetry, "record");
    await opened();
    removeAt(1);
    expect(screen.queryByText("3.5 m in all")).toBeNull();
    const notice = screen.getByRole("status");
    // One message, the name in bold as on its line, Undo a button in it.
    expect(notice.textContent).toBe("Removed Cat6 cable. Undo");
    expect(notice.querySelector("strong")?.textContent).toBe("Cat6 cable");
    expect(document.activeElement).toBe(undoButton());
    expect(
      storedCart().lines.map((line: { itemId: number }) => line.itemId),
    ).toEqual([100, 99]);

    fireEvent.click(undoButton() as Element);
    expect(undoButton()).toBeNull();
    expect(screen.queryByRole("status")).toBeNull();
    // Where it was, with what it held, under the same key.
    const stored = storedCart();
    expect(stored.lines).toEqual(FILLED.lines);
    expect(stored.batchKey).toBe(FILLED.batchKey);
    screen.getByText("3.5 m in all");
    expect(document.activeElement).toBe(removeButtons()[1]);
    expect(removedEvents(record)).toEqual(["line_removed", "removal_undone"]);
  });

  test("the last line removed and put back keeps the cart's key and when it was started", async () => {
    const only = { ...FILLED, lines: FILLED.lines.slice(0, 1) };
    await opened(only);
    removeAt(0);
    expect(storedCart().lines).toEqual([]);
    expect(storedCart()).not.toHaveProperty("batchKey");
    fireEvent.click(undoButton() as Element);
    const stored = storedCart();
    expect(stored.lines).toEqual(only.lines);
    expect(stored.batchKey).toBe(FILLED.batchKey);
    expect(stored.startedAt).toBe(FILLED.startedAt);
  });

  test("a line whose item has gone is named as gone", async () => {
    await opened();
    removeAt(2);
    expect(screen.getByRole("status").textContent).toBe(
      `Removed ${say(NO_LONGER_IN_CATALOGUE)}. Undo`,
    );
  });

  test.each<[string, () => void]>([
    [
      "an item added",
      () => {
        find("litebeam");
        fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
      },
    ],
    [
      "a line changed",
      () =>
        fireEvent.input(screen.getByLabelText("Loose units"), {
          target: { value: "5" },
        }),
    ],
    ["another line removed", () => removeAt(0)],
    ["the name changed", () => pick(say(YOUR_NAME), "2")],
    [
      "the cart discarded",
      () => {
        fireEvent.click(
          screen.getByRole("button", { name: say(DISCARD_CART) }),
        );
        fireEvent.click(screen.getByRole("button", { name: say(DISCARD) }));
      },
    ],
    ["the cart recorded", save],
    [
      "the cart changed in another tab",
      () => {
        storeCart({ ...FILLED, revision: "theirs" });
        window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
      },
    ],
  ])("%s drops the Undo", async (_, change) => {
    const record = vi.spyOn(telemetry, "record");
    await opened();
    // The line whose item has gone, so the rest can be recorded.
    removeAt(2);
    const offered = () =>
      screen.queryByRole("status")?.textContent ===
      `Removed ${say(NO_LONGER_IN_CATALOGUE)}. Undo`;
    expect(offered()).toBe(true);
    change();
    await vi.waitFor(() => expect(offered()).toBe(false));
    expect(removedEvents(record)).not.toContain("removal_undone");
  });

  test("Undo after another tab changed the cart, not yet heard, puts nothing back", async () => {
    await opened();
    removeAt(1);
    const theirs = { ...FILLED, lines: [], revision: "theirs" };
    storeCart(theirs);
    fireEvent.click(undoButton() as Element);
    expect(undoButton()).toBeNull();
    expect(storedCart().lines).toEqual([]);
  });

  test("a reload offers no Undo", async () => {
    const sent = await opened();
    removeAt(1);
    document.body.innerHTML = "";
    open();
    await ready();
    expect(undoButton()).toBeNull();
    expect(saves(sent)).toHaveLength(0);
  });
});
