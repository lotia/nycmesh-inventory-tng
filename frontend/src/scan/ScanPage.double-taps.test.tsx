/**
 * The scan page's taps that take away what is above other controls: each
 * leaves taps ignored a moment, so a double tap's second tap does nothing,
 * and moves focus, without scrolling, to a target an ignored tap puts it
 * back on (Ali, 2026-11-27, inventory-tng-r3jx). The lists' own waits are
 * in ScanPage.older-build-list and ScanPage.outbox, the name check's and a
 * place question's kept space in ScanPage.names and ScanPage.camera.
 */
import { fireEvent, screen } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import type { Outbox } from "../shared/outbox";
import { SETTLE_MS } from "../shared/settling";
import { CATALOGUE } from "../testing/catalogue";
import { answering, json } from "../testing/fetch";
import {
  fillMove,
  find,
  open,
  ready,
  saveQueued,
  server,
  storedCart,
  tap,
} from "../testing/scan-page";
import { CART, DISCARD_CART, RECORD } from "./CartForm";
import { REMOVE } from "./CartLine";
import { type Cart, freshCart, KIND_WORDS, storeCart } from "./cart";
import { DISCARD, KEEP_IT } from "./Notes";
import { COULD_NOT_LOAD, SCAN, TRY_AGAIN } from "./ScanPage";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

/** A move by Ada, from the hub to the van, of a radio and a cable. */
const FILLED: Cart = {
  ...freshCart(),
  volunteerId: 1,
  fromId: 10,
  toId: 12,
  lines: [
    { itemId: 100, packages: {}, loose: "1" },
    { itemId: 101, packages: {}, loose: "3.5", unit: "m" },
  ],
  usedAt: Date.now(),
  revision: "filled",
};

const removeButtons = () =>
  screen.getAllByRole("button", { name: say(REMOVE) });
const lineCount = () => storedCart().lines.length;
const button = (words: string) => screen.getByRole("button", { name: words });
const cartHeading = () => screen.getByRole("heading", { name: say(CART) });

/**
 * That focus is on `focused()`, moved there without scrolling; that the
 * second tap of a double tap, on `second()`, changes nothing `seen()`
 * shows and puts focus back there; that a key's press on it changes
 * nothing either and leaves focus on it; and that a tap a moment later
 * is taken, changing what `seen()` shows.
 */
async function expectSecondTapIgnored({
  focused,
  second,
  seen,
  focus,
}: {
  focused: () => Element | null;
  second: () => HTMLElement;
  seen: () => unknown;
  focus: { mock: { calls: unknown[][] } };
}) {
  await vi.waitFor(() => expect(document.activeElement).toBe(focused()));
  // Every focus the page moved went without scrolling.
  expect(focus.mock.calls).not.toHaveLength(0);
  for (const call of focus.mock.calls) {
    expect(call).toEqual([{ preventScroll: true }]);
  }
  const before = seen();
  second().focus();
  fireEvent.click(second(), { detail: 1 });
  expect(seen()).toEqual(before);
  expect(document.activeElement).toBe(focused());
  // Pressed by a key: ignored, focus left where the volunteer moved it.
  second().focus();
  fireEvent.click(second(), { detail: 0 });
  expect(seen()).toEqual(before);
  expect(document.activeElement).toBe(second());
  await vi.advanceTimersByTimeAsync(SETTLE_MS);
  fireEvent.click(second(), { detail: 1 });
  expect(seen()).not.toEqual(before);
}

