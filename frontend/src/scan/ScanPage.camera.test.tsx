import { act, fireEvent, screen, within } from "@testing-library/preact";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { UPGRADE } from "../shared/build";
import { say } from "../shared/i18n";
import { STORAGE_KEY as OUTBOX_KEY, Outbox } from "../shared/outbox";
import { pass } from "../testing/camera";
import { CATALOGUE } from "../testing/catalogue";
import { answering, deferred, json, saved } from "../testing/fetch";
import { stubInView } from "../testing/in-view";
import {
  camera,
  fillMove,
  find,
  isCatalogue,
  open,
  pick,
  ready,
  save,
  selected,
  server,
} from "../testing/scan-page";
import { refusingRemovals, refusingWrites } from "../testing/storage";
import { BACK_TO_CAMERA } from "./BackToCamera";
import {
  CART,
  FROM,
  NOT_ADDED,
  NOT_KEEPING,
  NOTHING_IN_CART,
  SCAN_WITH_CAMERA,
  TO,
  WHERE,
  YOUR_NAME,
} from "./CartForm";
import { REMOVE } from "./CartLine";
import { openCamera } from "./camera";
import {
  STORAGE_KEY as CART_KEY,
  freshCart,
  KIND_WORDS,
  storeCart,
} from "./cart";
import { FIND } from "./Finder";
import { DISMISS } from "./OutboxList";
import {
  CAMERA,
  DONE,
  NOT_ALLOWED,
  POINT,
  RELOAD_PAGE,
  TYPING,
} from "./Scanner";
import { COULD_NOT_LOAD } from "./ScanPage";

/** UPGRADE_TO_SAVE as shown, its link's words in it. */
const UPGRADE_WORDS = "Scanner updated. Upgrade to keep saving.";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock(import("./camera"), async (actual) => ({
  ...(await actual()),
  openCamera: vi.fn(),
}));

/** The cart's lines' item names, top to bottom. */
const lineNames = () =>
  [...document.querySelectorAll("li > div > strong")].map(
    (each) => each.textContent,
  );

/** Whether `later` comes after `earlier` on the page. */
const follows = (earlier: Node, later: Node) =>
  (earlier.compareDocumentPosition(later) &
    Node.DOCUMENT_POSITION_FOLLOWING) !==
  0;

