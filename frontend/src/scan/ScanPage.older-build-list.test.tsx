import { fireEvent, screen, within } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { STORAGE_KEY as OUTBOX_KEY } from "../shared/outbox";
import { telemetry } from "../shared/telemetry";
import { json, TOO_OLD } from "../testing/fetch";
import {
  ADDED_BACK,
  AT,
  boxes,
  discardOf,
  HEADING,
  lastDiscard,
  leaveCart,
  leaveSave,
  listing,
  NOT_KEPT,
  noted,
} from "../testing/older-list";
import {
  KEY,
  keep,
  keepOlderCart,
  olderSave,
  outboxEntry,
  storeOutbox,
} from "../testing/saves";
import {
  expectBold,
  expectRefusal,
  fillMove,
  open,
  ready,
  save,
  saves,
  selected,
  server,
  status,
  storedCart,
  UUID,
} from "../testing/scan-page";
import { refusingWrites } from "../testing/storage";
import {
  FROM,
  INSTALL_NUMBER,
  NOT_REMOVED as NOT_REMOVED_PHRASE,
  NOTHING_IN_CART,
  TO,
  YOUR_NAME,
} from "./CartForm";
import { REMOVE } from "./CartLine";
import { STORAGE_KEY as CART_KEY, freshCart, storeCart } from "./cart";
import { ADD_TO_CART, CONFIRM_DISCARD, DISCARD, KEEP_IT } from "./Notes";
import {
  CANNOT_ADD,
  CART_NOT_EMPTY,
  CHANGED_CHECK_AGAIN,
  CHANGED_ELSEWHERE,
  CHANGED_NOT_DISCARDED,
  FIX_THEN_RECORD,
  NOT_DISCARDED as NOT_DISCARDED_PHRASE,
  SOMETHING,
} from "./OlderList";
import { CARTS_FROM_DEVICE } from "./OutboxList";
import { RESCAN as RESCAN_PHRASE } from "./older-to-cart";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

describe("what an older build left", () => {
  // Failures, in bold (Ali, inventory-tng-93z).
  const NOT_DISCARDED = say(NOT_DISCARDED_PHRASE);
  const NOT_REMOVED = say(NOT_REMOVED_PHRASE);
  const RESCAN = say(RESCAN_PHRASE);
  const OLDER = "2026-11-12T09:00:00.000Z";
  const at = new Date(AT).toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });

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
    const setItem = refusingWrites();
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
    const setItem = refusingWrites();
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
    const setItem = refusingWrites("inventory.outbox.v3");
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
    keepOlderCart({
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
    leaveSave({
      state: "refused",
      note: "Not saved. Line 1: no. Line 2: gone",
    });
    open();
    await ready();
    // In the alert's style: "Not saved." in bold, the rest not, its
    // numbers in italic (Ali, inventory-tng-93z), each problem on a line
    // of its own (Ali, 2026-11-20, inventory-tng-61ys).
    const why = within(listing()).getByText(
      noted(`Not saved.Line 1: no.Line 2: gone ${say(FIX_THEN_RECORD)}`),
    );
    expectRefusal(
      why,
      "Not saved.",
      ["1", "2"],
      ["Line 1: no.", "Line 2: gone"],
    );
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
    leaveSave(
      {},
      {
        lines: [{ item_id: 101, quantity: "3", open_package_key: KEY }],
      },
    );
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
    // saying "Added back to cart. Review before Record." and nothing
    // left out to say (Ali, 2026-11-13, inventory-tng-0su).
    await screen.findByText(ADDED_BACK);
    expect(status().textContent).toBe(ADDED_BACK);
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
    leaveSave(
      {},
      {
        lines: [
          { item_id: 101, quantity: "3", open_package_key: KEY },
          { item_id: 101, quantity: "5", open_package_key: KEY },
        ],
      },
    );
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
    leaveSave(
      {},
      {
        lines: [
          { item_id: 101, quantity: "3", open_package_key: KEY },
          { item_id: 101, quantity: "5", open_package_key: KEY },
          { item_id: 101, quantity: "7", open_package_key: KEY },
          { item_id: 100, quantity: "8", package_id: 1000 },
        ],
      },
    );
    open();
    await ready();
    fireEvent.click(discardOf("Cat6 cable: 3 m from an open package"));
    await vi.waitFor(() => expect(document.activeElement?.id).toBe("older-0"));
    // The second tap of a double tap, on what moved up in its place and
    // focusing it, as a browser does: ignored, with focus back on the
    // heading so Enter cannot discard it later (inventory-tng-bz73).
    const movedUp = discardOf("Cat6 cable: 5 m from an open package");
    movedUp.focus();
    fireEvent.click(movedUp, { detail: 1 });
    within(listing()).getByText("Cat6 cable: 5 m from an open package");
    expect(document.activeElement?.id).toBe("older-0");
    // Moved to it by key, on purpose, and pressed: ignored, but focus
    // stays where the volunteer put it (a key's click has detail 0).
    movedUp.focus();
    fireEvent.click(movedUp, { detail: 0 });
    within(listing()).getByText("Cat6 cable: 5 m from an open package");
    expect(document.activeElement).toBe(movedUp);
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
    leaveSave(
      {},
      {
        lines: [
          { item_id: 101, quantity: "3", open_package_key: KEY },
          { item_id: "lots" },
        ],
      },
    );
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
    const setItem = refusingWrites();
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
      {
        item_id: 100,
        package_id: 1000,
        package_size: "4",
        quantity: "8",
        unit: "each",
      },
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

  test("nothing is removed without the volunteer acting, but empty keys", async () => {
    leaveSave();
    const left = localStorage.getItem("inventory.outbox.v3");
    keepOlderCart();
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
});
