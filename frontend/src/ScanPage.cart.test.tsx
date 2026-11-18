import {
  act,
  cleanup,
  fireEvent,
  screen,
  within,
} from "@testing-library/preact";
import { describe, expect, type MockInstance, test, vi } from "vitest";
import { ITEMS, MOVE, PICK_FROM, PICK_TO, PICK_YOUR_NAME } from "./batch";
import {
  CHANGED_BEFORE_DISCARD,
  DISCARD_CART,
  FROM,
  NOT_KEEPING as NOT_KEEPING_PHRASE,
  NOT_SENT,
  NOTHING_IN_CART,
  SENDING_WHAT,
  TO,
  WHERE,
  YOUR_NAME,
} from "./CartForm";
import { REMOVE } from "./CartLine";
import {
  STORAGE_KEY as CART_KEY,
  freshCart,
  KIND_WORDS,
  storeCart,
} from "./cart";
import { FIND } from "./Finder";
import { say, speak } from "./i18n";
import { messages as pseudo } from "./locales/pseudo.po";
import { CONFIRM_DISCARD, DISCARD, KEEP_IT } from "./Notes";
import {
  ALL_OF_IT,
  NOTHING_USABLE,
  PRINTED_LENGTH,
  SHOWS_NOTHING_LEFT,
  WRONG_LENGTH,
} from "./OpenPackageCard";
import { DISMISS } from "./OutboxList";
import {
  NO_ANSWER,
  NOT_RECORDED,
  OFFLINE,
  STORAGE_KEY as OUTBOX_KEY,
  PROBLEMS,
  SOME_NOT_RECORDED,
} from "./outbox";
import { MOST_SECONDS_TO_SAVE, telemetry } from "./telemetry";
import { CATALOGUE, WITH_CAFE } from "./testing/catalogue";
import { answering, deferred, json, saved } from "./testing/fetch";
import { KEY, moveBody, outboxEntry, storeOutbox } from "./testing/saves";
import {
  expectBold,
  expectRefusal,
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
  storedCart,
  UUID,
} from "./testing/scan-page";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

/** The browser says it is `online`, or not, and tells the page so. */
function setOnline(onLine: MockInstance<() => boolean>, online: boolean) {
  onLine.mockReturnValue(online);
  act(() => {
    window.dispatchEvent(new Event(online ? "online" : "offline"));
  });
}

