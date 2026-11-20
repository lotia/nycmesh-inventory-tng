import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { type Body, STORAGE_KEY as OUTBOX_KEY } from "../shared/outbox";
import {
  KEY_REUSED,
  MAKE_CHANGES,
  SOME_NOT_RECORDED,
} from "../shared/outbox-notes";
import { telemetry } from "../shared/telemetry";
import { CATALOGUE } from "../testing/catalogue";
import { answering, deferred, json, keyReused, saved } from "../testing/fetch";
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
  moveBody,
  olderSave,
  outboxEntry,
  storeOutbox,
} from "../testing/saves";
import {
  expectBold,
  fillMove,
  isCatalogue,
  open,
  ready,
  save,
  saves,
  server,
  storedCart,
  UUID,
} from "../testing/scan-page";
import { refusingWrites } from "../testing/storage";
import {
  DISCARD_CART,
  NOT_REMOVED as NOT_REMOVED_PHRASE,
  NOTHING_IN_CART,
} from "./CartForm";
import { REMOVE } from "./CartLine";
import {
  addOne,
  STORAGE_KEY as CART_KEY,
  CartStore,
  freshCart,
  storeCart,
} from "./cart";
import { ADD_TO_CART, DISCARD } from "./Notes";
import { CANNOT_ADD, CART_NOT_EMPTY } from "./OlderList";
import { DISMISS } from "./OutboxList";
import { ITEM_RETIRED } from "./older-to-cart";
import { ADD_DIFFERENCE, IN_CART } from "./recorded-elsewhere";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

describe("what an older build left", () => {
  const RETIRED = say(ITEM_RETIRED);

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
    const { api } = server(saved, named);
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

  /** The batch endpoint as the ledger answers a key (scan/batch.py, _replay):
   * a new key is saved as the next transaction; the same lines again under
   * a key are its transaction; others are refused, once `held` lets it,
   * saying what was recorded only when `saying`, as a server since
   * inventory-tng-0gg does. `written` holds the lines saved under each key,
   * in order. */
  function ledger(
    written: Map<string, Body["lines"]>,
    { saying = false, held = Promise.resolve() as Promise<unknown> } = {},
  ) {
    return answering(async (request) => {
      if (isCatalogue(request)) {
        return json(CATALOGUE, 200);
      }
      const body = await request.clone().json();
      const first = written.get(body.key);
      if (first === undefined) {
        written.set(body.key, body.lines);
        return saved(written.size);
      }
      const transaction = [...written.keys()].indexOf(body.key) + 1;
      if (linesOf({ lines: first }) === linesOf(body)) {
        return json({ transaction_id: transaction }, 200);
      }
      await held;
      if (!saying) {
        return json({ reason: "key_reused" }, 409);
      }
      return keyReused(
        first.map((line) => ({ ...line, quantity: `${line.quantity}.000` })),
        { transaction_id: transaction },
      );
    });
  }

  /** A move of one radio left, put back in the cart without a tap, which
   * the older build's tab, its request in flight as this page loaded,
   * saved first. */
  async function putBackSavedFirst() {
    leaveMoves([KEY, AT, [radios(1)]]);
    const written = new Map([[KEY, [radios(1)]]]);
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
      ledger(new Map([[KEY, first]]), {
        saying: true,
        held: meanwhile === undefined ? undefined : refusal.promise,
      }).api,
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
    expect(within(entry).getByText(say(IN_CART)).classList).not.toContain(
      "invisible",
    );
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
    // Its words keep their room, so Dismiss does not move, unseen and not
    // read out (Ali, 2026-11-19, inventory-tng-2lad).
    const room = within(entry).getByText(say(IN_CART));
    expect(room.classList).toContain("invisible");
    expect(room.getAttribute("aria-hidden")).toBe("true");
    // Emptied, the cart does not take it again.
    expect(storedCart().lines).toEqual([]);
    // Drawn again, recorded already, it keeps no room: it will not move.
    cleanup();
    open(server().api);
    await ready();
    const again = (await screen.findByText(RECORDED_HEADING)).closest(
      "li",
    ) as HTMLElement;
    expect(within(again).queryByText(say(IN_CART))).toBeNull();
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
    const written = new Map([[KEY, [boxed(1)]]]);
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
    await screen.findByText(NOT_KEPT);
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
    const { api } = server(() => keyReused());
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
    const written = new Map([[KEY, first.body.lines]]);
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
    const written = new Map([[key, [radios(1)]]]);
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
    const written = new Map([[KEY, []]]);
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
    const setItem = refusingWrites(CART_KEY);
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