describe("scanning labels with the camera", () => {
  // The camera's clock, faked: scan lets a read's time pass (testing/setup.ts
  // makes it real again).
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  /** Shows the camera these labels, and lets the next read take them. */
  async function scan(...codes: string[]) {
    camera.state.inView = codes;
    await pass();
  }

  /** The volunteer opens the camera. */
  function startCamera() {
    fireEvent.click(
      screen.getByRole("button", { name: say(SCAN_WITH_CAMERA) }),
    );
  }

  async function opened(api = server().api) {
    const outbox = open(api);
    await ready();
    camera.state.inView = [];
    startCamera();
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

  test("an item's label adds nothing to loose that reads as no amount, and says so", async () => {
    await opened();
    await scan("B3ZT9W");
    await screen.findByText("Added one: LiteBeam 5AC.");
    const loose = screen.getByLabelText<HTMLInputElement>("Loose units");
    fireEvent.input(loose, { target: { value: "2,5" } });
    // Out of view a while, then shown again: a new scan.
    await scan();
    await vi.advanceTimersByTimeAsync(2000);
    await scan("B3ZT9W");
    await screen.findByText(say(NOT_ADDED));
    expect(loose.value).toBe("2,5");
  });

  test("a refusal under the camera clears once the line it is about changes, and no other", async () => {
    await opened();
    await scan("K7QX2M");
    await screen.findByText("Added a Spool: Cat6 cable.");
    await scan("B3ZT9W");
    await screen.findByText("Added one: LiteBeam 5AC.");
    const loose = screen.getByLabelText<HTMLInputElement>("Loose units");
    fireEvent.input(loose, { target: { value: "2,5" } });
    await scan();
    await vi.advanceTimersByTimeAsync(2000);
    await scan("B3ZT9W");
    const status = (await screen.findByText(say(NOT_ADDED))) as HTMLElement;
    // Another line changed: the refusal stays.
    fireEvent.input(screen.getByLabelText("Loose m"), {
      target: { value: "3" },
    });
    expect(status.textContent).toBe(say(NOT_ADDED));
    // Its own line fixed: cleared, the live region still there.
    fireEvent.input(loose, { target: { value: "2" } });
    expect(status.textContent).toBe("");
    expect(status.getAttribute("role")).toBe("status");
    expect(status.isConnected).toBe(true);
  });

  test("a new line goes first, under the camera and the search; a repeat adds to its line where it is", async () => {
    await opened();
    await scan("B3ZT9W");
    await screen.findByText("Added one: LiteBeam 5AC.");
    await scan("K7QX2M");
    await screen.findByText("Added a Spool: Cat6 cable.");
    expect(lineNames()).toEqual(["Cat6 cable", "LiteBeam 5AC"]);
    // Out of view a while, then shown again: a new scan.
    await scan();
    await vi.advanceTimersByTimeAsync(2000);
    await scan("B3ZT9W");
    await vi.waitFor(() =>
      expect(screen.getByLabelText<HTMLInputElement>("Loose units").value).toBe(
        "2",
      ),
    );
    expect(lineNames()).toEqual(["Cat6 cable", "LiteBeam 5AC"]);
    // The camera, then the search, then the cart.
    const [camera, search, cart] = [
      screen.getByRole("region", { name: say(CAMERA) }),
      screen.getByLabelText(say(FIND)),
      screen.getByRole("heading", { name: say(CART) }),
    ];
    expect(follows(camera, search)).toBe(true);
    expect(follows(search, cart)).toBe(true);
  });

  test("a label the camera reads leaves the item search's words and matches; a pick or a typed code empties it", async () => {
    await opened();
    const search = screen.getByLabelText<HTMLInputElement>(say(FIND));
    find("cable");
    screen.getByRole("button", { name: "Cat6 cable" });
    await scan("B3ZT9W");
    await screen.findByText("Added one: LiteBeam 5AC.");
    expect(search.value).toBe("cable");
    screen.getByRole("button", { name: "Cat6 cable" });
    // A pick from its list.
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
    expect(search.value).toBe("");
    // A code typed, as a scanner gun types one.
    find("b3zt9w");
    fireEvent.keyDown(search, { key: "Enter" });
    expect(search.value).toBe("");
  });

  test("Back to camera is offered only while the camera is open and off the screen", async () => {
    const view = stubInView();
    await opened();
    const picture = document.querySelector("video") as HTMLVideoElement;
    await vi.waitFor(() => expect(view.watched()).toBe(1));
    view.inView(picture, 0.5);
    const back = () =>
      screen.queryByRole("button", { name: say(BACK_TO_CAMERA) });
    expect(back()).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: say(DONE) }));
    expect(back()).toBeNull();
    expect(view.watched()).toBe(0);
  });

  test("opening, the camera is scrolled into view once, and off the screen it adds nothing until it is back", async () => {
    const view = stubInView();
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    await opened();
    const picture = document.querySelector("video") as HTMLVideoElement;
    expect(scroll.mock.contexts).toEqual([picture]);
    await vi.waitFor(() => expect(view.watched()).toBe(1));
    view.inView(picture, 0.5);
    await scan("B3ZT9W");
    await pass(2);
    expect(lineNames()).toEqual([]);
    view.inView(picture, 1);
    await screen.findByText("Added one: LiteBeam 5AC.");
    expect(lineNames()).toEqual(["LiteBeam 5AC"]);
    // Not scrolled again after a scan.
    expect(scroll).toHaveBeenCalledTimes(1);
  });

  test("while a text box has focus, the camera adds nothing; once focus leaves it, it reads again", async () => {
    await opened();
    const search = screen.getByLabelText<HTMLInputElement>(say(FIND));
    search.focus();
    await scan("B3ZT9W");
    await pass(2);
    expect(lineNames()).toEqual([]);
    search.blur();
    await screen.findByText("Added one: LiteBeam 5AC.");
    expect(lineNames()).toEqual(["LiteBeam 5AC"]);
  });

  test("while a text box has focus, the camera's line says reading waits, over a refusal it says again after", async () => {
    await opened();
    await scan("B3ZT9W");
    await screen.findByText("Added one: LiteBeam 5AC.");
    const loose = screen.getByLabelText<HTMLInputElement>("Loose units");
    fireEvent.input(loose, { target: { value: "2,5" } });
    await scan();
    await vi.advanceTimersByTimeAsync(2000);
    await scan("B3ZT9W");
    const status = (await screen.findByText(say(NOT_ADDED))) as HTMLElement;
    const search = screen.getByLabelText<HTMLInputElement>(say(FIND));
    act(() => search.focus());
    expect(status.textContent).toBe(say(TYPING));
    act(() => search.blur());
    expect(status.textContent).toBe(say(NOT_ADDED));
    // Fixed while typing in its box: cleared, so not said again.
    act(() => loose.focus());
    fireEvent.input(loose, { target: { value: "2" } });
    expect(status.textContent).toBe(say(TYPING));
    act(() => loose.blur());
    expect(status.textContent).toBe("");
  });

  test("a tap on the camera's picture takes focus from a text box, so it reads again", async () => {
    await opened();
    const search = screen.getByLabelText<HTMLInputElement>(say(FIND));
    act(() => search.focus());
    await screen.findByText(say(TYPING));
    await scan("B3ZT9W");
    await pass(2);
    expect(lineNames()).toEqual([]);
    fireEvent.click(document.querySelector("video") as HTMLVideoElement);
    expect(document.activeElement).not.toBe(search);
    await screen.findByText("Added one: LiteBeam 5AC.");
  });

  test("too old, the camera's line says to upgrade, over typing and what a label did", async () => {
    const outbox = await opened();
    await scan("B3ZT9W");
    const status = (await screen.findByText(
      "Added one: LiteBeam 5AC.",
    )) as HTMLElement;
    act(() => outbox.builds.learn("too_old"));
    expect(status.textContent).toBe(UPGRADE_WORDS);
    const search = screen.getByLabelText<HTMLInputElement>(say(FIND));
    act(() => search.focus());
    expect(status.textContent).toBe(UPGRADE_WORDS);
    act(() => search.blur());
    expect(status.textContent).toBe(UPGRADE_WORDS);
    expect(status.getAttribute("role")).toBe("status");
  });

  test("too old, the camera reads no label, till an upgrade", async () => {
    const outbox = await opened();
    act(() => outbox.builds.learn("too_old"));
    await scan("B3ZT9W");
    await pass(2);
    expect(lineNames()).toEqual([]);
    const camera = screen.getByRole("region", { name: say(CAMERA) });
    expect(within(camera).getByRole("status").textContent).toBe(UPGRADE_WORDS);
  });

  test("too old, Upgrade on the camera's line brings the alert's Upgrade button into view and focus", async () => {
    const outbox = await opened();
    act(() => outbox.builds.learn("too_old"));
    const scroll = vi.spyOn(HTMLElement.prototype, "scrollIntoView");
    const camera = screen.getByRole("region", { name: say(CAMERA) });
    const link = within(camera).getByRole("link", { name: say(UPGRADE) });
    const button = within(screen.getByRole("alert")).getByRole("button", {
      name: say(UPGRADE),
    });
    expect(link.getAttribute("href")).toBe(`#${button.id}`);
    fireEvent.click(link);
    expect(scroll.mock.contexts).toEqual([button]);
    expect(document.activeElement).toBe(button);
  });

  test("Enter in the item search leaves focus there, for the next code", async () => {
    await opened();
    const search = screen.getByLabelText<HTMLInputElement>(say(FIND));
    search.focus();
    find("b3zt9w");
    fireEvent.keyDown(search, { key: "Enter" });
    expect(lineNames()).toEqual(["LiteBeam 5AC"]);
    expect(document.activeElement).toBe(search);
    expect(screen.getByText(say(TYPING))).toBeTruthy();
  });

  test("what a label added stays said as its line changes", async () => {
    await opened();
    await scan("B3ZT9W");
    const status = await screen.findByText("Added one: LiteBeam 5AC.");
    fireEvent.input(screen.getByLabelText("Loose units"), {
      target: { value: "3" },
    });
    expect(status.textContent).toBe("Added one: LiteBeam 5AC.");
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
    // One spool sealed in the van (testing/catalogue.ts); opened, it waits in
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
    // Once by Record, to check the cart against (inventory-tng-h7ua), and
    // once after it is saved.
    await vi.waitFor(() => expect(asked()).toBe(3));
    // Asked whether it shows the batch saved.
    const [, , again] = sent.filter(isCatalogue);
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
      // The first after a save: Record's own check is answered at once.
      return catalogues.length === 3 ? later.promise : json(CATALOGUE, 200);
    });
    await opened(api);
    await openSpoolInVan();
    save();
    // Asked for again once saved, after Record's own check.
    await vi.waitFor(() => expect(catalogues).toHaveLength(3));
    // The radio's box, sealed at the hub.
    pick(say(WHERE), "10");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    save();
    await vi.waitFor(() =>
      expect(screen.getAllByText(/^Recorded \(/)).toHaveLength(2),
    );
    // One at a time: the second save's refresh waits for the first's
    // answer; Record's check does not.
    expect(catalogues).toHaveLength(4);
    later.resolve(json(CATALOGUE, 200));
    await vi.waitFor(() => expect(catalogues).toHaveLength(5));
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
    // Record's check, unanswered, queued it as before (inventory-tng-h7ua).
    await vi.waitFor(() => expect(asked).toBe(3));
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
    await vi.waitFor(() => expect(asked).toBe(3));
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

  test("a camera not allowed offers to reload the page", async () => {
    vi.mocked(openCamera).mockRejectedValueOnce(
      new DOMException("no", "NotAllowedError"),
    );
    const reload = vi.fn();
    open(server().api, reload);
    await ready();
    startCamera();
    await screen.findByText(say(NOT_ALLOWED.bold));
    fireEvent.click(screen.getByRole("button", { name: say(RELOAD_PAGE) }));
    expect(reload).toHaveBeenCalledOnce();
  });

  test("a camera not allowed, while the cart is not kept, says to record it before a reload", async () => {
    vi.mocked(openCamera).mockRejectedValueOnce(
      new DOMException("no", "NotAllowedError"),
    );
    const reload = vi.fn();
    open(server().api, reload);
    await ready();
    const setItem = refusingWrites(CART_KEY);
    fillMove();
    screen.getByText(say(NOT_KEEPING));
    startCamera();
    await screen.findByText(say(NOT_ALLOWED.notKept));
    // Still offered: the volunteer may record the cart first.
    screen.getByRole("button", { name: say(RELOAD_PAGE) });
    // Kept again, the usual hint.
    setItem.mockRestore();
    pick(say(YOUR_NAME), "2");
    await screen.findByText(say(NOT_ALLOWED.plain));
    expect(screen.queryByText(say(NOT_KEEPING))).toBeNull();
  });

  test("a camera not allowed, with an empty cart the browser would not write but holds none of, gives the usual hint", async () => {
    vi.mocked(openCamera).mockRejectedValueOnce(
      new DOMException("no", "NotAllowedError"),
    );
    open();
    await ready();
    refusingWrites(CART_KEY);
    pick(say(YOUR_NAME), "1");
    // None stored, a reload brings an empty cart: nothing to record first.
    expect(screen.queryByText(say(NOT_KEEPING))).toBeNull();
    startCamera();
    await screen.findByText(say(NOT_ALLOWED.plain));
  });

  test("a camera not allowed, with the cart emptied but its lines still stored, says to record it before a reload (inventory-tng-xm07)", async () => {
    vi.mocked(openCamera).mockRejectedValueOnce(
      new DOMException("no", "NotAllowedError"),
    );
    open();
    await ready();
    fillMove();
    refusingWrites(CART_KEY);
    refusingRemovals(CART_KEY);
    fireEvent.click(screen.getByRole("button", { name: say(REMOVE) }));
    screen.getByText(say(NOTHING_IN_CART));
    screen.getByText(say(NOT_KEEPING));
    startCamera();
    await screen.findByText(say(NOT_ALLOWED.notKept));
  });
});