describe("filling and saving a cart", () => {
  /** The cable at the hub, with these open packages there. */
  async function cableWith(...opens: Record<string, unknown>[]) {
    const { api } = answering((request) =>
      isCatalogue(request)
        ? json({ ...CATALOGUE, open_packages: opens }, 200)
        : saved(),
    );
    open(api);
    await ready();
    pick(say(FROM), "10");
    find("cat6");
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
  }

  const spool = (fields: Record<string, unknown> = {}) => ({
    id: 5,
    key: "11111111-1111-4111-8111-111111111111",
    item_id: 101,
    package_id: 1001,
    package_name: "Spool",
    location_id: 10,
    opened_at: "2026-11-03T10:00:00Z",
    left: "120.000",
    short_below: null,
    ...fields,
  });

  const CUT = "Cut or take from the Spool opened 2026-11-03 (m)";
  const NOTHING_LEFT = say(NOTHING_USABLE);
  const ticked = () =>
    (screen.getByLabelText(NOTHING_LEFT) as HTMLInputElement).checked;
  const note = () =>
    screen.getByText(
      (_, element) => element?.getAttribute("aria-live") === "polite",
    ).textContent;

  test("a cart gets its key with its first line, and Record sends it under that key, then drops it (inventory-tng-e2d)", async () => {
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    pick(say(YOUR_NAME), "1");
    expect(storedCart()).not.toHaveProperty("batchKey");
    fillMove();
    const key = storedCart().batchKey;
    expect(key).toMatch(UUID);
    find("litebeam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect(storedCart().batchKey).toBe(key);
    save();
    await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
    expect(outbox.entries.value[0]?.body.key).toBe(key);
    // Another tab sending it too would send this key: refused changed,
    // it was recorded there first.
    expect(outbox.entries.value[0]?.putBack).toBe(true);
    expect(storedCart()).not.toHaveProperty("batchKey");
  });

  test("a cart recorded again under its key, as it was or changed, is not said to be sending nor counted as a save (inventory-tng-e2d)", async () => {
    open();
    await ready();
    fillMove();
    const left = localStorage.getItem(CART_KEY) ?? "";
    save();
    await screen.findByText("Recorded (transaction 77).");
    const record = vi.spyOn(telemetry, "record");
    // Only what follows: a spy another test left may hold calls already.
    record.mockClear();
    // Its emptying not kept: the same cart again, loaded again.
    const again = async (cart: string) => {
      cleanup();
      localStorage.setItem(CART_KEY, cart);
      // Asked about under the cart's key, a batch listed recorded
      // elsewhere is refused: by a server from before inventory-tng-0gg,
      // without saying what it recorded.
      const { api, sent } = server(() => json({ reason: "key_reused" }, 409));
      open(api);
      await ready();
      save();
      expect(screen.queryByText(/^Sending:/)).toBeNull();
      expect(screen.getByText(say(NOTHING_IN_CART))).toBeDefined();
      return sent;
    };
    expect(saves(await again(left))).toHaveLength(0);
    expect(screen.queryByText(say(SOME_NOT_RECORDED))).toBeNull();
    // Changed, by a tab that had not heard: listed as recorded elsewhere,
    // and asked about once, under the cart's key, not recorded.
    const changed = JSON.parse(left);
    changed.lines.push({ itemId: 100, packages: {}, loose: "1" });
    const asked = await again(JSON.stringify(changed));
    await screen.findByText(say(SOME_NOT_RECORDED));
    expect(saves(asked)).toHaveLength(1);
    expect(record).not.toHaveBeenCalledWith(
      expect.objectContaining({ kind: "scan_to_save" }),
    );
  });

  test("a batch the browser would not keep is not saved, and the cart stays", async () => {
    const outbox = open();
    await ready();
    fillMove();
    const real = localStorage.setItem.bind(localStorage);
    const setItem = vi
      .spyOn(localStorage, "setItem")
      .mockImplementation((key, value) => {
        if (key === OUTBOX_KEY) {
          throw new DOMException("full", "QuotaExceededError");
        }
        real(key, value);
      });
    save();
    setItem.mockRestore();
    screen.getByText(say(NOT_SENT));
    screen.getByText("Cat6 cable");
    expect(outbox.entries.value).toEqual([]);
  });

  test("a cart the browser would not keep says so, until it keeps it again", async () => {
    open();
    await ready();
    const real = localStorage.setItem.bind(localStorage);
    const setItem = vi
      .spyOn(localStorage, "setItem")
      .mockImplementation((key, value) => {
        if (key === CART_KEY) {
          throw new DOMException("full", "QuotaExceededError");
        }
        real(key, value);
      });
    const NOT_KEEPING = say(NOT_KEEPING_PHRASE);
    fillMove();
    expectBold(screen.getByText(NOT_KEEPING).closest("li"), NOT_KEEPING);
    setItem.mockRestore();
    pick(say(TO), "10");
    expect(screen.queryByText(NOT_KEEPING)).toBeNull();
  });

  test("an open package is a card: now, after this cut, and a box that ticks itself", async () => {
    await cableWith(spool());
    const card = screen.getByRole("group", {
      name: /^Spool open opened 2026-11-03/,
    });
    expect(within(card).getByText("120 m")).toBeDefined();
    const field = screen.getByLabelText(CUT) as HTMLInputElement;
    // The cut's box is described by both figures.
    const described = (field.getAttribute("aria-describedby") ?? "").split(" ");
    expect(
      described.map((id) => document.getElementById(id)?.textContent),
    ).toEqual(["Now: 120 m", "After this cut: "]);
    fireEvent.input(field, { target: { value: "20" } });
    expect(within(card).getByText("100 m")).toBeDefined();
    expect(ticked()).toBe(false);
    expect(note()).toBe("");
    // All it shows: ticked, and why.
    fireEvent.input(field, { target: { value: "120" } });
    expect(ticked()).toBe(true);
    expect(note()).toBe(
      "Your cut uses up the Spool, so it's ticked. Untick it if cable is left. An administrator confirms.",
    );
    // Unticked by the volunteer: no note.
    fireEvent.click(screen.getByLabelText(NOTHING_LEFT));
    expect(ticked()).toBe(false);
    expect(note()).toBe("");
    // Another cut: the box follows it again.
    fireEvent.input(field, { target: { value: "150" } });
    expect(within(card).getByText("30 m over")).toBeDefined();
    expect(ticked()).toBe(true);
    expect(note()).toBe(
      "Your cut is more than the Spool shows, so it's ticked. It's recorded as you typed it, and an administrator will check the Spool.",
    );
    // Back to a cut that leaves some: unticked again.
    fireEvent.input(field, { target: { value: "20" } });
    expect(ticked()).toBe(false);
    // Moved whole: no cut, no box.
    fireEvent.click(screen.getByLabelText(say(ALL_OF_IT)));
    expect(field.value).toBe("120");
    expect(field.disabled).toBe(true);
    expect(screen.queryByLabelText(NOTHING_LEFT)).toBeNull();
    fireEvent.click(screen.getByLabelText(say(ALL_OF_IT)));
    expect(field.value).toBe("");
    // Another place: what was taken from this one's packages goes.
    fireEvent.input(field, { target: { value: "20" } });
    pick(say(FROM), "12");
    pick(say(FROM), "10");
    expect((screen.getByLabelText(CUT) as HTMLInputElement).value).toBe("");
  });

  test("what is on it now: asked for when a cut passes it, or by a button", async () => {
    await cableWith(spool());
    const now = "What's on the Spool now (m), if you can tell";
    expect(screen.queryByLabelText(now)).toBeNull();
    fireEvent.input(screen.getByLabelText(CUT), { target: { value: "150" } });
    expect(
      screen.getByLabelText(
        "What's on the Spool after this cut (m), if you can tell",
      ),
    ).toBeDefined();
    fireEvent.input(screen.getByLabelText(CUT), { target: { value: "" } });
    expect(screen.queryByLabelText(now)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: say(WRONG_LENGTH) }));
    const field = screen.getByLabelText(now) as HTMLInputElement;
    fireEvent.input(field, { target: { value: "90" } });
    expect(field.value).toBe("90");
    // In metres: how to read the length printed on it (inventory-tng-pg7).
    const hint = document.getElementById(
      field.getAttribute("aria-describedby") ?? "",
    );
    expect(hint?.textContent).toBe(say(PRINTED_LENGTH));
  });

  test("a cut leaving less than the package's short length ticks the box", async () => {
    await cableWith(spool({ short_below: "15.000" }));
    fireEvent.input(screen.getByLabelText(CUT), { target: { value: "110" } });
    expect(ticked()).toBe(true);
    expect(note()).toBe(
      "This Spool will be unusable after this cut, so it's ticked. Untick it if you'll keep the short end.",
    );
  });

  test("several open packages: most left first, one showing nothing last", async () => {
    await cableWith(
      spool({ key: "22222222-2222-4222-8222-222222222222", left: "-4.000" }),
      spool({ key: "33333333-3333-4333-8333-333333333333", left: "40.000" }),
      spool(),
    );
    const cards = screen.getAllByRole("group", { name: /^Spool open/ });
    expect(
      cards.map((card) => within(card).getAllByText(/ m$/)[0]?.textContent),
    ).toEqual(["Now: 120 m", "Now: 40 m", "Now: -4 m"]);
    expect(
      within(cards[2] as HTMLElement).getByText(say(SHOWS_NOTHING_LEFT)),
    ).toBeDefined();
    expect(
      within(cards[0] as HTMLElement).queryByText(say(SHOWS_NOTHING_LEFT)),
    ).toBeNull();
  });

  test("an open package is named by its package, though the catalogue has it no more", async () => {
    // A retired spool with none sealed left is not among the cable's
    // packages, but its open one is still taken from (inventory-tng-4w4).
    await cableWith(
      spool({ package_id: 1009, package_name: "Old reel", left: "40.000" }),
    );
    expect(
      screen.getByRole("group", { name: /^Old reel open opened 2026-11-03/ }),
    ).toBeDefined();
    expect(
      screen.getByLabelText(
        "Cut or take from the Old reel opened 2026-11-03 (m)",
      ),
    ).toBeDefined();
  });

  test("opening packages asks where, not from and to; an item adds its package", async () => {
    open();
    await ready();
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.open)));
    expect(screen.getByLabelText(say(WHERE))).toBeDefined();
    expect(screen.queryByLabelText(say(TO))).toBeNull();
    // Only whole packages are opened: the radio's box sealed here, not a
    // radio.
    pick(say(WHERE), "10");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect(
      (screen.getByLabelText("Box (4 each)") as HTMLInputElement).value,
    ).toBe("1");
  });

  test("opening, a place picked fills what was added before and not typed", async () => {
    open();
    await ready();
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.open)));
    const box = () => (screen.getByLabelText(/^Box/) as HTMLInputElement).value;
    // No place yet: the radio's one package (inventory-tng-prp).
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect(box()).toBe("1");
    // None sealed at the van; one at the hub.
    pick(say(WHERE), "12");
    expect(box()).toBe("");
    pick(say(WHERE), "10");
    expect(box()).toBe("1");
    // Picked again, then through a place with none: still two.
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    pick(say(WHERE), "12");
    pick(say(WHERE), "10");
    expect(box()).toBe("2");
    // Switched to another kind and back, the place kept: as it was.
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.move)));
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.open)));
    expect(box()).toBe("2");
    // Typed into: never filled again.
    fireEvent.input(screen.getByLabelText(/^Box/), { target: { value: "3" } });
    pick(say(WHERE), "12");
    expect(box()).toBe("3");
  });

  test("opening, an item adds its one package sealed there, retired or not", async () => {
    const withOld = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 100
          ? {
              ...item,
              packages: [
                ...item.packages,
                {
                  id: 1002,
                  name: "Old crate",
                  size: "8",
                  track_opened: false,
                  retired: true,
                  short_below: null,
                  sealed: [
                    { location_id: 10, count: 1 },
                    { location_id: 12, count: 1 },
                  ],
                },
              ],
            }
          : item,
      ),
    };
    const { api } = answering(() => json(withOld, 200));
    open(api);
    await ready();
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.open)));
    const field = (name: RegExp) =>
      (screen.getByLabelText(name) as HTMLInputElement).value;
    // At the van only the old crate is sealed (inventory-tng-f9h).
    pick(say(WHERE), "12");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect([field(/^Box/), field(/^Old crate/)]).toEqual(["", "1"]);
    // At the hub both are: neither is picked for the volunteer.
    pick(say(WHERE), "10");
    fireEvent.input(screen.getByLabelText(/^Old crate/), {
      target: { value: "" },
    });
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    // Nor a loose one: only whole packages are opened.
    expect([field(/^Box/), field(/^Old crate/), field(/^Loose/)]).toEqual([
      "",
      "",
      "",
    ]);
  });

  test("Discard cart asks first, in the page, then empties the cart and counts it, keeping who and where", async () => {
    const record = vi.spyOn(telemetry, "record");
    const { api, sent } = server();
    open(api);
    await ready();
    // Nothing to discard in an empty cart.
    expect(
      screen.queryByRole("button", { name: say(DISCARD_CART) }),
    ).toBeNull();
    fillMove();
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD_CART) }));
    // Row 15's pattern (Ali, inventory-tng-wyq).
    expect(screen.getByRole("alert").textContent).toBe(say(CONFIRM_DISCARD));
    expect(screen.getByRole("button", { name: say(DISCARD) }).className).toBe(
      "danger",
    );
    expect(
      screen.queryByRole("button", { name: say(DISCARD_CART) }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: say(KEEP_IT) }));
    screen.getByText("Cat6 cable");
    expect(screen.queryByText(say(CONFIRM_DISCARD))).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD_CART) }));
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD) }));
    screen.getByText(say(NOTHING_IN_CART));
    expect(screen.queryByText(say(CONFIRM_DISCARD))).toBeNull();
    expect(
      screen.queryByRole("button", { name: say(DISCARD_CART) }),
    ).toBeNull();
    expect(storedCart().lines).toEqual([]);
    expect(selected(say(YOUR_NAME))).toBe("1");
    expect(selected(say(FROM))).toBe("10");
    expect(
      record.mock.calls.filter(([event]) => event.kind === "cart_discarded"),
    ).toEqual([[{ kind: "cart_discarded" }]]);
    expect(saves(sent)).toHaveLength(0);
  });

  test.each([
    ["heard", true],
    ["not yet heard", false],
  ])(
    "Discard cart confirmed after another tab changed the cart, %s, discards nothing",
    async (_, heard) => {
      const record = vi.spyOn(telemetry, "record");
      open();
      await ready();
      fillMove();
      fireEvent.click(screen.getByRole("button", { name: say(DISCARD_CART) }));
      // Another tab counts 4 m, which this tab's volunteer never confirmed.
      const theirs = {
        ...freshCart(),
        volunteerId: 1,
        fromId: 10,
        toId: 12,
        lines: [{ itemId: 101, packages: {}, loose: "4" }],
        revision: "theirs",
      };
      storeCart(theirs);
      if (heard) {
        await vi.waitFor(() => {
          window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
          screen.getByText("4 m in all");
        });
      }
      fireEvent.click(screen.getByRole("button", { name: say(DISCARD) }));
      expect(screen.getByRole("alert").textContent).toBe(
        say(CHANGED_BEFORE_DISCARD),
      );
      screen.getByText("4 m in all");
      expect(screen.queryByText(say(CONFIRM_DISCARD))).toBeNull();
      expect(storedCart().lines).toEqual(theirs.lines);
      expect(
        record.mock.calls.filter(([event]) => event.kind === "cart_discarded"),
      ).toEqual([]);
    },
  );

  test("Discard cart asked, then the cart emptied and filled again in another tab, asks nothing", async () => {
    open();
    await ready();
    fillMove();
    fireEvent.click(screen.getByRole("button", { name: say(DISCARD_CART) }));
    screen.getByText(say(CONFIRM_DISCARD));
    const other = (lines: ReturnType<typeof freshCart>["lines"]) => {
      storeCart({ ...freshCart(), volunteerId: 1, lines, revision: "x" });
      window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
    };
    other([]);
    await screen.findByText(say(NOTHING_IN_CART));
    other([{ itemId: 101, packages: {}, loose: "4" }]);
    await screen.findByText("4 m in all");
    expect(screen.queryByText(say(CONFIRM_DISCARD))).toBeNull();
    screen.getByRole("button", { name: say(DISCARD_CART) });
  });

  test("a move is saved, sent, and a new cart keeps the choices", async () => {
    const record = vi.spyOn(telemetry, "record");
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    pick(say(YOUR_NAME), "1");
    pick(say(FROM), "10");
    pick(say(TO), "12");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    fireEvent.input(screen.getByLabelText("Box (4 each)"), {
      target: { value: "1" },
    });
    expect(screen.getByText("5 each in all")).toBeDefined();
    save();

    expect((await screen.findByRole("status")).textContent).toBe(
      "Sending: Move: 1 item from Hub to Van. A new cart is ready.",
    );
    await screen.findByText("Recorded (transaction 77).");
    const body = await saves(sent)[0]?.json();
    expect(body).toMatchObject({
      kind: "move",
      volunteer_id: 1,
      from_location_id: 10,
      to_location_id: 12,
      // The package scanned stays sealed; the one added by hand is loose.
      lines: [
        { item_id: 100, quantity: "4", package_id: 1000 },
        { item_id: 100, quantity: "1" },
      ],
    });
    expect(outbox.entries.value[0]?.body.key).toBe(body.key);
    // How long it took from its first line, which the page reports.
    expect(record).toHaveBeenCalledWith({
      kind: "scan_to_save",
      seconds: expect.any(Number),
    });
    // The next cart: empty, with the same name and places.
    expect(screen.getByText(say(NOTHING_IN_CART))).toBeDefined();
    expect(selected(say(YOUR_NAME))).toBe("1");
    expect(selected(say(TO))).toBe("12");
  });

  test("a scanned label adds its package; Enter adds the only match", async () => {
    open();
    await ready();
    find("K7QX2M");
    fireEvent.keyDown(screen.getByLabelText(say(FIND)), { key: "Enter" });
    expect(screen.getByText("305 m in all")).toBeDefined();
    find("litebeam");
    fireEvent.keyDown(screen.getByLabelText(say(FIND)), { key: "Enter" });
    expect(screen.getByText("1 each in all")).toBeDefined();
  });

  test("an item is found without its accents, and shown with them", async () => {
    open(answering(() => json(WITH_CAFE, 200)).api);
    await ready();
    find("cafe lodz");
    screen.getByRole("button", { name: "Café Łódź patch" });
    expect(document.body.textContent).not.toContain("Cafe Lodz");
  });

  test("another key, or Enter with more than one match, adds nothing", async () => {
    open();
    await ready();
    find("a");
    expect(screen.getByRole("button", { name: "LiteBeam 5AC" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Cat6 cable" })).toBeDefined();
    const finder = screen.getByLabelText(say(FIND));
    fireEvent.keyDown(finder, { key: "a" });
    fireEvent.keyDown(finder, { key: "Enter" });
    expect(screen.getByText(say(NOTHING_IN_CART))).toBeDefined();
  });

  test("Save says what is missing, and sends nothing", async () => {
    const { api, sent } = server();
    open(api);
    await ready();
    save();
    const problems = within(await screen.findByRole("alert")).getAllByRole(
      "listitem",
    );
    expect(problems.map((p) => p.textContent)).toEqual([
      say(PICK_YOUR_NAME),
      say(PICK_FROM),
      say(PICK_TO),
    ]);
    expect(saves(sent)).toEqual([]);
  });

  test("a retired item says it can only be taken", async () => {
    const retired = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 101 ? { ...item, retired: true } : item,
      ),
    };
    const { api } = answering(() => json(retired, 200));
    open(api);
    await ready();
    find("cable");
    expect(screen.getByText(/retired: taking only/)).toBeDefined();
  });

  test("a retired package says it can only be taken", async () => {
    const retired = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 101
          ? {
              ...item,
              packages: item.packages.map((pack) => ({
                ...pack,
                retired: true,
              })),
            }
          : item,
      ),
    };
    const { api } = answering(() => json(retired, 200));
    open(api);
    await ready();
    find("cat6");
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
    expect(
      screen.getByLabelText("Spool (305 m), retired: taking only"),
    ).toBeDefined();
  });

  test("an install asks for its number and has no To", async () => {
    open();
    await ready();
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.install)));
    expect(screen.getByLabelText(/install number/)).toBeDefined();
    expect(screen.queryByLabelText(say(TO))).toBeNull();
    expect(screen.getByLabelText(say(FROM))).toBeDefined();
  });

  test("a line is changed or removed on its own, and an install number typed", async () => {
    open();
    await ready();
    find("cable");
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
    find("litebeam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    fireEvent.input(screen.getByLabelText("Loose m"), {
      target: { value: "2" },
    });
    expect(screen.getByText("2 m in all")).toBeDefined();
    expect(screen.getByText("1 each in all")).toBeDefined();
    fireEvent.click(
      screen.getAllByRole("button", { name: say(REMOVE) })[1] as Element,
    );
    expect(screen.queryByText("1 each in all")).toBeNull();
    expect(screen.getByText("2 m in all")).toBeDefined();
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.install)));
    const number = screen.getByLabelText(/install number/) as HTMLInputElement;
    fireEvent.input(number, { target: { value: "1234" } });
    expect(number.value).toBe("1234");
  });

  test("the cart survives a reload", async () => {
    open();
    await ready();
    find("cable");
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
    document.body.innerHTML = "";
    open();
    await ready();
    expect(screen.getByText("Cat6 cable")).toBeDefined();
  });

  test("a refused batch says why in the alert's style, and lists its lines", async () => {
    const detail = [
      { type: "not_whole", loc: ["body", "lines", 1, "quantity"] },
    ];
    open(server(() => json({ detail }, 422)).api);
    await ready();
    fillMove();
    find("LiteBeam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    save();
    const note = await screen.findByText(
      (_, element) =>
        element?.className === "alert" &&
        element.textContent ===
          `Not recorded. Line 2: ${say(PROBLEMS.not_whole)}`,
    );
    // Only say(NOT_RECORDED) in bold; in what follows, the line's number
    // the page wrote in italic (Ali, inventory-tng-93z).
    expectRefusal(note, say(NOT_RECORDED), ["2"]);
    const entry = note.closest("li") as HTMLElement;
    // Numbered as the problems name them.
    expect(
      [...entry.querySelectorAll("ul li")].map((each) => each.textContent),
    ).toEqual(["1. Cat6 cable: 1 m", "2. LiteBeam 5AC: 1"]);
    within(entry).getByRole("button", { name: say(DISMISS) });
  });

  test("two batches under one key are listed each with its own lines, and dismissed alone (inventory-tng-suo)", async () => {
    // Saved, then queued again under its key by a tab of an older build,
    // changed, and refused as the key was used.
    const batch = (quantity: string) =>
      moveBody({ key: KEY, lines: [{ item_id: 101, quantity }] });
    storeOutbox([
      outboxEntry({
        body: batch("1"),
        summary: { from: "Hub", to: "Van" },
        state: "saved",
        transactionId: 41,
      }),
      outboxEntry({
        body: batch("2"),
        summary: { from: "Hub", to: "Shelf" },
        state: "refused",
        reason: { code: "key_reused" },
      }),
    ]);
    const outbox = open();
    await ready();
    const row = (what: string) =>
      screen.getByText(what).closest("li") as HTMLElement;
    const lines = (what: string) =>
      [...row(what).querySelectorAll("ul li")].map((each) => each.textContent);
    const first = "Move: 1 item from Hub to Van";
    const second = "Move: 1 item from Hub to Shelf";
    expect(lines(first)).toEqual([]);
    expect(lines(second)).toEqual(["1. Cat6 cable: 2 m"]);
    fireEvent.click(
      within(row(second)).getByRole("button", { name: say(DISMISS) }),
    );
    expect(screen.queryByText(second)).toBeNull();
    screen.getByText(first);
    expect(outbox.entries.value).toHaveLength(1);
    expect(outbox.entries.value[0]).not.toHaveProperty("dismissed");
  });

  test("a batch queued is worded in the language spoken as it is shown, its places as named when queued (inventory-tng-ar8)", async () => {
    const answer = deferred();
    open(server(() => answer.promise).api);
    await ready();
    fillMove();
    fireEvent.input(screen.getByLabelText("Loose m"), {
      target: { value: "2" },
    });
    save();
    const english = "Move: 1 item from Hub to Van";
    screen.getByText(`Sending: ${english}. A new cart is ready.`);
    screen.getByText(english);
    act(() => speak("pseudo", pseudo));
    const words = say(MOVE, {
      items: say(ITEMS, { count: 1 }),
      from: "Hub",
      to: "Van",
    });
    expect(words).not.toBe(english);
    screen.getByText(say(SENDING_WHAT, { what: words }));
    screen.getByText(words);
    expect(screen.queryByText(english)).toBeNull();
  });

  test("a cart's problem puts in italic the counts the page wrote, not a name's numbers", async () => {
    const named = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 101 ? { ...item, name: "Cat 6 cable" } : item,
      ),
    };
    const { api } = answering((request) =>
      isCatalogue(request) ? json(named, 200) : saved(),
    );
    open(api);
    await ready();
    fillMove("Cat 6 cable");
    fireEvent.input(screen.getByLabelText("Loose m"), {
      target: { value: "1.2345" },
    });
    save();
    // Ali, inventory-tng-93z.
    expectBold(
      (await screen.findByText(/^Cat 6 cable: Enter the loose amount/)).closest(
        "li",
      ),
      "Cat 6 cable: Enter the loose amount in m, up to 3 decimals.",
      ["3"],
    );
  });

  test("a cart's problem puts in italic the counts the page wrote, even where a name is the same number", async () => {
    // The cart names what it quotes (batch.ts, toBatch), so the page's "3"
    // is not taken for the name "3" (inventory-tng-wyq).
    const named = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 101 ? { ...item, name: "3" } : item,
      ),
    };
    const { api } = answering((request) =>
      isCatalogue(request) ? json(named, 200) : saved(),
    );
    open(api);
    await ready();
    pick(say(YOUR_NAME), "1");
    pick(say(FROM), "10");
    pick(say(TO), "12");
    find("3");
    fireEvent.click(screen.getByRole("button", { name: "3" }));
    fireEvent.input(screen.getByLabelText("Loose m"), {
      target: { value: "1.2345" },
    });
    save();
    expectBold(
      (await screen.findByText(/^3: Enter the loose amount/)).closest("li"),
      "3: Enter the loose amount in m, up to 3 decimals.",
      ["3"],
    );
  });

  test("saved with no connection, the batch waits in the outbox", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = server(() =>
      Promise.reject(new TypeError("Failed to fetch")),
    );
    const outbox = open(api);
    await ready();
    pick(say(YOUR_NAME), "2");
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.receipt)));
    pick(say(TO), "11");
    find("cable");
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
    save();
    // A failure, in the alert's style, in bold, but not read out: every
    // note in the list is there as the page loads (Ali, inventory-tng-93z).
    const note = await screen.findByText(say(NO_ANSWER));
    expect(note.tagName).toBe("STRONG");
    expect(note.parentElement?.className).toBe("alert");
    expect(outbox.entries.value[0]).toMatchObject({
      state: "waiting",
      summary: { from: null, to: "Hub › Shelf A" },
    });
  });

  test("while the browser is offline, a batch waiting for an answer says so, and online again, as before", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const onLine = vi.spyOn(navigator, "onLine", "get").mockReturnValue(false);
    const { api } = server(() =>
      Promise.reject(new TypeError("Failed to fetch")),
    );
    open(api);
    await ready();
    fillMove();
    fireEvent.input(screen.getByLabelText(/^Loose/), {
      target: { value: "3" },
    });
    save();
    // Bold whole, in the alert's style (Ali, inventory-tng-wyq, 'Offline').
    const note = await screen.findByText(say(OFFLINE));
    expect(note.tagName).toBe("STRONG");
    expect(screen.queryByText(say(NO_ANSWER))).toBeNull();
    setOnline(onLine, true);
    await screen.findByText(say(NO_ANSWER));
    expect(screen.queryByText(say(OFFLINE))).toBeNull();
    setOnline(onLine, false);
    await screen.findByText(say(OFFLINE));
    onLine.mockRestore();
  });
});

test("a cart kept by an older page, which did not note its start, reports no time", async () => {
  const record = vi.spyOn(telemetry, "record");
  storeCart({
    ...freshCart(),
    volunteerId: 1,
    fromId: 10,
    toId: 12,
    lines: [{ itemId: 101, packages: {}, loose: "4" }],
  });
  const { api, sent } = server();
  open(api);
  await ready();
  save();
  await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
  expect(record).not.toHaveBeenCalledWith(
    expect.objectContaining({ kind: "scan_to_save" }),
  );
});

test("a cart kept for days reports a day, the most the backend takes", async () => {
  const record = vi.spyOn(telemetry, "record");
  storeCart({
    ...freshCart(),
    volunteerId: 1,
    fromId: 10,
    toId: 12,
    lines: [{ itemId: 101, packages: {}, loose: "4" }],
    startedAt: Date.now() - 3 * MOST_SECONDS_TO_SAVE * 1000,
  });
  const { api, sent } = server();
  open(api);
  await ready();
  save();
  await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
  expect(record).toHaveBeenCalledWith({
    kind: "scan_to_save",
    seconds: MOST_SECONDS_TO_SAVE,
  });
});
