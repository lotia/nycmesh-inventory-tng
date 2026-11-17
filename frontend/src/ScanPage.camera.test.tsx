import { fireEvent, screen } from "@testing-library/preact";
import { beforeEach, describe, expect, test, vi } from "vitest";
import { freshCart, storeCart } from "./cart";
import { say } from "./i18n";
import { STORAGE_KEY as OUTBOX_KEY, Outbox } from "./outbox";
import { DONE, POINT, READ_EVERY_MS } from "./Scanner";
import {
  COULD_NOT_LOAD,
  DISMISS,
  FIND,
  FROM,
  KIND_WORDS,
  NOTHING_IN_CART,
  REMOVE,
  SCAN_WITH_CAMERA,
  TO,
  WHERE,
  YOUR_NAME,
} from "./ScanPage";
import { CATALOGUE } from "./test-catalogue";
import { answering, deferred, json, saved } from "./test-fetch";
import {
  camera,
  find,
  isCatalogue,
  open,
  pick,
  ready,
  save,
  selected,
  server,
} from "./test-scan-page";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

describe("scanning labels with the camera", () => {
  // The camera's clock, faked: scan lets a read's time pass (test-setup.ts
  // makes it real again).
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));

  /** Shows the camera these labels, and lets the next read take them. */
  async function scan(...codes: string[]) {
    camera.state.inView = codes;
    await vi.advanceTimersByTimeAsync(READ_EVERY_MS);
  }

  async function opened(api = server().api) {
    const outbox = open(api);
    await ready();
    camera.state.inView = [];
    fireEvent.click(
      screen.getByRole("button", { name: say(SCAN_WITH_CAMERA) }),
    );
    await screen.findByText(say(POINT));
    return outbox;
  }

  /** Ada opens the van's sealed spool, by its label. */
  async function openSpoolInVan() {
    pick(say(YOUR_NAME), "1");
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.open)));
    pick(say(WHERE), "12");
    await scan("K7QX2M");
    await screen.findByText("Added a Spool: Cat6 cable.");
  }

  test("an item's label adds one, and a package's label a package", async () => {
    const vibrate = vi.fn();
    Object.assign(navigator, { vibrate });
    await opened();
    await scan("B3ZT9W");
    await screen.findByText("Added one: LiteBeam 5AC.");
    expect(vibrate).toHaveBeenCalled();
    await scan("K7QX2M");
    await screen.findByText("Added a Spool: Cat6 cable.");
    expect(screen.queryByText(say(NOTHING_IN_CART))).toBeNull();
  });

  test("opening, an item's label asks which package if none is sealed there", async () => {
    await opened();
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.open)));
    // The radio's box is sealed at the hub, not the van.
    pick(say(WHERE), "12");
    await scan("B3ZT9W");
    await screen.findByText(
      "LiteBeam 5AC: type how many of which package to open.",
    );
  });

  test("opening, the last one sealed there opened, the next fills nothing", async () => {
    // One spool sealed in the van (test-catalogue); opened, it waits in
    // the outbox, and the catalogue still says one is sealed there.
    const { api } = server(() =>
      Promise.reject(new TypeError("Failed to fetch")),
    );
    await opened(api);
    await openSpoolInVan();
    save();
    await screen.findByText(/^Sending: Opening/);
    find("cable");
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
    expect(
      (screen.getByLabelText("Spool (305 m)") as HTMLInputElement).value,
    ).toBe("");
  });

  test("a batch saved, the catalogue is asked for again", async () => {
    const { api, sent } = server();
    const outbox = await opened(api);
    const asked = () => sent.filter(isCatalogue).length;
    expect(asked()).toBe(1);
    await openSpoolInVan();
    save();
    await screen.findByText("Recorded (transaction 77).");
    await vi.waitFor(() => expect(asked()).toBe(2));
    // Asked whether it shows the batch saved.
    const [, again] = sent.filter(isCatalogue);
    const [entry] = outbox.entries.value;
    expect(new URL(again?.url ?? "").searchParams.getAll("key")).toEqual([
      entry?.body.key,
    ]);
  });

  test("a save while the catalogue is asked for asks once more after", async () => {
    const later = deferred();
    const catalogues: Request[] = [];
    const { api } = answering((request) => {
      if (!isCatalogue(request)) {
        return saved();
      }
      catalogues.push(request);
      return catalogues.length === 2 ? later.promise : json(CATALOGUE, 200);
    });
    await opened(api);
    await openSpoolInVan();
    save();
    await screen.findByText(/^Sending: Opening/);
    // The radio's box, sealed at the hub.
    pick(say(WHERE), "10");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    save();
    await vi.waitFor(() =>
      expect(screen.getAllByText(/^Recorded \(/)).toHaveLength(2),
    );
    // One at a time: the second save waits for the first answer.
    expect(catalogues).toHaveLength(2);
    later.resolve(json(CATALOGUE, 200));
    await vi.waitFor(() => expect(catalogues).toHaveLength(3));
  });

  test("asked for again and not answered, the catalogue it has is kept", async () => {
    let asked = 0;
    const { api } = answering((request) => {
      if (!isCatalogue(request)) {
        return saved();
      }
      asked += 1;
      return asked === 1
        ? json(CATALOGUE, 200)
        : json({ reason: "database_unavailable" }, 503);
    });
    await opened(api);
    await openSpoolInVan();
    save();
    await screen.findByText("Recorded (transaction 77).");
    await vi.waitFor(() => expect(asked).toBe(2));
    await vi.advanceTimersByTimeAsync(0);
    expect(screen.queryByText(say(COULD_NOT_LOAD))).toBeNull();
    expect(screen.getByLabelText(say(YOUR_NAME))).toBeDefined();
  });

  /** Ada's opening of the van's spool, saved and dismissed while the
   * catalogue cannot be read again; later catalogues give `answer`. */
  async function savedThenDismissed() {
    const later = {
      answer: (): Response => json({ reason: "database_unavailable" }, 503),
    };
    let asked = 0;
    const { api } = answering((request) => {
      if (!isCatalogue(request)) {
        return saved();
      }
      asked += 1;
      return asked === 1 ? json(CATALOGUE, 200) : later.answer();
    });
    const outbox = await opened(api);
    await openSpoolInVan();
    save();
    await screen.findByText("Recorded (transaction 77).");
    await vi.waitFor(() => expect(asked).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: say(DISMISS) }));
    expect(screen.queryByText(/^Recorded \(/)).toBeNull();
    const [batch] = outbox.entries.value;
    return { api, outbox, later, key: batch?.body.key ?? "" };
  }

  /** The van's one sealed spool is taken off: opening fills none. */
  function spoolTakenOff() {
    find("cable");
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
    expect(
      (screen.getByLabelText("Spool (305 m)") as HTMLInputElement).value,
    ).toBe("");
  }

  test("a saved batch dismissed while the catalogue cannot be read still counts, until one shows it", async () => {
    const { outbox, later, key } = await savedThenDismissed();
    spoolTakenOff();
    // The next catalogue, asked for after the next save, shows it: the
    // outbox forgets it.
    later.answer = () => json({ ...CATALOGUE, saved: [key] }, 200);
    fireEvent.click(screen.getByRole("button", { name: say(REMOVE) }));
    // The radio's box, sealed at the hub.
    pick(say(WHERE), "10");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    save();
    await vi.waitFor(() =>
      expect(outbox.entries.value.map((each) => each.summary)).toEqual([
        { from: "Hub", to: null },
      ]),
    );
  });

  test("a saved batch another tab's catalogue let storage forget still counts here", async () => {
    const { api, outbox, key } = await savedThenDismissed();
    new Outbox(api).forgetShown([key]);
    window.dispatchEvent(new StorageEvent("storage", { key: OUTBOX_KEY }));
    expect(JSON.parse(localStorage.getItem(OUTBOX_KEY) ?? "")).toMatchObject({
      entries: [],
    });
    expect(outbox.shown()).toEqual([]);
    spoolTakenOff();
    // This tab's own catalogue shows it: forgotten here too.
    outbox.forgetShown([key]);
    expect(outbox.entries.value).toEqual([]);
  });

  test("opening, an item of no package is not added", async () => {
    const loose = {
      ...CATALOGUE,
      items: CATALOGUE.items.map((item) =>
        item.id === 100 ? { ...item, packages: [] } : item,
      ),
    };
    await opened(answering(() => json(loose, 200)).api);
    fireEvent.click(screen.getByLabelText(say(KIND_WORDS.open)));
    pick(say(WHERE), "10");
    await scan("B3ZT9W");
    await screen.findByText("LiteBeam 5AC comes in no package to open.");
    expect(screen.getByText(say(NOTHING_IN_CART))).toBeDefined();
  });

  test("a place's label asks from or to, the empty side first", async () => {
    await opened();
    await scan("S4HELF");
    await screen.findByText("Hub › Shelf A: from here, or to here?");
    screen.getByRole("group", { name: "Hub › Shelf A" });
    const sides = screen
      .getAllByRole("button", { name: /^(From|To)$/ })
      .map((button) => button.textContent);
    expect(sides).toEqual([say(FROM), say(TO)]);
    fireEvent.click(screen.getByRole("button", { name: say(TO) }));
    expect(selected(say(TO))).toBe("11");
    expect(screen.queryByRole("button", { name: say(FROM) })).toBeNull();
  });

  test("a place's label sets the only side a kind has, at once", async () => {
    storeCart({ ...freshCart(), kind: "receipt" });
    await opened();
    await scan("S4HELF");
    await screen.findByText("Hub › Shelf A: to here.");
    expect(selected(say(TO))).toBe("11");
    expect(screen.queryByRole("button", { name: say(FROM) })).toBeNull();
  });

  test("for an install, a place's label is where it comes from", async () => {
    storeCart({ ...freshCart(), kind: "install" });
    await opened();
    await scan("S4HELF");
    await screen.findByText("Hub › Shelf A: from here.");
    expect(selected(say(FROM))).toBe("11");
  });

  test("opening, a place's label is where", async () => {
    storeCart({ ...freshCart(), kind: "open" });
    await opened();
    await scan("S4HELF");
    await screen.findByText("Hub › Shelf A: here.");
    expect(selected(say(WHERE))).toBe("11");
  });

  test("an item read next puts a place's question away", async () => {
    await opened();
    await scan("S4HELF");
    await screen.findByRole("button", { name: say(FROM) });
    await scan("B3ZT9W");
    await screen.findByText("Added one: LiteBeam 5AC.");
    expect(screen.queryByRole("button", { name: say(FROM) })).toBeNull();
  });

  test("a stray code leaves a place's question to answer", async () => {
    await opened();
    await scan("S4HELF");
    await screen.findByRole("button", { name: say(FROM) });
    await scan("NOT-OURS");
    await screen.findByText("That label isn't in the inventory (NOT-OURS).");
    screen.getByRole("button", { name: say(FROM) });
  });

  test("a scanner gun's place label asks too", async () => {
    open();
    await ready();
    find("s4helf");
    fireEvent.keyDown(screen.getByLabelText(say(FIND)), { key: "Enter" });
    fireEvent.click(await screen.findByRole("button", { name: say(TO) }));
    expect(selected(say(TO))).toBe("11");
  });

  test("an unknown label says so, and Done closes the camera", async () => {
    await opened();
    await scan("OLD-FORM");
    await screen.findByText("That label isn't in the inventory (OLD-FORM).");
    fireEvent.click(screen.getByRole("button", { name: say(DONE) }));
    screen.getByRole("button", { name: say(SCAN_WITH_CAMERA) });
  });
});