describe("a tap that takes away what is above other controls (Ali, 2026-11-27, inventory-tng-r3jx)", () => {
  let focus: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    focus = vi.spyOn(HTMLElement.prototype, "focus");
  });
  afterEach(() => {
    focus.mockRestore();
  });
  /** The page open on `cart`, its own focus calls not counted. */
  async function opened(cart: Cart = FILLED) {
    storeCart(cart);
    open();
    await ready();
    focus.mockClear();
  }

  test("Remove: the line below moves up, and Undo has focus", async () => {
    await opened();
    tap(removeButtons()[0] as HTMLElement);
    await expectSecondTapIgnored({
      focused: () => screen.queryByRole("button", { name: "Undo" }),
      second: () => removeButtons()[0] as HTMLElement,
      seen: lineCount,
      focus,
    });
  });

  test("Undo: its offer goes, and the line put back has focus on its Remove", async () => {
    await opened();
    tap(removeButtons()[0] as HTMLElement);
    await vi.advanceTimersByTimeAsync(SETTLE_MS);
    focus.mockClear();
    tap(button("Undo"));
    await expectSecondTapIgnored({
      focused: () => removeButtons()[0] as HTMLElement,
      second: () => removeButtons()[1] as HTMLElement,
      seen: lineCount,
      focus,
    });
  });

  test("a tap that changes the cart and takes Undo's offer away, the kind's radio here", async () => {
    await opened();
    tap(removeButtons()[0] as HTMLElement);
    await vi.advanceTimersByTimeAsync(SETTLE_MS);
    const radio = screen.getByLabelText(say(KIND_WORDS.install));
    radio.focus();
    focus.mockClear();
    tap(radio);
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    // Focus stays on the radio, as the page did not move it; an ignored
    // tap puts it back there.
    const before = lineCount();
    const remove = removeButtons()[0] as HTMLElement;
    remove.focus();
    fireEvent.click(remove, { detail: 1 });
    expect(lineCount()).toBe(before);
    expect(document.activeElement).toBe(radio);
    await vi.advanceTimersByTimeAsync(SETTLE_MS);
    fireEvent.click(removeButtons()[0] as HTMLElement, { detail: 1 });
    expect(lineCount()).toBe(before - 1);
  });

  test("typed in a box, a change that takes Undo's offer away leaves taps taken: no finger is on the page", async () => {
    await opened();
    tap(removeButtons()[0] as HTMLElement);
    await vi.advanceTimersByTimeAsync(SETTLE_MS);
    const loose = screen.getByLabelText<HTMLInputElement>(/^Loose|^How many/);
    loose.focus();
    fireEvent.input(loose, { target: { value: "4" } });
    expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
    fireEvent.click(removeButtons()[0] as HTMLElement, { detail: 1 });
    expect(lineCount()).toBe(0);
  });

  test("Discard cart, confirmed: the lines go, and the Cart heading has focus", async () => {
    await opened();
    tap(button(say(DISCARD_CART)));
    focus.mockClear();
    tap(button(say(DISCARD)));
    // Record, moved up under the finger, saying nothing is in the cart.
    await expectSecondTapIgnored({
      focused: cartHeading,
      second: () => button(say(RECORD)),
      seen: () => screen.queryAllByRole("alert").length,
      focus,
    });
  });

  test("Keep it: the question goes, and Discard cart has focus", async () => {
    await opened();
    tap(button(say(DISCARD_CART)));
    focus.mockClear();
    tap(button(say(KEEP_IT)));
    await expectSecondTapIgnored({
      focused: () => button(say(DISCARD_CART)),
      second: () => button(say(DISCARD_CART)),
      seen: () => screen.queryByRole("button", { name: say(KEEP_IT) }),
      focus,
    });
  });

  test("Record: the lines go once it is queued, and the Cart heading has focus", async () => {
    const { api } = server();
    const outbox: Outbox = open(api);
    await ready();
    fillMove();
    focus.mockClear();
    await saveQueued(outbox, 1);
    await expectSecondTapIgnored({
      focused: cartHeading,
      second: () => button(say(RECORD)),
      seen: () => screen.queryAllByRole("alert").length,
      focus,
    });
  });

  test("an item picked in the search: the matches go, and the Cart heading has focus, not the search box's keyboard", async () => {
    await opened();
    find("cable");
    focus.mockClear();
    tap(button("Cat6 cable"));
    await expectSecondTapIgnored({
      focused: cartHeading,
      second: () => removeButtons()[0] as HTMLElement,
      seen: lineCount,
      focus,
    });
  });

  test("Try again: the alert gives way to Loading, and the page's heading has focus", async () => {
    let first = true;
    const { api } = answering(() => {
      if (first) {
        first = false;
        return json({ reason: "database_unavailable" }, 503);
      }
      return json(CATALOGUE, 200);
    });
    open(api);
    await screen.findByText(say(COULD_NOT_LOAD));
    focus.mockClear();
    tap(button(say(TRY_AGAIN)));
    const heading = screen.getByRole("heading", { name: say(SCAN) });
    expect(document.activeElement).toBe(heading);
    expect(focus.mock.calls).toEqual([[{ preventScroll: true }]]);
    // Whatever moved up under the finger: its tap is ignored (cancelled),
    // focus back on the heading.
    heading.blur();
    expect(fireEvent.click(document.body, { detail: 1 })).toBe(false);
    expect(document.activeElement).toBe(heading);
    await ready();
  });
});
