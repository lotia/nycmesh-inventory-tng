import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { REMOVE } from "./CartLine";
import {
  addOne,
  STORAGE_KEY as CART_KEY,
  CartStore,
  freshCart,
  storeCart,
} from "./cart";
import { say } from "./i18n";
import { CONFIRM_DISCARD, DISCARD, KEEP_IT } from "./Notes";
import {
  ADD_TO_CART,
  CANNOT_ADD,
  CART_NOT_EMPTY,
  CHANGED_CHECK_AGAIN,
  CHANGED_ELSEWHERE,
  CHANGED_NOT_DISCARDED,
  CHECKED_OUT,
  FIX_THEN_RECORD,
  NOT_DISCARDED as NOT_DISCARDED_PHRASE,
  NOT_KEPT as NOT_KEPT_PHRASE,
  SOMETHING,
} from "./OlderList";
import { CARTS_FROM_DEVICE, DISMISS } from "./OutboxList";
import { ITEM_RETIRED, RESCAN as RESCAN_PHRASE } from "./older";
import {
  type Body,
  KEY_REUSED,
  MAKE_CHANGES,
  STORAGE_KEY as OUTBOX_KEY,
  SOME_NOT_RECORDED,
} from "./outbox";
import { ADD_DIFFERENCE, IN_CART } from "./recorded";
import {
  ADDED_BACK as ADDED_BACK_PHRASE,
  DISCARD_CART,
  FROM,
  INSTALL_NUMBER,
  NOT_REMOVED as NOT_REMOVED_PHRASE,
  NOTHING_IN_CART,
  TO,
  YOUR_NAME,
} from "./ScanPage";
import { telemetry } from "./telemetry";
import { CATALOGUE } from "./test-catalogue";
import { answering, deferred, json, saved, TOO_OLD } from "./test-fetch";
import {
  KEY,
  keep,
  moveBody,
  olderSave,
  outboxEntry,
  storeOutbox,
} from "./test-saves";
import {
  expectBold,
  expectRefusal,
  fillMove,
  isCatalogue,
  open,
  ready,
  save,
  saves,
  selected,
  server,
  storedCart,
  UUID,
} from "./test-scan-page";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

/** Storage refusing every write to `key`, until restored. */
function refusingWrites(key: string) {
  const real = localStorage.setItem.bind(localStorage);
  return vi.spyOn(localStorage, "setItem").mockImplementation((at, value) => {
    if (at === key) {
      throw new DOMException("full", "QuotaExceededError");
    }
    real(at, value);
  });
}

