import { cleanup, fireEvent, screen, within } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { STORAGE_KEY as OUTBOX_KEY } from "../shared/outbox";
import { SOME_NOT_RECORDED } from "../shared/outbox-notes";
import { MOST_SECONDS, telemetry } from "../shared/telemetry";
import { CATALOGUE, WITH_CAFE } from "../testing/catalogue";
import { answering, json } from "../testing/fetch";
import {
  expectBold,
  fillMove,
  find,
  open,
  pick,
  ready,
  save,
  saves,
  selected,
  server,
  storedCart,
  UUID,
} from "../testing/scan-page";
import { refusingWrites } from "../testing/storage";
import { PICK_FROM, PICK_TO, PICK_YOUR_NAME } from "./batch";
import {
  CHANGED_BEFORE_DISCARD,
  DISCARD_CART,
  FROM,
  NOT_KEEPING as NOT_KEEPING_PHRASE,
  NOT_SENT,
  NOTHING_IN_CART,
  TO,
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
import { CONFIRM_DISCARD, DISCARD, KEEP_IT } from "./Notes";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

describe("filling and saving a cart", () => {
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
      await screen.findByText(say(NOTHING_IN_CART));
      expect(screen.queryByText(/^Sending:/)).toBeNull();
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
    const setItem = refusingWrites(OUTBOX_KEY);
    save();
    await screen.findByText(say(NOT_SENT));
    setItem.mockRestore();
    screen.getByText("Cat6 cable");
    expect(outbox.entries.value).toEqual([]);
  });

  test("a cart the browser would not keep says so, until it keeps it again", async () => {
    open();
    await ready();
    const setItem = refusingWrites(CART_KEY);
    const NOT_KEEPING = say(NOT_KEEPING_PHRASE);
    fillMove();
    expectBold(screen.getByText(NOT_KEEPING).closest("li"), NOT_KEEPING);
    setItem.mockRestore();
    pick(say(TO), "10");
    expect(screen.queryByText(NOT_KEEPING)).toBeNull();
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
    expect(
      screen.getByRole("button", { name: say(DISCARD) }).classList,
    ).toContain("danger");
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
    startedAt: Date.now() - 3 * MOST_SECONDS * 1000,
  });
  const { api, sent } = server();
  open(api);
  await ready();
  save();
  await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
  expect(record).toHaveBeenCalledWith({
    kind: "scan_to_save",
    seconds: MOST_SECONDS,
  });
});