describe("what an older build left", () => {
  const HEADING = say(CHECKED_OUT);
  const ADDED_BACK = say(ADDED_BACK_PHRASE);
  const RETIRED = say(ITEM_RETIRED);
  // Failures, in bold (Ali, inventory-tng-93z).
  const NOT_KEPT = say(NOT_KEPT_PHRASE);
  const NOT_DISCARDED = say(NOT_DISCARDED_PHRASE);
  const NOT_REMOVED = say(NOT_REMOVED_PHRASE);
  const RESCAN = say(RESCAN_PHRASE);
  const AT = 1_700_000_000_000;
  const OLDER = "2026-11-12T09:00:00.000Z";
  const at = new Date(AT).toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });

  /** A move of two boxes of radios and some cable, waiting under v3. */
  function leaveSave(fields = {}) {
    keep("inventory.outbox.v3", [
      olderSave(
        moveBody({
          key: KEY,
          lines: [
            { item_id: 100, quantity: "8", package_id: 1000 },
            { item_id: 101, quantity: "3", open_package_key: KEY },
          ],
        }),
        { what: "Move: 2 items from Hub to Van", queuedAt: AT, ...fields },
      ),
    ]);
  }

  /** A cart filled for an install, under v1, with a line no build wrote:
   * not whole, so it waits for the volunteer. */
  function leaveCart(fields = {}) {
    keep("inventory.cart.v1", {
      ...freshCart(),
      kind: "install",
      volunteerId: 2,
      fromId: 12,
      installNumber: "456",
      lines: [{ itemId: 101, packages: {}, loose: "12.5" }, { loose: 1 }],
      usedAt: AT,
      ...fields,
    });
  }

  const listing = () =>
    screen
      .getByRole("heading", { name: HEADING })
      .closest("section") as HTMLElement;

  /** A note under a line, in the alert's style, which says `text`, its
   * bold words among the rest. */
  const noted =
    (text: string) =>
    (_: string, element: Element | null): boolean =>
      element?.className === "alert" && element.textContent === text;

  /** The Discard of a whole entry, `in` the listing's first by default,
   * after any of its lines' own; once tapped, the one confirming it. */
  const lastDiscard = (entry: HTMLElement = listing()) =>
    within(entry)
      .getAllByRole("button", { name: say(DISCARD) })
      .at(-1) as HTMLElement;
  /** The Discard of the listed line that says `words`. */
  const discardOf = (words: string) =>
    within(
      within(listing()).getByText(words).closest("li") as HTMLElement,
    ).getByRole("button", { name: say(DISCARD) });

  test("each is listed in words, and counted once as found", async () => {
    const record = vi.spyOn(telemetry, "record");
    leaveSave();
    leaveCart();
    open();
    await ready();
    const [save, cart] = listing().querySelector("ul")?.children ?? [];
    expect(save?.textContent).toContain("Moving stock from Hub to Van, by Ada");
    within(save as HTMLElement).getByText(
      `Kept on this device ${at}, not sent.`,
    );
    within(save as HTMLElement).getByText("LiteBeam 5AC: 2 × Box");
    // What cannot go back says why under it, in the alert's style, in
    // bold, and has its own Discard.
    const taken = within(save as HTMLElement)
      .getByText("Cat6 cable: 3 m from an open package")
      .closest("li") as HTMLElement;
    expectBold(within(taken).getByText(noted(RESCAN)), RESCAN);
    within(taken).getByRole("button", { name: say(DISCARD) });
    // What goes back has none.
    expect(
      within(
        within(save as HTMLElement)
          .getByText("LiteBeam 5AC: 2 × Box")
          .closest("li") as HTMLElement,
      ).queryByRole("button"),
    ).toBeNull();
    expect(cart?.textContent).toContain(
      "A cart: taking it to an install from Van for install 456, by Grace",
    );
    within(cart as HTMLElement).getByText(`Added on ${at} but not sent`);
    within(cart as HTMLElement).getByText("Cat6 cable: 12.5 m");
    expect(record.mock.calls.map(([event]) => event)).toEqual([
      { kind: "older_storage", held: "saves", action: "found" },
      { kind: "older_storage", held: "cart", action: "found" },
    ]);
    record.mockRestore();
  });

  test("each says the time it was kept, so two of one day differ", async () => {
    // Two saves and a cart of one day, by the device's clock (Ali,
    // 2026-11-16, inventory-tng-6fl).
    const morning = new Date(2026, 10, 14, 9, 30).getTime();
    const afternoon = new Date(2026, 10, 14, 14, 5).getTime();
    leaveSave({ queuedAt: morning });
    const [kept] = JSON.parse(
      localStorage.getItem("inventory.outbox.v3") ?? "[]",
    );
    keep("inventory.outbox.v3", [
      kept,
      {
        ...kept,
        body: { ...kept.body, key: "other" },
        queuedAt: afternoon,
      },
    ]);
    leaveCart({ usedAt: afternoon });
    open();
    await ready();
    const time = (at: number) =>
      new Date(at).toLocaleTimeString([], { timeStyle: "short" });
    const [first, second, cart] = [
      ...(listing().querySelector("ul")?.children ?? []),
    ].map((entry) => entry.querySelector("small")?.textContent ?? "");
    expect(first).toMatch(/^Kept on this device .+, not sent\.$/);
    expect(first).toContain(time(morning));
    expect(second).toContain(time(afternoon));
    expect(second).not.toBe(first);
    expect(cart).toMatch(/^Added on .+ but not sent$/);
    expect(cart).toContain(time(afternoon));
  });

  test("each kind, and what the catalogue no longer has, are told plainly", async () => {
    const save = (key: string, body: object, fields = {}) =>
      olderSave(
        { key, volunteer_id: 99, lines: [], ...body },
        { queuedAt: null, ...fields },
      );
    keep("inventory.outbox.v3", [
      save("a", { kind: "report", from_location_id: 10, used_up: [KEY] }),
      save("b", {
        kind: "open",
        from_location_id: 10,
        to_location_id: 10,
        lines: [{ item_id: 101, quantity: "305", package_id: 1001 }],
        // Not whole, so listed, not put in the cart.
        used_up: [KEY],
      }),
      save("c", {
        kind: "receipt",
        to_location_id: 98,
        lines: [
          { item_id: 100, quantity: "4", package_id: 1000 },
          { item_id: 100, quantity: "3" },
          { item_id: 101, quantity: "0" },
          { item_id: 999, quantity: "1" },
        ],
      }),
      save("d", { kind: "install", install_number: 77 }, { state: "refused" }),
    ]);
    open();
    await ready();
    const [report, opening, receipt, install] =
      listing().querySelector("ul")?.children ?? [];
    expect(report?.textContent).toContain(
      "Saying what is on open packages at HubKept on this device, not sent.",
    );
    // Nothing in it can go in the cart: discarding is all there is.
    expect(
      within(report as HTMLElement).queryByRole("button", {
        name: say(ADD_TO_CART),
      }),
    ).toBeNull();
    within(opening as HTMLElement).getByText("Cat6 cable: 1 × Spool");
    expect(receipt?.textContent).toContain(
      "Receiving stock to a place no longer in the catalogue",
    );
    within(receipt as HTMLElement).getByText("LiteBeam 5AC: 1 × Box, 3 loose");
    // Gone, it says how much (Ali, inventory-tng-93z).
    within(receipt as HTMLElement).getByText(
      "An item no longer in the catalogue: 1",
    );
    expect(install?.textContent).toContain(
      "Taking it to an install for install 77Add it to the cart, fix that, then record it.",
    );
  });

  test("a save of no kind this build knows is something saved", async () => {
    keep("inventory.outbox.v3", [
      olderSave({
        key: KEY,
        kind: "teleport",
        lines: [{ item_id: 102, quantity: "1" }],
      }),
    ]);
    open();
    await ready();
    within(listing()).getByText(say(SOMETHING));
  });

  test("a browser that will not keep the cart keeps the older one", async () => {
    leaveSave();
    open();
    await ready();
    const setItem = vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    setItem.mockRestore();
    expect(within(listing()).getByText(NOT_KEPT).tagName).toBe("STRONG");
    // Not filled either: saved from here, then put back, it would go twice.
    screen.getByText(say(NOTHING_IN_CART));
    expect(selected(say(FROM))).not.toBe("10");
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("a browser that will not forget a discarded one says so", async () => {
    leaveSave();
    const [first] = JSON.parse(
      localStorage.getItem("inventory.outbox.v3") ?? "",
    );
    keep("inventory.outbox.v3", [
      first,
      { ...first, body: { ...first.body, key: "other" } },
    ]);
    open();
    await ready();
    const setItem = vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    const [save] = within(listing()).getAllByRole("listitem");
    fireEvent.click(lastDiscard(save as HTMLElement));
    fireEvent.click(lastDiscard(save as HTMLElement));
    setItem.mockRestore();
    expect(within(listing()).getByText(NOT_DISCARDED).tagName).toBe("STRONG");
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v3") ?? ""),
    ).toHaveLength(2);
  });

  test("a browser that will not forget the older one says so", async () => {
    leaveSave();
    const [first] = JSON.parse(
      localStorage.getItem("inventory.outbox.v3") ?? "",
    );
    keep("inventory.outbox.v3", [
      first,
      { ...first, body: { ...first.body, key: "other" } },
    ]);
    open();
    await ready();
    const real = localStorage.setItem.bind(localStorage);
    const setItem = vi
      .spyOn(localStorage, "setItem")
      .mockImplementation((key, value) => {
        if (key === "inventory.outbox.v3") {
          throw new DOMException("full", "QuotaExceededError");
        }
        real(key, value);
      });
    fireEvent.click(
      within(listing()).getAllByRole("button", {
        name: say(ADD_TO_CART),
      })[0] as HTMLElement,
    );
    setItem.mockRestore();
    expect(screen.getByText(NOT_REMOVED).tagName).toBe("STRONG");
    expect(selected(say(FROM))).toBe("10");
  });

  test("an older cart discarded goes", async () => {
    const record = vi.spyOn(telemetry, "record");
    leaveCart();
    open();
    await ready();
    fireEvent.click(lastDiscard());
    fireEvent.click(lastDiscard());
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
    expect(record).toHaveBeenCalledWith({
      kind: "older_storage",
      held: "cart",
      action: "discarded",
    });
    record.mockRestore();
  });

  test("one gone meanwhile, discarded, just goes from the list", async () => {
    leaveSave();
    open();
    await ready();
    fireEvent.click(lastDiscard());
    // Sent by an older tab, the storage event not yet heard.
    localStorage.removeItem("inventory.outbox.v3");
    fireEvent.click(lastDiscard());
    expect(screen.queryByText(HEADING)).toBeNull();
  });

  test("a cart's line with nothing readable in it is not told", async () => {
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [
        { itemId: 100, packages: { 1000: "some" }, loose: "lots" },
        { itemId: 101, packages: {}, loose: "2" },
        // Not whole, so listed, not put in the cart.
        { loose: 1 },
      ],
    });
    open();
    await ready();
    expect(within(listing()).queryByText(/LiteBeam/)).toBeNull();
    within(listing()).getByText("Cat6 cable: 2 m");
  });

  test("one an older build left under this build's key, held back as too old, goes back in the empty cart, and this tab is not too old", async () => {
    storeOutbox([outboxEntry({ build: OLDER, queuedAt: AT })], OLDER);
    // A server whose oldest build is after OLDER.
    const { api } = server(() => json(TOO_OLD, 409));
    const outbox = open(api);
    await ready();
    await screen.findByText(ADDED_BACK);
    expect(outbox.builds.newer.value).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(say(CARTS_FROM_DEVICE))).toBeNull();
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(
      (screen.getByLabelText("Box (4 each)") as HTMLInputElement).value,
    ).toBe("2");
    // Gone from the outbox, whose key stays.
    expect(JSON.parse(localStorage.getItem(OUTBOX_KEY) ?? "").entries).toEqual(
      [],
    );
  });

  test("one another tab held back as too old is listed here, and goes when put back there", async () => {
    open();
    await ready();
    // A cart with lines takes none without a tap.
    fillMove();
    expect(screen.queryByText(HEADING)).toBeNull();
    storeOutbox(
      [outboxEntry({ build: OLDER, state: "too_old", queuedAt: AT })],
      OLDER,
    );
    window.dispatchEvent(new StorageEvent("storage", { key: OUTBOX_KEY }));
    await vi.waitFor(() =>
      within(listing()).getByText(`Kept on this device ${at}, not sent.`),
    );
    // Put back in the cart there.
    storeOutbox([]);
    window.dispatchEvent(new StorageEvent("storage", { key: OUTBOX_KEY }));
    await vi.waitFor(() => expect(screen.queryByText(HEADING)).toBeNull());
  });

  test("a refused one says why", async () => {
    leaveSave({ state: "refused", note: "Not saved. Line 1: no." });
    open();
    await ready();
    // In the alert's style: "Not saved." in bold, the rest not, its
    // numbers in italic (Ali, inventory-tng-93z).
    const why = within(listing()).getByText(
      noted(
        "Not saved. Line 1: no. Add it to the cart, fix that, then record it.",
      ),
    );
    expectRefusal(why, "Not saved.", ["1"]);
    // What to do, in bold as after any other refusal (row 11).
    expect(
      [...why.querySelectorAll("strong")].map((each) => each.textContent),
    ).toEqual(["Not saved.", say(FIX_THEN_RECORD)]);
  });

  test("put in the cart, it fills the cart, goes from the list, and saves as any cart", async () => {
    const record = vi.spyOn(telemetry, "record");
    leaveSave();
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
    screen.getByText(ADDED_BACK);
    // Each note left behind, naming its item (Ali, inventory-tng-0su).
    screen.getByText(`Cat6 cable: ${RESCAN}`);
    expect(selected(say(YOUR_NAME))).toBe("1");
    expect(selected(say(FROM))).toBe("10");
    expect(selected(say(TO))).toBe("12");
    expect(
      (screen.getByLabelText("Box (4 each)") as HTMLInputElement).value,
    ).toBe("2");
    expect(record).toHaveBeenCalledWith({
      kind: "older_storage",
      held: "saves",
      action: "put_in_cart",
    });
    record.mockRestore();
    // The cable taken from an open package is left out, not put back as
    // its item's empty line.
    expect(screen.queryByText("Cat6 cable")).toBeNull();
    save();
    await screen.findByText(/^Sending: /);
    await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
    expect(outbox.entries.value[0]?.body).toMatchObject({
      kind: "move",
      from_location_id: 10,
      to_location_id: 12,
      lines: [{ item_id: 100, package_id: 1000, quantity: "8" }],
    });
    // Under its key, which an older build's tab may have saved meanwhile.
    expect(outbox.entries.value[0]?.body.key).toBe(KEY);
    expect(outbox.entries.value[0]?.putBack).toBe(true);
  });

  test("one that only took from an open package has nothing to put back: its line, discarded, takes it", async () => {
    const record = vi.spyOn(telemetry, "record");
    leaveSave({
      body: {
        key: KEY,
        kind: "move",
        volunteer_id: 1,
        from_location_id: 10,
        to_location_id: 12,
        lines: [{ item_id: 101, quantity: "3", open_package_key: KEY }],
      },
    });
    open();
    await ready();
    within(listing()).getByText(noted(RESCAN));
    expect(
      within(listing()).queryByRole("button", { name: say(ADD_TO_CART) }),
    ).toBeNull();
    const [line, entry] = within(listing()).getAllByRole("button", {
      name: say(DISCARD),
    });
    expect(line?.getAttribute("aria-describedby")).toBe("older-0-0");
    expect(screen.getByText("Cat6 cable: 3 m from an open package").id).toBe(
      "older-0-0",
    );
    expect(entry?.getAttribute("aria-describedby")).toBe("older-0");
    // Without asking: one line goes.
    fireEvent.click(line as HTMLElement);
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
    expect(record).toHaveBeenLastCalledWith({
      kind: "older_storage",
      held: "saves",
      action: "line_discarded",
    });
    record.mockRestore();
  });

  test("a line discarded goes alone, and the rest, whole, goes in the empty cart, under its key", async () => {
    const record = vi.spyOn(telemetry, "record");
    leaveSave();
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    fireEvent.click(discardOf("Cat6 cable: 3 m from an open package"));
    // What is left goes back whole, so in the empty cart without a tap,
    // with row 16's status and nothing left out to say (Ali,
    // inventory-tng-0su, question 10).
    await screen.findByText(ADDED_BACK);
    expect(screen.getByRole("status").textContent).toBe(ADDED_BACK);
    expect(record).toHaveBeenCalledWith({
      kind: "older_storage",
      held: "saves",
      action: "put_in_cart_automatically",
    });
    record.mockRestore();
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(boxes()).toBe("2");
    save();
    await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
    expect(outbox.entries.value[0]?.body.key).toBe(KEY);
  });

  test("a line's Discard, once it has gone, does not become the next line's", async () => {
    leaveSave({
      body: {
        key: KEY,
        kind: "move",
        volunteer_id: 1,
        from_location_id: 10,
        to_location_id: 12,
        lines: [
          { item_id: 101, quantity: "3", open_package_key: KEY },
          { item_id: 101, quantity: "5", open_package_key: KEY },
        ],
      },
    });
    open();
    await ready();
    const first = discardOf("Cat6 cable: 3 m from an open package");
    first.focus();
    fireEvent.click(first);
    // Focus left on it, a second press would discard the next, unasked:
    // it goes to the entry's heading.
    await vi.waitFor(() => expect(document.activeElement?.id).toBe("older-0"));
    expect(document.activeElement).not.toBe(
      discardOf("Cat6 cable: 5 m from an open package"),
    );
  });

  test("just after a line's Discard, taps on its entry's buttons are ignored, so a double tap discards one line", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    leaveSave({
      body: {
        key: KEY,
        kind: "move",
        volunteer_id: 1,
        from_location_id: 10,
        to_location_id: 12,
        lines: [
          { item_id: 101, quantity: "3", open_package_key: KEY },
          { item_id: 101, quantity: "5", open_package_key: KEY },
          { item_id: 101, quantity: "7", open_package_key: KEY },
          { item_id: 100, quantity: "8", package_id: 1000 },
        ],
      },
    });
    open();
    await ready();
    fireEvent.click(discardOf("Cat6 cable: 3 m from an open package"));
    // The second tap of a double tap, on what moved up in its place.
    fireEvent.click(discardOf("Cat6 cable: 5 m from an open package"));
    within(listing()).getByText("Cat6 cable: 5 m from an open package");
    fireEvent.click(lastDiscard());
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    expect(within(listing()).queryByText(say(CONFIRM_DISCARD))).toBeNull();
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
    await vi.advanceTimersByTimeAsync(500);
    fireEvent.click(lastDiscard());
    within(listing()).getByText(say(CONFIRM_DISCARD));
    // Asked, a line discarded meanwhile holds the answer back too.
    fireEvent.click(discardOf("Cat6 cable: 5 m from an open package"));
    fireEvent.click(lastDiscard());
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(KEEP_IT) }),
    );
    within(listing()).getByText(say(CONFIRM_DISCARD));
    await vi.advanceTimersByTimeAsync(500);
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(KEEP_IT) }),
    );
    expect(within(listing()).queryByText(say(CONFIRM_DISCARD))).toBeNull();
  });

  test("a cart's line of an item no longer in the catalogue says so, and can be removed", async () => {
    storeCart({
      ...freshCart(),
      volunteerId: 1,
      lines: [{ itemId: 999, packages: {}, loose: "1", typed: true }],
    });
    open();
    await ready();
    fireEvent.click(
      within(
        screen
          .getByText(/^No longer in the catalogue\./)
          .closest("li") as HTMLElement,
      ).getByRole("button", { name: say(REMOVE) }),
    );
    expect(screen.queryByText(/^No longer in the catalogue\./)).toBeNull();
  });

  test("one that could not read some lines stays, saying so, once its last line listed is discarded, until it is discarded itself", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    leaveSave({
      body: {
        key: KEY,
        kind: "move",
        volunteer_id: 1,
        from_location_id: 10,
        to_location_id: 12,
        lines: [
          { item_id: 101, quantity: "3", open_package_key: KEY },
          { item_id: "lots" },
        ],
      },
    });
    open();
    await ready();
    const unread = "Could not read 1 previously added item, scan it again.";
    expectBold(within(listing()).getByText(noted(unread)), unread, ["1"]);
    fireEvent.click(discardOf("Cat6 cable: 3 m from an open package"));
    expect(
      within(listing()).queryByText("Cat6 cable: 3 m from an open package"),
    ).toBeNull();
    within(listing()).getByText(noted(unread));
    await vi.waitFor(() => expect(document.activeElement?.id).toBe("older-0"));
    // Nothing left to put back: only its own Discard.
    expect(
      within(listing())
        .getAllByRole("button")
        .map((each) => each.textContent),
    ).toEqual([say(DISCARD)]);
    await vi.advanceTimersByTimeAsync(500);
    fireEvent.click(lastDiscard());
    fireEvent.click(lastDiscard());
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
  });

  test("a line the browser will not let go of says so, and stays", async () => {
    leaveSave();
    open();
    await ready();
    const setItem = vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    fireEvent.click(discardOf("Cat6 cable: 3 m from an open package"));
    setItem.mockRestore();
    within(listing()).getByText(NOT_DISCARDED);
    within(listing()).getByText(noted(RESCAN));
  });

  test("a line of one changed meanwhile, by an older tab, is not discarded, says so, and the list shows it as it is", async () => {
    leaveSave();
    open();
    await ready();
    const [first] = JSON.parse(
      localStorage.getItem("inventory.outbox.v3") ?? "",
    );
    keep("inventory.outbox.v3", [{ ...first, state: "refused", note: "No." }]);
    fireEvent.click(discardOf("Cat6 cable: 3 m from an open package"));
    expect(within(listing()).queryByText(NOT_DISCARDED)).toBeNull();
    expect(
      within(listing()).getByText(say(CHANGED_NOT_DISCARDED)).tagName,
    ).toBe("STRONG");
    within(listing()).getByText(
      noted("No. Add it to the cart, fix that, then record it."),
    );
    within(listing()).getByText(noted(RESCAN));
  });

  test("what cannot go back left out, the rest saves as it is", async () => {
    leaveSave();
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    save();
    await screen.findByText(/^Sending: /);
    await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
    expect(outbox.entries.value[0]?.body.lines).toEqual([
      { item_id: 100, package_id: 1000, package_size: "4", quantity: "8" },
    ]);
  });

  test("an older cart put in the cart fills it", async () => {
    leaveCart();
    open();
    await ready();
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
    expect(selected(say(YOUR_NAME))).toBe("2");
    expect(
      (screen.getByLabelText(say(INSTALL_NUMBER)) as HTMLInputElement).value,
    ).toBe("456");
  });

  test("with the cart filled, it asks for it to be saved first, and keeps the older one", async () => {
    leaveSave();
    open();
    await ready();
    fillMove();
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    expect(within(listing()).getByText(say(CART_NOT_EMPTY)).tagName).toBe(
      "STRONG",
    );
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("discarding asks first, on the page; kept, nothing goes", async () => {
    // jsdom has none: one stubbed, to prove it is never asked.
    const confirm = vi.fn();
    vi.stubGlobal("confirm", confirm);
    const record = vi.spyOn(telemetry, "record");
    leaveSave();
    open();
    await ready();
    fireEvent.click(lastDiscard());
    within(listing()).getByText(say(CONFIRM_DISCARD));
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(KEEP_IT) }),
    );
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
    fireEvent.click(lastDiscard());
    fireEvent.click(lastDiscard());
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(confirm).not.toHaveBeenCalled();
    expect(record).toHaveBeenCalledWith({
      kind: "older_storage",
      held: "saves",
      action: "discarded",
    });
    record.mockRestore();
    vi.unstubAllGlobals();
  });

  test("one an older tab sends meanwhile goes from the list, and is not put in the cart", async () => {
    leaveSave();
    open();
    await ready();
    const button = within(listing()).getByRole("button", {
      name: say(ADD_TO_CART),
    });
    // Sent, but the storage event not yet heard.
    const sent = JSON.parse(localStorage.getItem("inventory.outbox.v3") ?? "");
    sent[0].state = "saved";
    keep("inventory.outbox.v3", sent);
    fireEvent.click(button);
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(screen.getByText(say(NOTHING_IN_CART))).not.toBeNull();
  });

  test("one an older tab refused for its key meanwhile is not put in the cart under that key", async () => {
    leaveSave();
    open();
    await ready();
    const button = within(listing()).getByRole("button", {
      name: say(ADD_TO_CART),
    });
    // Refused for its key, which the inventory holds for another batch,
    // but the storage event not yet heard: put back under it, it would
    // be refused too, and never recorded.
    const kept = JSON.parse(localStorage.getItem("inventory.outbox.v3") ?? "");
    // In main's words, as its tab keeps them, said as kept.
    const words =
      "Not saved: its key was already used for a different batch. Please report this.";
    kept[0] = { ...kept[0], state: "refused", note: words };
    keep("inventory.outbox.v3", kept);
    fireEvent.click(button);
    screen.getByText(say(NOTHING_IN_CART));
    expect(localStorage.getItem(CART_KEY)).toBeNull();
    // Listed as it is now, it goes back without its key.
    within(listing()).getByText(
      noted(`${words} Add it to the cart, fix that, then record it.`),
    );
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    expect(boxes()).toBe("2");
    // Under a key of its own, made as it went in.
    expect(storedCart().batchKey).toMatch(UUID);
    expect(storedCart().batchKey).not.toBe(KEY);
  });

  test("one an older tab changed meanwhile is not put in the cart, and says so, bold whole, under it as it is now", async () => {
    leaveSave();
    open();
    await ready();
    const button = within(listing()).getByRole("button", {
      name: say(ADD_TO_CART),
    });
    // Refused there, but the storage event not yet heard.
    const kept = JSON.parse(localStorage.getItem("inventory.outbox.v3") ?? "");
    kept[0] = { ...kept[0], state: "refused", note: "No." };
    keep("inventory.outbox.v3", kept);
    fireEvent.click(button);
    screen.getByText(say(NOTHING_IN_CART));
    expect(within(listing()).getByText(say(CHANGED_ELSEWHERE)).tagName).toBe(
      "STRONG",
    );
    expect(within(listing()).queryByText(/has gone/)).toBeNull();
    within(listing()).getByText(
      noted("No. Add it to the cart, fix that, then record it."),
    );
    // Checked, it goes in.
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    expect(boxes()).toBe("2");
  });

  test.each([
    [
      "nothing of it goes back",
      [{ item_id: 101, quantity: "3", open_package_key: KEY }],
      say(ADD_TO_CART),
    ],
    [
      "it holds an item retired since",
      [
        { item_id: 100, quantity: "8", package_id: 1000 },
        { item_id: 103, quantity: "4", package_id: 1003 },
      ],
      say(CANNOT_ADD),
    ],
  ])(
    "one changed meanwhile so that %s says only to check it, bold whole",
    async (_, lines, gone) => {
      leaveSave();
      open();
      await ready();
      const button = within(listing()).getByRole("button", {
        name: say(ADD_TO_CART),
      });
      // Changed there, the storage event not yet heard.
      const kept = JSON.parse(
        localStorage.getItem("inventory.outbox.v3") ?? "",
      );
      kept[0] = { ...kept[0], body: { ...kept[0].body, lines } };
      keep("inventory.outbox.v3", kept);
      fireEvent.click(button);
      screen.getByText(say(NOTHING_IN_CART));
      expect(
        within(listing()).getByText(say(CHANGED_CHECK_AGAIN)).tagName,
      ).toBe("STRONG");
      expect(
        within(listing()).queryByText(/add it to the cart again/),
      ).toBeNull();
      expect(
        within(listing()).queryByRole("button", { name: say(ADD_TO_CART) }),
      ).toBeNull();
      if (gone !== say(ADD_TO_CART)) {
        within(listing()).getByRole("button", { name: gone });
      }
    },
  );

  test("an entry's Discard on one changed meanwhile discards nothing, and says so, bold whole", async () => {
    leaveSave();
    open();
    await ready();
    fireEvent.click(lastDiscard());
    // Refused there, the storage event not yet heard.
    const kept = JSON.parse(localStorage.getItem("inventory.outbox.v3") ?? "");
    kept[0] = { ...kept[0], state: "refused", note: "No." };
    keep("inventory.outbox.v3", kept);
    fireEvent.click(lastDiscard());
    expect(
      within(listing()).getByText(say(CHANGED_NOT_DISCARDED)).tagName,
    ).toBe("STRONG");
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v3") ?? ""),
    ).toEqual(kept);
    within(listing()).getByText(
      noted("No. Add it to the cart, fix that, then record it."),
    );
    // Checked, it goes.
    fireEvent.click(lastDiscard());
    fireEvent.click(lastDiscard());
    expect(screen.queryByText(HEADING)).toBeNull();
  });

  test("an older cart's Discard, changed meanwhile, discards nothing, and says so", async () => {
    leaveCart();
    open();
    await ready();
    fireEvent.click(lastDiscard());
    const kept = JSON.parse(localStorage.getItem("inventory.cart.v1") ?? "");
    const changed = { ...kept, installNumber: "789" };
    keep("inventory.cart.v1", changed);
    fireEvent.click(lastDiscard());
    within(listing()).getByText(say(CHANGED_NOT_DISCARDED));
    expect(JSON.parse(localStorage.getItem("inventory.cart.v1") ?? "")).toEqual(
      changed,
    );
  });

  test("a cart another tab filled meanwhile, unheard, is not written over", async () => {
    leaveSave();
    open();
    await ready();
    screen.getByText(say(NOTHING_IN_CART));
    const theirs = {
      ...freshCart(),
      lines: [{ itemId: 101, packages: {}, loose: "4" }],
      revision: "theirs",
    };
    storeCart(theirs);
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    expect(within(listing()).getByText(say(CART_NOT_EMPTY)).tagName).toBe(
      "STRONG",
    );
    expect(storedCart().lines).toEqual(theirs.lines);
    screen.getByText("4 m in all");
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("one that goes back whole waits, unsaid, while another tab has filled the cart unheard, then goes in once it is empty", async () => {
    open();
    await ready();
    storeCart({
      ...freshCart(),
      lines: [{ itemId: 101, packages: {}, loose: "4" }],
      revision: "theirs",
    });
    leaveMoves([KEY, AT, [radios(1)]]);
    window.dispatchEvent(
      new StorageEvent("storage", { key: "inventory.outbox.v3" }),
    );
    await screen.findByText("4 m in all");
    expect(screen.queryByRole("alert")).toBeNull();
    within(listing()).getByText("LiteBeam 5AC: 1 × Box");
    // Recorded there, the cart is empty, and it goes in.
    storeCart({ ...freshCart(), revision: "recorded" });
    window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
    await screen.findByText(ADDED_BACK);
    expect(boxes()).toBe("1");
  });

  test("nothing is removed without the volunteer acting, but empty keys", async () => {
    leaveSave();
    const left = localStorage.getItem("inventory.outbox.v3");
    keep("inventory.cart.v1", freshCart());
    open();
    await ready();
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
    fillMove();
    save();
    await screen.findByText(/^Sending: /);
    expect(localStorage.getItem("inventory.outbox.v3")).toBe(left);
  });

  test("put in the cart after a save, the cart no longer says it was saved", async () => {
    leaveSave();
    open();
    await ready();
    fillMove();
    save();
    await screen.findByText(/^Sending: /);
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    screen.getByText(ADDED_BACK);
    expect(screen.queryByText(/^Sending: /)).toBeNull();
  });

  /** Moves from the hub to the van under v3, each queued at its time. */
  function leaveMoves(
    ...saves: [key: string, at: number, lines: Body["lines"]][]
  ) {
    keep(
      "inventory.outbox.v3",
      saves.map(([key, queuedAt, lines]) =>
        olderSave(moveBody({ key, lines }), { queuedAt }),
      ),
    );
  }

  /** A line of `n` boxes of radios, which goes back whole. */
  const radios = (n: number) => ({
    item_id: 100,
    quantity: String(n * 4),
    package_id: 1000,
  });

  const boxes = () =>
    (screen.getByLabelText("Box (4 each)") as HTMLInputElement).value;

  test("a package no longer offered is told, listed and put back, and never saved as something else", async () => {
    // A save of a box retired since, with none sealed left (1004), and a
    // box still offered.
    leaveMoves([
      KEY,
      AT,
      [{ item_id: 100, quantity: "4", package_id: 1004 }, radios(1)],
    ]);
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    within(listing()).getByText(noted(RETIRED));
    within(listing()).getByText("LiteBeam 5AC: 1 × Box");
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    screen.getByText(`LiteBeam 5AC: ${RETIRED}`);
    expect(boxes()).toBe("1");
    save();
    await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
    // Not 4 loose.
    expect(outbox.entries.value[0]?.body.lines).toEqual([
      { item_id: 100, package_id: 1000, package_size: "4", quantity: "4" },
    ]);
  });

  test("an older cart's count of a package no longer offered is told when put back", async () => {
    keep("inventory.cart.v1", {
      ...freshCart(),
      volunteerId: 1,
      fromId: 10,
      toId: 12,
      lines: [{ itemId: 100, packages: { 1000: "1", 1004: "2" }, loose: "" }],
      usedAt: AT,
    });
    open();
    await ready();
    within(listing()).getByText(noted(RETIRED));
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    screen.getByText(`LiteBeam 5AC: ${RETIRED}`);
    expect(boxes()).toBe("1");
  });

  test("a package no longer offered is named from the catalogue, and never offered", async () => {
    // The radio's box 1004, retired with none sealed left, as the
    // catalogue names it (retired_packages).
    const named = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 100
          ? {
              ...item,
              retired_packages: [{ id: 1004, name: "Old box", size: "4.000" }],
            }
          : item,
      ),
    };
    leaveMoves([
      KEY,
      AT,
      [{ item_id: 100, quantity: "8", package_id: 1004 }, radios(1)],
    ]);
    keep("inventory.cart.v1", {
      ...freshCart(),
      lines: [{ itemId: 100, packages: { 1004: "3", 1009: "1" }, loose: "" }],
      usedAt: AT,
    });
    const { api } = answering((request) =>
      isCatalogue(request) ? json(named, 200) : saved(),
    );
    open(api);
    await ready();
    within(listing()).getByText("LiteBeam 5AC: 2 × Old box");
    within(listing()).getByText("LiteBeam 5AC: 3 × Old box");
    within(listing()).getByText("LiteBeam 5AC: 1 × a removed package or item");
    fireEvent.click(
      within(listing()).getAllByRole("button", {
        name: say(ADD_TO_CART),
      })[0] as HTMLElement,
    );
    expect(boxes()).toBe("1");
    // Named only: no count of it in the cart.
    expect(screen.queryByLabelText(/Old box/)).toBeNull();
  });

  test("one that goes back whole goes in an empty cart without a tap, the oldest first, one at a time", async () => {
    const record = vi.spyOn(telemetry, "record");
    leaveMoves(["newer", AT, [radios(3)]], ["older", AT - 1000, [radios(1)]]);
    open();
    await ready();
    await screen.findByText(ADDED_BACK);
    expect(boxes()).toBe("1");
    // The newer one waits, listed, for the cart to be saved.
    within(listing()).getByText("LiteBeam 5AC: 3 × Box");
    expect(record).toHaveBeenCalledWith({
      kind: "older_storage",
      held: "saves",
      action: "put_in_cart_automatically",
    });
    record.mockRestore();
    save();
    await vi.waitFor(() => expect(boxes()).toBe("3"));
    screen.getByText(ADDED_BACK);
    // The save just made is still said.
    screen.getByText(/^Sending: /);
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
  });

  /** What the ledger compares of a batch sent again under its key. */
  const linesOf = (body: { lines: object[] }) =>
    JSON.stringify(
      (
        body.lines as {
          item_id: number;
          quantity: string;
          package_id?: number;
        }[]
      ).map((line) => [
        line.item_id,
        Number(line.quantity),
        line.package_id ?? null,
      ]),
    );

  /** The batch endpoint as the ledger answers a key (stock/ledger.py, _replay):
   * the same lines again are the first transaction, others are refused.
   * `written` holds the lines saved under each key, in order. */
  function ledger(written: Map<string, string>) {
    return answering(async (request) => {
      if (isCatalogue(request)) {
        return json(CATALOGUE, 200);
      }
      const body = await request.clone().json();
      const first = written.get(body.key);
      if (first === undefined) {
        written.set(body.key, linesOf(body));
        return saved(written.size);
      }
      return first === linesOf(body)
        ? json(
            { transaction_id: [...written.keys()].indexOf(body.key) + 1 },
            200,
          )
        : json({ reason: "key_reused" }, 409);
    });
  }

  /** A move of one radio left, put back in the cart without a tap, which
   * the older build's tab, its request in flight as this page loaded,
   * saved first. */
  async function putBackSavedFirst() {
    leaveMoves([KEY, AT, [radios(1)]]);
    const written = new Map([[KEY, linesOf({ lines: [radios(1)] })]]);
    const { api, sent } = ledger(written);
    const outbox = open(api);
    await ready();
    await screen.findByText(ADDED_BACK);
    /** How many times the page has asked for the catalogue. */
    const catalogues = () => sent.filter(isCatalogue).length;
    return { api, outbox, written, catalogues };
  }

  /** Changed, saved, and refused as the older build's tab saved it first:
   * the refusal's note. */
  async function changedRefused() {
    fireEvent.input(screen.getByLabelText("Box (4 each)"), {
      target: { value: "2" },
    });
    save();
    return screen.findByText(say(SOME_NOT_RECORDED));
  }

  test("one an older build's tab saved meanwhile is saved once, under its key", async () => {
    const { outbox, written } = await putBackSavedFirst();
    save();
    await screen.findByText("Recorded (transaction 1).");
    expect(written.size).toBe(1);
    expect(outbox.entries.value[0]?.body.key).toBe(KEY);
  });

  test("one changed after it went back, which an older build's tab saved first, is not saved, and says so", async () => {
    const { outbox, written } = await putBackSavedFirst();
    await changedRefused();
    expect(written.size).toBe(1);
    // Listed, to save the changes again.
    expect(outbox.shown()[0]).toMatchObject({
      state: "refused",
      summary: { from: "Hub", to: "Van" },
    });
  });

  test("one changed after it went back, which an older build's tab saved first, says what is left to do, bold whole, with Dismiss at once, and refreshes the stock once", async () => {
    const { catalogues } = await putBackSavedFirst();
    const before = catalogues();
    const said = await changedRefused();
    // In bold, in the alert's style, one note, and no reload (Ali,
    // inventory-tng-g0e, d).
    expect(said.tagName).toBe("STRONG");
    const entry = said.closest("li") as HTMLElement;
    // From a server that says nothing of what it recorded, the heading
    // over its lines, to compare with what was recorded (Ali,
    // inventory-tng-0gg, 2026-11-15, 1).
    expect(within(entry).getByText(say(MAKE_CHANGES)).tagName).toBe("H3");
    within(entry).getByText("1. LiteBeam 5AC: 2 × Box");
    expect(within(entry).queryByRole("button", { name: "Reload" })).toBeNull();
    // The stock, asked for again at once, shows what the other tab
    // recorded; once, not again.
    await vi.waitFor(() => expect(catalogues()).toBe(before + 1));
    await new Promise((settled) => setTimeout(settled, 50));
    expect(catalogues()).toBe(before + 1);
    fireEvent.click(within(entry).getByRole("button", { name: say(DISMISS) }));
    expect(screen.queryByText(say(SOME_NOT_RECORDED))).toBeNull();
    expect(catalogues()).toBe(before + 1);
  });

  test("one another tab's send refused as recorded first is heard, and refreshes the stock once; refused before the page loaded, its first load shows it", async () => {
    await putBackSavedFirst();
    await changedRefused();
    const refused = localStorage.getItem(OUTBOX_KEY) ?? "";
    cleanup();
    // This tab, opened before the other tab's send was refused.
    const kept = JSON.parse(refused);
    keep(OUTBOX_KEY, { ...kept, entries: [] });
    const { api, sent } = server();
    open(api);
    await ready();
    const before = sent.filter(isCatalogue).length;
    localStorage.setItem(OUTBOX_KEY, refused);
    window.dispatchEvent(new StorageEvent("storage", { key: OUTBOX_KEY }));
    await screen.findByText(say(SOME_NOT_RECORDED));
    await vi.waitFor(() =>
      expect(sent.filter(isCatalogue).length).toBe(before + 1),
    );
    // Loaded again, it is already shown: no refresh beyond the load.
    cleanup();
    const again = server();
    open(again.api);
    await ready();
    await screen.findByText(say(SOME_NOT_RECORDED));
    await new Promise((settled) => setTimeout(settled, 50));
    expect(again.sent.filter(isCatalogue).length).toBe(1);
  });

  test("its note and Dismiss wait for nothing: not for the cart, nor a newer build", async () => {
    const { outbox } = await putBackSavedFirst();
    const entry = (await changedRefused()).closest("li") as HTMLElement;
    fillMove();
    act(() => outbox.builds.learn("other_tab"));
    within(entry).getByText(say(SOME_NOT_RECORDED));
    within(entry).getByRole("button", { name: say(DISMISS) });
  });

  /** The batch endpoint as a server since inventory-tng-0gg answers a
   * key whose first batch held `first`: the same again is that
   * transaction (1); another is refused, saying what was recorded, once
   * `held` lets it. */
  function recordingLedger(
    first: { quantity: string }[],
    held: Promise<unknown> = Promise.resolve(),
  ) {
    return answering(async (request) => {
      if (isCatalogue(request)) {
        return json(CATALOGUE, 200);
      }
      const body = await request.clone().json();
      if (body.key !== KEY) {
        return saved(2);
      }
      if (linesOf(body) === linesOf({ lines: first })) {
        return json({ transaction_id: 1 }, 200);
      }
      await held;
      const lines = first.map((line) => ({
        package_id: null,
        package_size: null,
        open_package_key: null,
        whole: null,
        ...line,
        quantity: `${line.quantity}.000`,
      }));
      const recorded = {
        transaction_id: 1,
        kind: "move",
        volunteer_id: 1,
        from_location_id: 10,
        to_location_id: 12,
        install_number: null,
        lines,
        used_up: [],
        lengths: [],
      };
      return json({ reason: "key_reused", recorded }, 409);
    });
  }

  const RECORDED_HEADING = "Recorded in another tab in transaction 1";

  /** `first` put back without a tap, saved first by the older build's
   * tab; then the cart's boxes of radios changed to `boxes` and recorded,
   * refused, once `held` lets it: its entry, once it shows what was
   * recorded. `meanwhile` is done after Record, before the refusal. */
  async function differing(
    first: Body["lines"],
    boxes: string,
    meanwhile?: () => void,
  ) {
    leaveMoves([KEY, AT, first]);
    const refusal = deferred();
    open(
      recordingLedger(
        first as { quantity: string }[],
        meanwhile === undefined ? undefined : refusal.promise,
      ).api,
    );
    await ready();
    await screen.findByText(ADDED_BACK);
    fireEvent.input(screen.getByLabelText("Box (4 each)"), {
      target: { value: boxes },
    });
    save();
    if (meanwhile !== undefined) {
      await screen.findByText(say(NOTHING_IN_CART));
      meanwhile();
      refusal.resolve(new Response());
    }
    const heading = await screen.findByText(RECORDED_HEADING);
    return heading.closest("li") as HTMLElement;
  }

  const boxed = (n: number) => ({ ...radios(n), package_size: "4" });

  /** The innermost element in `entry` whose text, its counts in italic
   * apart, is `text`. */
  const whole = (entry: HTMLElement, text: string) =>
    within(entry).getByText(
      (_, element) =>
        element?.textContent === text &&
        ![...element.children].some((child) => child.textContent === text),
    );

  /** The cart's lines' items. */
  const itemsInCart = () =>
    storedCart().lines.map((line: { itemId: number }) => line.itemId);

  test("one changed after another tab recorded it says some items were not recorded, then what that tab recorded and what was not (Ali, inventory-tng-0gg, 2026-11-15)", async () => {
    const entry = await differing([boxed(1)], "2");
    // The note, bold as a whole, in the alert's style.
    expectBold(
      whole(entry, say(SOME_NOT_RECORDED)).closest("p"),
      say(SOME_NOT_RECORDED),
    );
    expect(within(entry).queryByText(/Scan again only/)).toBeNull();
    expect(within(entry).queryByText(/another tab recorded this/)).toBeNull();
    // What was recorded, then what was not, the page's counts in italic,
    // never a name's.
    const was = whole(entry, "LiteBeam 5AC: 1 × Box");
    expect([...was.querySelectorAll("em")].map((em) => em.textContent)).toEqual(
      ["1"],
    );
    expect(within(entry).getByText(say(MAKE_CHANGES)).tagName).toBe("H3");
    const more = whole(entry, "LiteBeam 5AC: 1 × Box more");
    expect(
      [...more.querySelectorAll("em")].map((em) => em.textContent),
    ).toEqual(["1"]);
    within(entry).getByRole("button", { name: say(DISMISS) });
  });

  test("its difference goes in an empty cart by itself, under a new key, once, and says so until recorded (Ali, inventory-tng-0gg, 2026-11-15, 2)", async () => {
    const record = vi.spyOn(telemetry, "record");
    record.mockClear();
    const entry = await differing([boxed(1)], "2");
    await vi.waitFor(() => expect(boxes()).toBe("1"));
    const key = storedCart().batchKey;
    expect(key).toMatch(UUID);
    expect(key).not.toBe(KEY);
    within(entry).getByText(say(IN_CART));
    expect(
      within(entry).queryByRole("button", { name: say(ADD_DIFFERENCE) }),
    ).toBeNull();
    expect(
      record.mock.calls.filter(([event]) => event.kind === "difference_added"),
    ).toEqual([[{ kind: "difference_added", how: "automatic" }]]);
    // The entry stays, to read; recorded, the cart no longer holds it.
    whole(entry, "LiteBeam 5AC: 1 × Box more");
    save();
    await screen.findByText("Recorded (transaction 2).");
    expect(within(entry).queryByText(say(IN_CART))).toBeNull();
    // Emptied, the cart does not take it again.
    expect(storedCart().lines).toEqual([]);
  });

  test("two saves under one key recorded elsewhere each put their difference in the cart by itself, under a key of its own, recorded once (inventory-tng-3iy)", async () => {
    const record = vi.spyOn(telemetry, "record");
    record.mockClear();
    const move = (lines: Body["lines"]) =>
      moveBody({ key: KEY, install_number: null, lines });
    const refused = (n: number) =>
      outboxEntry({
        queuedAt: AT + n,
        // Put back from what was recorded, then changed.
        body: { ...move([boxed(n)]), changed_from: [boxed(1)] },
        state: "refused",
        reason: {
          code: "recorded_elsewhere",
          recorded: {
            by: "elsewhere",
            transactionId: 1,
            batch: move([boxed(1)]),
          },
        },
      });
    storeOutbox([refused(2), refused(3)]);
    const written = new Map([[KEY, linesOf({ lines: [boxed(1)] })]]);
    const { api, sent } = ledger(written);
    open(api);
    await ready();
    await vi.waitFor(() => expect(boxes()).toBe("1"));
    save();
    await screen.findByText("Recorded (transaction 2).");
    // Emptied, the cart takes the second's, under another key.
    await vi.waitFor(() => expect(boxes()).toBe("2"));
    save();
    await screen.findByText("Recorded (transaction 3).");
    const keys = await Promise.all(
      saves(sent).map(async (request) => (await request.clone().json()).key),
    );
    expect(keys).toHaveLength(2);
    expect(new Set([KEY, ...keys]).size).toBe(3);
    expect(written.size).toBe(3);
    // Neither goes in again.
    await new Promise((settled) => setTimeout(settled, 50));
    expect(storedCart().lines).toEqual([]);
    expect(
      record.mock.calls.filter(([event]) => event.kind === "difference_added"),
    ).toEqual([
      [{ kind: "difference_added", how: "automatic" }],
      [{ kind: "difference_added", how: "automatic" }],
    ]);
  });

  test("its difference waits on a button while the cart has lines, and goes in by itself once it is empty (Ali, inventory-tng-0gg, 2026-11-15, 2)", async () => {
    const entry = await differing([boxed(1)], "2", fillMove);
    fireEvent.click(
      within(entry).getByRole("button", { name: say(ADD_DIFFERENCE) }),
    );
    expectBold(within(entry).getByRole("alert"), say(CART_NOT_EMPTY));
    expect(itemsInCart()).toEqual([101]);
    expect(within(entry).queryByText(say(IN_CART))).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD_CART) }));
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD) }));
    await vi.waitFor(() => expect(boxes()).toBe("1"));
    within(entry).getByText(say(IN_CART));
  });

  test("its difference the browser would not keep says so, the cart stays empty, and a tap puts it in (inventory-tng-0gg)", async () => {
    const record = vi.spyOn(telemetry, "record");
    record.mockClear();
    let refused: { mockRestore: () => void } | undefined;
    const entry = await differing([boxed(1)], "2", () => {
      refused = refusingWrites(CART_KEY);
    });
    await screen.findByText(say(NOT_KEPT_PHRASE));
    refused?.mockRestore();
    expect(storedCart().lines).toEqual([]);
    fireEvent.click(
      within(entry).getByRole("button", { name: say(ADD_DIFFERENCE) }),
    );
    expect(boxes()).toBe("1");
    within(entry).getByText(say(IN_CART));
    expect(
      record.mock.calls.filter(([event]) => event.kind === "difference_added"),
    ).toEqual([[{ kind: "difference_added", how: "tap" }]]);
  });

  test("its difference put in the cart, but not kept as put there, says so (inventory-tng-0gg)", async () => {
    await differing([boxed(1)], "2", fillMove);
    const refused = refusingWrites(OUTBOX_KEY);
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD_CART) }));
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD) }));
    await vi.waitFor(() => expect(boxes()).toBe("1"));
    refused.mockRestore();
    screen.getByText(say(NOT_REMOVED_PHRASE));
  });

  test("a line taken down says an administrator will correct it, and nothing goes in the cart (inventory-tng-0gg)", async () => {
    const entry = await differing([boxed(2)], "1");
    whole(entry, "LiteBeam 5AC: 1 × Box fewer");
    const lowered =
      "Recorded 2 × Box, you changed it to 1. An administrator will correct it.";
    expectBold(whole(entry, lowered).closest("p"), lowered, ["2", "1"]);
    expect(
      within(entry).queryByRole("button", { name: say(ADD_DIFFERENCE) }),
    ).toBeNull();
    await new Promise((settled) => setTimeout(settled, 50));
    expect(storedCart().lines).toEqual([]);
  });

  test("its difference waits while another tab, unheard yet, filled the cart, and goes in by itself once it is empty (inventory-tng-0gg)", async () => {
    const entry = await differing([boxed(1)], "2", () => {
      // Written as another tab writes it, its storage event not yet heard.
      new CartStore(CATALOGUE).change({
        ...freshCart(),
        volunteerId: 1,
        fromId: 10,
        toId: 12,
        lines: [{ itemId: 101, packages: {}, loose: "1" }],
      });
    });
    await new Promise((settled) => setTimeout(settled, 50));
    expect(itemsInCart()).toEqual([101]);
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
    });
    within(entry).getByRole("button", { name: say(ADD_DIFFERENCE) });
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD_CART) }));
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD) }));
    await vi.waitFor(() => expect(boxes()).toBe("1"));
    within(entry).getByText(say(IN_CART));
  });

  test("a cart not put back, refused as a tab of a newer build recorded it changed, lists no changes of its own (inventory-tng-0gg review)", async () => {
    // That tab took the cart over and changed it: what differs is its
    // change, not this cart's, which nothing here could add back.
    const { api } = answering(async (request) => {
      if (isCatalogue(request)) {
        return json(CATALOGUE, 200);
      }
      const recorded = {
        transaction_id: 1,
        kind: "move",
        volunteer_id: 1,
        from_location_id: 10,
        to_location_id: 12,
        install_number: null,
        lines: [],
        used_up: [],
        lengths: [],
      };
      return json({ reason: "key_reused", recorded }, 409);
    });
    open(api);
    await ready();
    fillMove();
    fireEvent.input(screen.getByLabelText(/^Loose/), {
      target: { value: "3" },
    });
    save();
    const heading = await screen.findByText(RECORDED_HEADING);
    const entry = heading.closest("li") as HTMLElement;
    expect(within(entry).queryByText(say(MAKE_CHANGES))).toBeNull();
    expect(
      within(entry).queryByRole("button", { name: say(ADD_DIFFERENCE) }),
    ).toBeNull();
  });

  test("one with a line discarded, which an older build's tab saved first whole, is not saved again, and says so", async () => {
    leaveSave();
    const [first] = JSON.parse(
      localStorage.getItem("inventory.outbox.v3") ?? "",
    );
    // The older tab's request, in flight as this page loaded, saved it
    // whole, the cable taken from an open package included.
    const written = new Map([[KEY, linesOf(first.body)]]);
    open(ledger(written).api);
    await ready();
    fireEvent.click(discardOf("Cat6 cable: 3 m from an open package"));
    await screen.findByText(ADDED_BACK);
    save();
    await screen.findByText(say(SOME_NOT_RECORDED));
    expect(written.size).toBe(1);
  });

  test("discarded, a cart that went back drops its key, and what fills it next goes under a new one", async () => {
    const { outbox } = await putBackSavedFirst();
    expect(storedCart().batchKey).toBe(KEY);
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD_CART) }));
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD) }));
    expect(storedCart()).not.toHaveProperty("batchKey");
    fillMove();
    save();
    await screen.findByText("Recorded (transaction 2).");
    expect(outbox.entries.value[0]?.body.key).not.toBe(KEY);
  });

  test("emptied, a cart that went back saves what fills it next under a new key", async () => {
    const { outbox } = await putBackSavedFirst();
    const radio = screen.getByText("LiteBeam 5AC").closest("li") as HTMLElement;
    fireEvent.click(within(radio).getByRole("button", { name: say(REMOVE) }));
    fillMove();
    save();
    await screen.findByText("Recorded (transaction 2).");
    expect(outbox.entries.value[0]?.body.key).not.toBe(KEY);
  });

  test("a cart an older build's tab recorded as this tab took it over is recorded once, under the cart's key (inventory-tng-e2d)", async () => {
    // A cart of one box of radios, filled as this build fills it, then
    // left under an older key, as a newer build reads it once out.
    const store = new CartStore(CATALOGUE);
    store.change({
      ...freshCart(),
      volunteerId: 1,
      fromId: 10,
      toId: 12,
      lines: [{ itemId: 100, packages: { 1000: "1" }, loose: "" }],
    });
    const left = localStorage.getItem(CART_KEY) ?? "";
    localStorage.removeItem(CART_KEY);
    localStorage.setItem("inventory.cart.v1", left);
    const key = JSON.parse(left).batchKey;
    // The older build's tab recorded it under its key, its request in
    // flight as this page loaded and took the cart over.
    const written = new Map([[key, linesOf({ lines: [radios(1)] })]]);
    const outbox = open(ledger(written).api);
    await ready();
    await screen.findByText(ADDED_BACK);
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
    save();
    await screen.findByText("Recorded (transaction 1).");
    expect(written.size).toBe(1);
    expect(outbox.entries.value[0]?.body.key).toBe(key);
  });

  test("a cart an older build left without a key gets one as it goes back, and is recorded under it", async () => {
    leaveCart();
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    const key = storedCart().batchKey;
    expect(key).toMatch(UUID);
    save();
    await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
    expect(outbox.entries.value[0]?.body.key).toBe(key);
  });

  test("one put back and refused as another tab recorded it first goes in no cart again, only Discard", async () => {
    // As this build keeps it, once a newer build reads it.
    leaveSave({
      state: "refused",
      reason: { code: "recorded_elsewhere" },
      putBack: true,
    });
    open();
    await ready();
    // What it held is recorded: once more would record it twice (Ali,
    // inventory-tng-wyq, question c).
    const why = within(listing()).getByText(noted(say(SOME_NOT_RECORDED)));
    expect(why.firstElementChild?.tagName).toBe("STRONG");
    within(listing()).getByText(say(MAKE_CHANGES));
    expect(
      within(listing()).queryByRole("button", { name: say(ADD_TO_CART) }),
    ).toBeNull();
    fireEvent.click(lastDiscard());
    fireEvent.click(lastDiscard());
    expect(screen.queryByText(HEADING)).toBeNull();
    expect(screen.getByText(say(NOTHING_IN_CART))).toBeDefined();
  });

  test("one refused for its key goes back without it, and saves under a new one", async () => {
    // As this build keeps it, once a newer build reads it.
    leaveSave({ state: "refused", reason: { code: "key_reused" } });
    // The ledger holds another batch under its key.
    const written = new Map([[KEY, "another"]]);
    const outbox = open(ledger(written).api);
    await ready();
    // Said from its code, in this build's words.
    within(listing()).getByText(
      noted(`${say(KEY_REUSED)} Add it to the cart, fix that, then record it.`),
    );
    fireEvent.click(
      within(listing()).getByRole("button", { name: say(ADD_TO_CART) }),
    );
    save();
    await screen.findByText("Recorded (transaction 2).");
    expect(outbox.entries.value[0]?.body.key).not.toBe(KEY);
  });

  test("a cart with lines takes none without a tap", async () => {
    storeCart(addOne(freshCart(), 101));
    leaveMoves([KEY, AT, [radios(1)]]);
    open();
    await ready();
    within(listing()).getByRole("button", { name: say(ADD_TO_CART) });
    expect(screen.queryByText(ADDED_BACK)).toBeNull();
  });

  test("one changed meanwhile, unheard, goes in without a tap as it is now, once the cart is empty", async () => {
    storeCart(addOne(freshCart(), 101));
    leaveMoves([KEY, AT, [radios(1)]]);
    open();
    await ready();
    // Changed by an older tab, unheard; then the cart recorded there.
    leaveMoves([KEY, AT, [radios(2)]]);
    storeCart({ ...freshCart(), revision: "recorded" });
    // Effects attach after the page paints, so send until it is heard.
    await vi.waitFor(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
      screen.getByText(ADDED_BACK);
    });
    expect(boxes()).toBe("2");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  test("one the browser would not keep in the cart says so, and is not tried again", async () => {
    leaveMoves([KEY, AT, [radios(1)]]);
    const real = localStorage.setItem.bind(localStorage);
    const setItem = vi
      .spyOn(localStorage, "setItem")
      .mockImplementation((key, value) => {
        if (key === CART_KEY) {
          throw new DOMException("full", "QuotaExceededError");
        }
        real(key, value);
      });
    open();
    await ready();
    await screen.findByText(NOT_KEPT);
    setItem.mockRestore();
    screen.getByText(say(NOTHING_IN_CART));
    within(listing()).getByRole("button", { name: say(ADD_TO_CART) });
  });

  test("an item retired since, with none left, is named, and the rest goes in the cart only once that line is discarded", async () => {
    leaveMoves([
      KEY,
      AT,
      [{ item_id: 103, quantity: "4", package_id: 1003 }, radios(1)],
    ]);
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    const retired = within(listing())
      .getByText(/NanoStation M5: 2 × Pair/)
      .closest("li") as HTMLElement;
    expect(retired.textContent).toBe(
      "Item retiredNanoStation M5: 2 × PairDiscard",
    );
    const radio = within(listing())
      .getByText("LiteBeam 5AC: 1 × Box")
      .closest("li") as HTMLElement;
    expect(within(radio).queryByRole("button")).toBeNull();
    const add = within(listing()).getByRole("button", {
      name: say(CANNOT_ADD),
    }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    expect(screen.queryByText(ADDED_BACK)).toBeNull();
    fireEvent.click(
      within(retired).getByRole("button", { name: say(DISCARD) }),
    );
    // The rest, whole now, goes in the empty cart without a tap, and is
    // saved as it is, without the line discarded.
    await screen.findByText(ADDED_BACK);
    expect(boxes()).toBe("1");
    save();
    await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
    expect(outbox.entries.value[0]?.body.lines).toEqual([
      { item_id: 100, package_id: 1000, package_size: "4", quantity: "4" },
    ]);
  });

  test("an item retired since, the whole save discarded, goes", async () => {
    leaveMoves([
      KEY,
      AT,
      [{ item_id: 103, quantity: "4", package_id: 1003 }, radios(1)],
    ]);
    open();
    await ready();
    fireEvent.click(lastDiscard());
    fireEvent.click(lastDiscard());
    expect(screen.queryByText(HEADING)).toBeNull();
  });
});
