import { fireEvent, render, screen, within } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { BUILD_KEY, Builds } from "./build";
import { openCamera } from "./camera";
import { addOne, STORAGE_KEY as CART_KEY, freshCart, storeCart } from "./cart";
import { PRINTED_LENGTH } from "./OpenPackageCard";
import { STORAGE_KEY as OUTBOX_KEY, Outbox } from "./outbox";
import { ScanPage } from "./ScanPage";
import { MOST_SECONDS_TO_SAVE, telemetry } from "./telemetry";
import { fakeCamera } from "./test-camera";
import { CATALOGUE, WITH_CAFE } from "./test-catalogue";
import { answering, deferred, json, saved, TOO_OLD } from "./test-fetch";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));
const camera = fakeCamera();
vi.mocked(openCamera).mockImplementation(camera.open);

/** An API with the test catalogue, saving batches as transaction 77. */
function server(save: () => Response | Promise<Response> = () => saved()) {
  return answering((request) =>
    new URL(request.url).pathname === "/api/catalogue"
      ? json(CATALOGUE, 200)
      : save(),
  );
}

function open(api = server().api) {
  const outbox = new Outbox(api);
  render(<ScanPage api={api} outbox={outbox} reload={vi.fn()} />);
  return outbox;
}

const IDLE = CATALOGUE.settings.name_check_minutes * 60_000;

const pick = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

const selected = (label: string) =>
  (screen.getByLabelText(label) as HTMLSelectElement).value;

const isCatalogue = (request: Request) =>
  new URL(request.url).pathname === "/api/catalogue";

const saves = (sent: Request[]) =>
  sent.filter((request) => request.url.endsWith("/api/transactions"));

async function ready() {
  await screen.findByLabelText("Your name");
}

function find(text: string) {
  fireEvent.input(screen.getByLabelText(/Find an item/), {
    target: { value: text },
  });
}

/** A move from the hub to the van, of one cable, by Ada. */
function fillMove() {
  pick("Your name", "1");
  pick("From", "10");
  pick("To", "12");
  find("cable");
  fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
}

const save = () =>
  fireEvent.click(screen.getByRole("button", { name: "Save" }));

/** A cart that was last used this long ago, by Ada. */
const usedAgo = (ms: number) =>
  storeCart({ ...freshCart(), volunteerId: 1, usedAt: Date.now() - ms });

describe("loading", () => {
  test("the catalogue fills the choices", async () => {
    open();
    await ready();
    const names = within(screen.getByLabelText("Your name")).getAllByRole(
      "option",
    );
    expect(names.map((option) => option.textContent)).toEqual([
      "Pick your name",
      "Ada",
      "Grace",
    ]);
    const places = within(screen.getByLabelText("From")).getAllByRole("option");
    expect(places.map((option) => option.textContent)).toEqual([
      "Pick a place",
      "Hub",
      "Hub › Shelf A",
      "Van",
    ]);
  });

  test("an older load answering after a newer one is not shown", async () => {
    const older = deferred();
    const first = answering(() => older.promise).api;
    const outbox = new Outbox(first);
    const { rerender } = render(
      <ScanPage api={first} outbox={outbox} reload={vi.fn()} />,
    );
    const newer = {
      ...CATALOGUE,
      volunteers: [{ id: 3, name: "Hedy" }],
    };
    const second = answering((request) =>
      isCatalogue(request) ? json(newer, 200) : saved(),
    ).api;
    rerender(<ScanPage api={second} outbox={outbox} reload={vi.fn()} />);
    await screen.findByRole("option", { name: "Hedy" });
    older.resolve(json(CATALOGUE, 200));
    await new Promise((done) => setTimeout(done, 0));
    expect(screen.queryByRole("option", { name: "Ada" })).toBeNull();
  });

  test("an unregistered browser is sent to register", async () => {
    const { api } = answering(() => json({ reason: "unregistered" }, 403));
    open(api);
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("isn't registered");
    expect(within(alert).getByRole("link").getAttribute("href")).toBe(
      "/register",
    );
  });

  test("a withdrawn registration says so", async () => {
    const { api } = answering(() => json({ reason: "revoked" }, 403));
    open(api);
    expect((await screen.findByRole("alert")).textContent).toContain(
      "registration was withdrawn",
    );
  });

  test("a failed load can be tried again", async () => {
    let first = true;
    const { api } = answering(() => {
      if (first) {
        first = false;
        return json({ reason: "database_unavailable" }, 503);
      }
      return json(CATALOGUE, 200);
    });
    open(api);
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await ready();
  });

  test("a page left open fetches a stale catalogue again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let catalogue = CATALOGUE;
    const { api } = answering(() => json(catalogue, 200));
    open(api);
    await ready();
    catalogue = {
      ...CATALOGUE,
      volunteers: [...CATALOGUE.volunteers, { id: 3, name: "Hedy" }],
    };
    await vi.advanceTimersByTimeAsync(11 * 60_000);
    expect(await screen.findByRole("option", { name: "Hedy" })).toBeDefined();
  });
});

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
    pick("From", "10");
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
  const NOTHING_LEFT = "Nothing usable left on it";
  const ticked = () =>
    (screen.getByLabelText(NOTHING_LEFT) as HTMLInputElement).checked;
  const note = () =>
    screen.getByText(
      (_, element) => element?.getAttribute("aria-live") === "polite",
    ).textContent;

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
      "Your cut is more than the Spool shows, so it's ticked. It's saved as you typed it, and an administrator will check the Spool.",
    );
    // Back to a cut that leaves some: unticked again.
    fireEvent.input(field, { target: { value: "20" } });
    expect(ticked()).toBe(false);
    // Moved whole: no cut, no box.
    fireEvent.click(screen.getByLabelText("All of it, moved still open"));
    expect(field.value).toBe("120");
    expect(field.disabled).toBe(true);
    expect(screen.queryByLabelText(NOTHING_LEFT)).toBeNull();
    fireEvent.click(screen.getByLabelText("All of it, moved still open"));
    expect(field.value).toBe("");
    // Another place: what was taken from this one's packages goes.
    fireEvent.input(field, { target: { value: "20" } });
    pick("From", "12");
    pick("From", "10");
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
    fireEvent.click(
      screen.getByRole("button", { name: "Shows the wrong length?" }),
    );
    const field = screen.getByLabelText(now) as HTMLInputElement;
    fireEvent.input(field, { target: { value: "90" } });
    expect(field.value).toBe("90");
    // In metres: how to read the length printed on it (inventory-tng-pg7).
    const hint = document.getElementById(
      field.getAttribute("aria-describedby") ?? "",
    );
    expect(hint?.textContent).toBe(PRINTED_LENGTH);
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
      within(cards[2] as HTMLElement).getByText("Shows nothing left"),
    ).toBeDefined();
    expect(
      within(cards[0] as HTMLElement).queryByText("Shows nothing left"),
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
    fireEvent.click(screen.getByLabelText("Opening packages"));
    expect(screen.getByLabelText("Where")).toBeDefined();
    expect(screen.queryByLabelText("To")).toBeNull();
    // Only whole packages are opened: the radio's box sealed here, not a
    // radio.
    pick("Where", "10");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect(
      (screen.getByLabelText("Box (4 each)") as HTMLInputElement).value,
    ).toBe("1");
  });

  test("opening, a place picked fills what was added before and not typed", async () => {
    open();
    await ready();
    fireEvent.click(screen.getByLabelText("Opening packages"));
    const box = () => (screen.getByLabelText(/^Box/) as HTMLInputElement).value;
    // No place yet: the radio's one package (inventory-tng-prp).
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect(box()).toBe("1");
    // None sealed at the van; one at the hub.
    pick("Where", "12");
    expect(box()).toBe("");
    pick("Where", "10");
    expect(box()).toBe("1");
    // Picked again, then through a place with none: still two.
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    pick("Where", "12");
    pick("Where", "10");
    expect(box()).toBe("2");
    // Switched to another kind and back, the place kept: as it was.
    fireEvent.click(screen.getByLabelText("Moving stock"));
    fireEvent.click(screen.getByLabelText("Opening packages"));
    expect(box()).toBe("2");
    // Typed into: never filled again.
    fireEvent.input(screen.getByLabelText(/^Box/), { target: { value: "3" } });
    pick("Where", "12");
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
    fireEvent.click(screen.getByLabelText("Opening packages"));
    const field = (name: RegExp) =>
      (screen.getByLabelText(name) as HTMLInputElement).value;
    // At the van only the old crate is sealed (inventory-tng-f9h).
    pick("Where", "12");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect([field(/^Box/), field(/^Old crate/)]).toEqual(["", "1"]);
    // At the hub both are: neither is picked for the volunteer.
    pick("Where", "10");
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

  test("a move is saved, sent, and a new cart keeps the choices", async () => {
    const record = vi.spyOn(telemetry, "record");
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    pick("Your name", "1");
    pick("From", "10");
    pick("To", "12");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    fireEvent.input(screen.getByLabelText("Box (4 each)"), {
      target: { value: "1" },
    });
    expect(screen.getByText("5 each in all")).toBeDefined();
    save();

    expect((await screen.findByRole("status")).textContent).toBe(
      "Saved: Move: 1 item from Hub to Van. A new cart is ready.",
    );
    await screen.findByText("In the inventory (transaction 77).");
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
    expect(screen.getByText("Nothing in the cart yet.")).toBeDefined();
    expect(selected("Your name")).toBe("1");
    expect(selected("To")).toBe("12");
  });

  test("a scanned label adds its package; Enter adds the only match", async () => {
    open();
    await ready();
    find("K7QX2M");
    fireEvent.keyDown(screen.getByLabelText(/Find an item/), { key: "Enter" });
    expect(screen.getByText("305 m in all")).toBeDefined();
    find("litebeam");
    fireEvent.keyDown(screen.getByLabelText(/Find an item/), { key: "Enter" });
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
    const finder = screen.getByLabelText(/Find an item/);
    fireEvent.keyDown(finder, { key: "a" });
    fireEvent.keyDown(finder, { key: "Enter" });
    expect(screen.getByText("Nothing in the cart yet.")).toBeDefined();
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
      "Pick your name.",
      "Pick where it comes from.",
      "Pick where it goes.",
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
    fireEvent.click(screen.getByLabelText("Taking it to an install"));
    expect(screen.getByLabelText(/install number/)).toBeDefined();
    expect(screen.queryByLabelText("To")).toBeNull();
    expect(screen.getByLabelText("From")).toBeDefined();
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
      screen.getAllByRole("button", { name: "Remove" })[1] as Element,
    );
    expect(screen.queryByText("1 each in all")).toBeNull();
    expect(screen.getByText("2 m in all")).toBeDefined();
    fireEvent.click(screen.getByLabelText("Taking it to an install"));
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

  test("saved with no connection, the batch waits in the outbox", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { api } = server(() =>
      Promise.reject(new TypeError("Failed to fetch")),
    );
    const outbox = open(api);
    await ready();
    pick("Your name", "2");
    fireEvent.click(screen.getByLabelText("Receiving stock"));
    pick("To", "11");
    find("cable");
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
    save();
    await screen.findByText(
      "No answer from the inventory. Trying again shortly.",
    );
    expect(outbox.entries.value[0]).toMatchObject({
      state: "waiting",
      what: "Receipt: 1 item to Hub › Shelf A",
    });
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
    storeCart({ ...freshCart(), volunteerId: 1, fromId: 10, toId: 12 });
    save();
    expect((await screen.findByRole("alert")).textContent).toContain(
      "changed in another tab",
    );
    expect(screen.getByText("Nothing in the cart yet.")).toBeDefined();
    expect(saves(sent)).toEqual([]);
  });

  test("a retired name or place in a stored cart shows as not picked", async () => {
    storeCart({ ...freshCart(), volunteerId: 99, toId: 98 });
    open();
    await ready();
    expect(selected("Your name")).toBe("");
    expect(selected("To")).toBe("");
  });
});

describe("whose name it is", () => {
  test("Save shows whose name it is saved under, without asking", async () => {
    const { api, sent } = server();
    open(api);
    await ready();
    fillMove();
    pick("Your name", "2");
    expect(screen.getByText(/Saving as/).textContent).toBe(
      "Saving as Grace. Change",
    );
    expect(screen.queryByText(/Are you/)).toBeNull();
    save();
    await screen.findByText("In the inventory (transaction 77).");
    expect(saves(sent)).toHaveLength(1);
  });

  test("after a while unused, the page asks whether it is still them", async () => {
    usedAgo(IDLE + 1000);
    open();
    await ready();
    expect(screen.getByText(/Are you/).textContent).toContain("Are you Ada?");
  });

  test("a cart used recently does not ask", async () => {
    usedAgo(0);
    open();
    await ready();
    expect(screen.queryByText(/Are you/)).toBeNull();
  });

  test("that's me keeps the name and stops asking", async () => {
    usedAgo(IDLE + 1000);
    open();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "That's me" }));
    expect(screen.queryByText(/Are you/)).toBeNull();
    expect(selected("Your name")).toBe("1");
    // And the cart counts as used now, so a reload does not ask again.
    document.body.innerHTML = "";
    open();
    await ready();
    expect(screen.queryByText(/Are you/)).toBeNull();
  });

  test("that's me keeps the saved message on show", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    open();
    await ready();
    fillMove();
    save();
    await screen.findByText(/A new cart is ready/);
    await vi.advanceTimersByTimeAsync(IDLE + 60_000);
    fireEvent.click(await screen.findByRole("button", { name: "That's me" }));
    expect(screen.getByText(/A new cart is ready/)).toBeDefined();
  });

  test("not me clears the name and goes to the list", async () => {
    usedAgo(IDLE + 1000);
    open();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: "Not me" }));
    expect(selected("Your name")).toBe("");
    expect(document.activeElement).toBe(screen.getByLabelText("Your name"));
    expect(screen.queryByText(/Saving as/)).toBeNull();
  });

  test("coming back to the page after a while asks", async () => {
    usedAgo(0);
    open();
    await ready();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(Date.now() + IDLE + 1000);
    await vi.waitFor(() => {
      document.dispatchEvent(new Event("visibilitychange"));
      expect(screen.getByText(/Are you/)).toBeDefined();
    });
  });

  test("a page left open asks once the time has passed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    usedAgo(0);
    open();
    await ready();
    expect(screen.queryByText(/Are you/)).toBeNull();
    await vi.advanceTimersByTimeAsync(IDLE + 60_000);
    expect(screen.getByText(/Are you/)).toBeDefined();
  });

  test("a page left open never asks when administrators set 0", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    usedAgo(0);
    const never = { ...CATALOGUE, settings: { name_check_minutes: 0 } };
    open(answering(() => json(never, 200)).api);
    await ready();
    await vi.advanceTimersByTimeAsync(IDLE + 60_000);
    expect(screen.queryByText(/Are you/)).toBeNull();
  });
});

describe("scanning labels with the camera", () => {
  /** Shows the camera these labels: it reads them within a moment. */
  function scan(...codes: string[]) {
    camera.state.inView = codes;
  }

  async function opened(api = server().api) {
    const outbox = open(api);
    await ready();
    camera.state.inView = [];
    fireEvent.click(
      screen.getByRole("button", { name: "Scan with the camera" }),
    );
    await screen.findByText("Point the camera at a label.");
    return outbox;
  }

  /** Ada opens the van's sealed spool, by its label. */
  async function openSpoolInVan() {
    pick("Your name", "1");
    fireEvent.click(screen.getByLabelText("Opening packages"));
    pick("Where", "12");
    scan("K7QX2M");
    await screen.findByText("Added a Spool: Cat6 cable.");
  }

  test("an item's label adds one, and a package's label a package", async () => {
    const vibrate = vi.fn();
    Object.assign(navigator, { vibrate });
    await opened();
    scan("B3ZT9W");
    await screen.findByText("Added one: LiteBeam 5AC.");
    expect(vibrate).toHaveBeenCalled();
    scan("K7QX2M");
    await screen.findByText("Added a Spool: Cat6 cable.");
    expect(screen.queryByText("Nothing in the cart yet.")).toBeNull();
  });

  test("opening, an item's label asks which package if none is sealed there", async () => {
    await opened();
    fireEvent.click(screen.getByLabelText("Opening packages"));
    // The radio's box is sealed at the hub, not the van.
    pick("Where", "12");
    scan("B3ZT9W");
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
    await screen.findByText(/^Saved: Opening/);
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
    await screen.findByText("In the inventory (transaction 77).");
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
    await screen.findByText(/^Saved: Opening/);
    // The radio's box, sealed at the hub.
    pick("Where", "10");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    save();
    await vi.waitFor(() =>
      expect(screen.getAllByText(/^In the inventory/)).toHaveLength(2),
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
    await screen.findByText("In the inventory (transaction 77).");
    await vi.waitFor(() => expect(asked).toBe(2));
    await new Promise((done) => setTimeout(done, 0));
    expect(screen.queryByText("Couldn't load the catalogue.")).toBeNull();
    expect(screen.getByLabelText("Your name")).toBeDefined();
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
    await screen.findByText("In the inventory (transaction 77).");
    await vi.waitFor(() => expect(asked).toBe(2));
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/^In the inventory/)).toBeNull();
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
    fireEvent.click(screen.getByRole("button", { name: "Remove" }));
    // The radio's box, sealed at the hub.
    pick("Where", "10");
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    save();
    await vi.waitFor(() =>
      expect(outbox.entries.value.map((each) => each.what)).toEqual([
        "Opening: 1 item at Hub",
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
    fireEvent.click(screen.getByLabelText("Opening packages"));
    pick("Where", "10");
    scan("B3ZT9W");
    await screen.findByText("LiteBeam 5AC comes in no package to open.");
    expect(screen.getByText("Nothing in the cart yet.")).toBeDefined();
  });

  test("a place's label asks from or to, the empty side first", async () => {
    await opened();
    scan("S4HELF");
    await screen.findByText("Hub › Shelf A: from here, or to here?");
    screen.getByRole("group", { name: "Hub › Shelf A" });
    const sides = screen
      .getAllByRole("button", { name: /^(From|To)$/ })
      .map((button) => button.textContent);
    expect(sides).toEqual(["From", "To"]);
    fireEvent.click(screen.getByRole("button", { name: "To" }));
    expect(selected("To")).toBe("11");
    expect(screen.queryByRole("button", { name: "From" })).toBeNull();
  });

  test("a place's label sets the only side a kind has, at once", async () => {
    storeCart({ ...freshCart(), kind: "receipt" });
    await opened();
    scan("S4HELF");
    await screen.findByText("Hub › Shelf A: to here.");
    expect(selected("To")).toBe("11");
    expect(screen.queryByRole("button", { name: "From" })).toBeNull();
  });

  test("for an install, a place's label is where it comes from", async () => {
    storeCart({ ...freshCart(), kind: "install" });
    await opened();
    scan("S4HELF");
    await screen.findByText("Hub › Shelf A: from here.");
    expect(selected("From")).toBe("11");
  });

  test("opening, a place's label is where", async () => {
    storeCart({ ...freshCart(), kind: "open" });
    await opened();
    scan("S4HELF");
    await screen.findByText("Hub › Shelf A: here.");
    expect(selected("Where")).toBe("11");
  });

  test("an item read next puts a place's question away", async () => {
    await opened();
    scan("S4HELF");
    await screen.findByRole("button", { name: "From" });
    scan("B3ZT9W");
    await screen.findByText("Added one: LiteBeam 5AC.");
    expect(screen.queryByRole("button", { name: "From" })).toBeNull();
  });

  test("a stray code leaves a place's question to answer", async () => {
    await opened();
    scan("S4HELF");
    await screen.findByRole("button", { name: "From" });
    scan("NOT-OURS");
    await screen.findByText("That label isn't in the inventory (NOT-OURS).");
    screen.getByRole("button", { name: "From" });
  });

  test("a scanner gun's place label asks too", async () => {
    open();
    await ready();
    find("s4helf");
    fireEvent.keyDown(screen.getByLabelText(/Find an item/), { key: "Enter" });
    fireEvent.click(await screen.findByRole("button", { name: "To" }));
    expect(selected("To")).toBe("11");
  });

  test("an unknown label says so, and Done closes the camera", async () => {
    await opened();
    scan("OLD-FORM");
    await screen.findByText("That label isn't in the inventory (OLD-FORM).");
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    screen.getByRole("button", { name: "Scan with the camera" });
  });
});

describe("a newer build", () => {
  const MINE = "2026-11-12T10:00:00.000Z";
  const NEWER = JSON.stringify({ build: "2026-11-12T11:00:00.000Z" });

  /** The page, of the build MINE; `reload` is what loading it again does. */
  function openMine(api = server().api) {
    const outbox = new Outbox(api, localStorage, new Builds(MINE));
    const reload = vi.fn();
    render(<ScanPage api={api} outbox={outbox} reload={reload} />);
    return { outbox, reload };
  }

  /** Another tab, of a newer build, starting. */
  function newerTab() {
    localStorage.setItem(BUILD_KEY, NEWER);
    window.dispatchEvent(
      new StorageEvent("storage", { key: BUILD_KEY, newValue: NEWER }),
    );
  }

  const reloadButton = () =>
    screen.queryByRole("button", { name: "Refresh this page" });

  test("with nothing waiting and the cart empty, it offers the reload", async () => {
    const { reload } = openMine();
    await ready();
    expect(screen.queryByRole("alert")).toBeNull();
    newerTab();
    await screen.findByText("The scanner has been updated, refresh this page.");
    fireEvent.click(reloadButton() as HTMLElement);
    expect(reload).toHaveBeenCalledOnce();
  });

  test("with the cart filled, it asks for it to be saved first", async () => {
    openMine();
    await ready();
    fillMove();
    newerTab();
    await screen.findByText(
      "The scanner has been updated, refresh this page. Save the cart, or empty it, first.",
    );
    expect(reloadButton()).toBeNull();
    save();
    await screen.findByText("The scanner has been updated, refresh this page.");
    expect(reloadButton()).not.toBeNull();
  });

  test("with the cart filled, then emptied in another tab, it offers the reload", async () => {
    openMine();
    await ready();
    fillMove();
    newerTab();
    await screen.findByText(
      "The scanner has been updated, refresh this page. Save the cart, or empty it, first.",
    );
    storeCart(freshCart());
    window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
    await screen.findByText("The scanner has been updated, refresh this page.");
    expect(reloadButton()).not.toBeNull();
  });

  test("with no catalogue, a cart filled in another tab is still to be saved first", async () => {
    const { api } = answering(() => json({}, 500));
    openMine(api);
    await screen.findByText(/Couldn't load the catalogue/);
    storeCart(addOne(freshCart(), 100));
    window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
    newerTab();
    await screen.findByText(
      "The scanner has been updated, refresh this page. Save the cart, or empty it, first.",
    );
    expect(reloadButton()).toBeNull();
  });

  test("offline, with saves waiting, it asks to stay, and offers no reload", async () => {
    const { api } = server(() => Promise.reject(new TypeError("offline")));
    const { outbox } = openMine(api);
    await ready();
    fillMove();
    save();
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
    );
    newerTab();
    await screen.findByText(
      "The scanner has been updated. 1 save waiting to send: stay on this page until they have gone.",
    );
    expect(reloadButton()).toBeNull();
  });

  test("a save from a build too old is kept, not refused, and the reload offered", async () => {
    const { api } = server(() => json(TOO_OLD, 409));
    const { outbox, reload } = openMine(api);
    await ready();
    fillMove();
    save();
    await screen.findByText(
      "The scanner has been updated, refresh this page. The 1 save waiting here will then be listed, to put back in the cart and save again.",
    );
    expect(outbox.entries.value.map((each) => each.state)).toEqual(["waiting"]);
    expect(screen.queryByRole("button", { name: "Dismiss" })).toBeNull();
    fireEvent.click(reloadButton() as HTMLElement);
    expect(reload).toHaveBeenCalledOnce();
  });

  test("too old with saves waiting, it counts them", async () => {
    const { api } = server(() => json(TOO_OLD, 409));
    openMine(api);
    await ready();
    fillMove();
    save();
    await screen.findByText(/The 1 save waiting here/);
    fillMove();
    save();
    await screen.findByText(
      "The scanner has been updated, refresh this page. The 2 saves waiting here will then be listed, to put back in the cart and save again.",
    );
  });

  test("too old with nothing waiting, it offers the reload alone", async () => {
    const { outbox } = openMine();
    await ready();
    outbox.builds.learn("too_old");
    await screen.findByText("The scanner has been updated, refresh this page.");
    expect(reloadButton()).not.toBeNull();
  });

  test("too old with the cart filled, it offers the reload, and says the cart is kept", async () => {
    const { outbox } = openMine();
    await ready();
    fillMove();
    outbox.builds.learn("too_old");
    await screen.findByText(
      "The scanner has been updated, refresh this page. The cart is kept for after the refresh.",
    );
    expect(reloadButton()).not.toBeNull();
  });
});

describe("what an older build left", () => {
  const KEY = "0b7c6a51-9a3d-4d39-8f3e-1d1f2c4b5a61";
  const AT = 1_700_000_000_000;
  const at = new Date(AT).toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });

  /** A move of two boxes of radios and some cable, waiting under v3. */
  function leaveSave(fields = {}) {
    localStorage.setItem(
      "inventory.outbox.v3",
      JSON.stringify([
        {
          body: {
            key: KEY,
            kind: "move",
            volunteer_id: 1,
            from_location_id: 10,
            to_location_id: 12,
            lines: [
              { item_id: 100, quantity: "8", package_id: 1000 },
              { item_id: 101, quantity: "3", open_package_key: KEY },
            ],
          },
          what: "Move: 2 items from Hub to Van",
          queuedAt: AT,
          state: "waiting",
          ...fields,
        },
      ]),
    );
  }

  /** A cart filled for an install, under v1. */
  function leaveCart() {
    localStorage.setItem(
      "inventory.cart.v1",
      JSON.stringify({
        ...freshCart(),
        kind: "install",
        volunteerId: 2,
        fromId: 12,
        installNumber: "456",
        lines: [{ itemId: 101, packages: {}, loose: "12.5" }],
        usedAt: AT,
      }),
    );
  }

  const listing = () =>
    screen
      .getByRole("heading", { name: "Left by an older version of this page" })
      .closest("section") as HTMLElement;

  test("each is listed in words, and counted once as found", async () => {
    const record = vi.spyOn(telemetry, "record");
    leaveSave();
    leaveCart();
    open();
    await ready();
    const [save, cart] = listing().querySelector("ul")?.children ?? [];
    expect(save?.textContent).toContain("Moving stock from Hub to Van, by Ada");
    within(save as HTMLElement).getByText(
      `Saved on this phone ${at}, not sent.`,
    );
    within(save as HTMLElement).getByText("LiteBeam 5AC: 2 × Box");
    within(save as HTMLElement).getByText(
      "Cat6 cable: 3 m was taken from an open package. Take it again.",
    );
    expect(cart?.textContent).toContain(
      "A cart: taking it to an install from Van for install 456, by Grace",
    );
    within(cart as HTMLElement).getByText(`Last changed ${at}, not saved.`);
    within(cart as HTMLElement).getByText("Cat6 cable: 12.5 m");
    expect(record.mock.calls.map(([event]) => event)).toEqual([
      { kind: "older_storage", held: "saves", action: "found" },
      { kind: "older_storage", held: "cart", action: "found" },
    ]);
    record.mockRestore();
  });

  test("each kind, and what the catalogue no longer has, are told plainly", async () => {
    const save = (key: string, body: object, fields = {}) => ({
      body: { key, volunteer_id: 99, lines: [], ...body },
      queuedAt: null,
      state: "waiting",
      ...fields,
    });
    localStorage.setItem(
      "inventory.outbox.v3",
      JSON.stringify([
        save("a", { kind: "report", from_location_id: 10, used_up: [KEY] }),
        save("b", {
          kind: "open",
          from_location_id: 10,
          to_location_id: 10,
          lines: [{ item_id: 101, quantity: "305", package_id: 1001 }],
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
        save(
          "d",
          { kind: "install", install_number: 77 },
          { state: "refused" },
        ),
      ]),
    );
    open();
    await ready();
    const [report, opening, receipt, install] =
      listing().querySelector("ul")?.children ?? [];
    expect(report?.textContent).toContain(
      "Saying what is on open packages at HubSaved on this phone, not sent.",
    );
    // Nothing in it can go in the cart: discarding is all there is.
    expect(
      within(report as HTMLElement).queryByRole("button", {
        name: "Put it in the cart",
      }),
    ).toBeNull();
    within(opening as HTMLElement).getByText("Cat6 cable: 1 × Spool");
    expect(receipt?.textContent).toContain(
      "Receiving stock to a place no longer in the catalogue",
    );
    within(receipt as HTMLElement).getByText("LiteBeam 5AC: 1 × Box, 3 loose");
    within(receipt as HTMLElement).getByText(
      "An item no longer in the catalogue",
    );
    expect(install?.textContent).toContain(
      "Taking it to an install for install 77Saved on this phone; the inventory did not take it.",
    );
  });

  test("a save of no kind this build knows is something saved", async () => {
    localStorage.setItem(
      "inventory.outbox.v3",
      JSON.stringify([
        {
          body: {
            key: KEY,
            kind: "teleport",
            lines: [{ item_id: 102, quantity: "1" }],
          },
          state: "waiting",
        },
      ]),
    );
    open();
    await ready();
    within(listing()).getByText("Something");
  });

  test("a browser that will not keep the cart keeps the older one", async () => {
    leaveSave();
    open();
    await ready();
    const setItem = vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    fireEvent.click(
      within(listing()).getByRole("button", { name: "Put it in the cart" }),
    );
    setItem.mockRestore();
    within(listing()).getByText(
      "This browser isn't keeping the cart, so it was not put in. Free some space or leave private browsing.",
    );
    // Not filled either: saved from here, then put back, it would go twice.
    screen.getByText("Nothing in the cart yet.");
    expect(selected("From")).not.toBe("10");
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("a browser that will not forget a discarded one says so", async () => {
    leaveSave();
    const [first] = JSON.parse(
      localStorage.getItem("inventory.outbox.v3") ?? "",
    );
    localStorage.setItem(
      "inventory.outbox.v3",
      JSON.stringify([
        first,
        { ...first, body: { ...first.body, key: "other" } },
      ]),
    );
    open();
    await ready();
    const setItem = vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("full", "QuotaExceededError");
    });
    const [save] = within(listing()).getAllByRole("listitem");
    fireEvent.click(
      within(save as HTMLElement).getByRole("button", { name: "Discard" }),
    );
    fireEvent.click(
      within(save as HTMLElement).getByRole("button", { name: "Discard it" }),
    );
    setItem.mockRestore();
    within(listing()).getByText(
      "This browser would not forget it. Free some space or leave private browsing, then discard it again.",
    );
    expect(
      JSON.parse(localStorage.getItem("inventory.outbox.v3") ?? ""),
    ).toHaveLength(2);
  });

  test("a browser that will not forget the older one says so", async () => {
    leaveSave();
    const [first] = JSON.parse(
      localStorage.getItem("inventory.outbox.v3") ?? "",
    );
    localStorage.setItem(
      "inventory.outbox.v3",
      JSON.stringify([
        first,
        { ...first, body: { ...first.body, key: "other" } },
      ]),
    );
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
        name: "Put it in the cart",
      })[0] as HTMLElement,
    );
    setItem.mockRestore();
    screen.getByText(
      "This browser would not forget the older one, so it is still listed: discard it once this cart is saved.",
    );
    expect(selected("From")).toBe("10");
  });

  test("an older cart discarded goes", async () => {
    const record = vi.spyOn(telemetry, "record");
    leaveCart();
    open();
    await ready();
    fireEvent.click(within(listing()).getByRole("button", { name: "Discard" }));
    fireEvent.click(
      within(listing()).getByRole("button", { name: "Discard it" }),
    );
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
    fireEvent.click(within(listing()).getByRole("button", { name: "Discard" }));
    // Sent by an older tab, the storage event not yet heard.
    localStorage.removeItem("inventory.outbox.v3");
    fireEvent.click(
      within(listing()).getByRole("button", { name: "Discard it" }),
    );
    expect(
      screen.queryByText("Left by an older version of this page"),
    ).toBeNull();
  });

  test("a cart's line with nothing readable in it is not told", async () => {
    localStorage.setItem(
      "inventory.cart.v1",
      JSON.stringify({
        ...freshCart(),
        lines: [
          { itemId: 100, packages: { 1000: "some" }, loose: "lots" },
          { itemId: 101, packages: {}, loose: "2" },
        ],
      }),
    );
    open();
    await ready();
    expect(within(listing()).queryByText(/LiteBeam/)).toBeNull();
    within(listing()).getByText("Cat6 cable: 2 m");
  });

  test("a refused one says why", async () => {
    leaveSave({ state: "refused", note: "Not saved. Line 1: no." });
    open();
    await ready();
    within(listing()).getByText(
      `Saved on this phone ${at}; the inventory did not take it. Not saved. Line 1: no.`,
    );
  });

  test("put in the cart, it fills the cart, goes from the list, and saves as any cart", async () => {
    const record = vi.spyOn(telemetry, "record");
    leaveSave();
    const { api, sent } = server();
    const outbox = open(api);
    await ready();
    fireEvent.click(
      within(listing()).getByRole("button", { name: "Put it in the cart" }),
    );
    expect(
      screen.queryByText("Left by an older version of this page"),
    ).toBeNull();
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
    screen.getByText(
      "Put back in the cart. Check it, fill in what it needs, then Save.",
    );
    screen.getByText(
      "Cat6 cable: 3 m was taken from an open package. Take it again.",
    );
    expect(selected("Your name")).toBe("1");
    expect(selected("From")).toBe("10");
    expect(selected("To")).toBe("12");
    expect(
      (screen.getByLabelText("Box (4 each)") as HTMLInputElement).value,
    ).toBe("2");
    expect(record).toHaveBeenCalledWith({
      kind: "older_storage",
      held: "saves",
      action: "put_in_cart",
    });
    record.mockRestore();
    // The cable taken from an open package is left out: removed, it saves.
    const cable = screen.getByText("Cat6 cable").closest("li") as HTMLElement;
    fireEvent.click(within(cable).getByRole("button", { name: "Remove" }));
    save();
    await screen.findByText(/^Saved: /);
    await vi.waitFor(() => expect(saves(sent)).toHaveLength(1));
    expect(outbox.entries.value[0]?.body).toMatchObject({
      kind: "move",
      from_location_id: 10,
      to_location_id: 12,
      lines: [{ item_id: 100, package_id: 1000, quantity: "8" }],
    });
    // A fresh key: the old one is never sent.
    expect(outbox.entries.value[0]?.body.key).not.toBe(KEY);
  });

  test("an older cart put in the cart fills it", async () => {
    leaveCart();
    open();
    await ready();
    fireEvent.click(
      within(listing()).getByRole("button", { name: "Put it in the cart" }),
    );
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
    expect(selected("Your name")).toBe("2");
    expect(
      (
        screen.getByLabelText(
          "MeshDB install number (if you know it)",
        ) as HTMLInputElement
      ).value,
    ).toBe("456");
  });

  test("with the cart filled, it asks for it to be saved first, and keeps the older one", async () => {
    leaveSave();
    open();
    await ready();
    fillMove();
    fireEvent.click(
      within(listing()).getByRole("button", { name: "Put it in the cart" }),
    );
    within(listing()).getByText("Save the cart, or empty it, first.");
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
    fireEvent.click(within(listing()).getByRole("button", { name: "Discard" }));
    within(listing()).getByText(
      "Discard it? It was never saved in the inventory, and can't be got back.",
    );
    fireEvent.click(within(listing()).getByRole("button", { name: "Keep it" }));
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
    fireEvent.click(within(listing()).getByRole("button", { name: "Discard" }));
    fireEvent.click(
      within(listing()).getByRole("button", { name: "Discard it" }),
    );
    expect(localStorage.getItem("inventory.outbox.v3")).toBeNull();
    expect(
      screen.queryByText("Left by an older version of this page"),
    ).toBeNull();
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
      name: "Put it in the cart",
    });
    // Sent, but the storage event not yet heard.
    const sent = JSON.parse(localStorage.getItem("inventory.outbox.v3") ?? "");
    sent[0].state = "saved";
    localStorage.setItem("inventory.outbox.v3", JSON.stringify(sent));
    fireEvent.click(button);
    expect(
      screen.queryByText("Left by an older version of this page"),
    ).toBeNull();
    expect(screen.getByText("Nothing in the cart yet.")).not.toBeNull();
  });

  test("nothing is removed without the volunteer acting, but empty keys", async () => {
    leaveSave();
    const left = localStorage.getItem("inventory.outbox.v3");
    localStorage.setItem("inventory.cart.v1", JSON.stringify(freshCart()));
    open();
    await ready();
    expect(localStorage.getItem("inventory.cart.v1")).toBeNull();
    fillMove();
    save();
    await screen.findByText(/^Saved: /);
    expect(localStorage.getItem("inventory.outbox.v3")).toBe(left);
  });

  test("put in the cart after a save, the cart no longer says it was saved", async () => {
    leaveSave();
    open();
    await ready();
    fillMove();
    save();
    await screen.findByText(/^Saved: /);
    fireEvent.click(
      within(listing()).getByRole("button", { name: "Put it in the cart" }),
    );
    screen.getByText(
      "Put back in the cart. Check it, fill in what it needs, then Save.",
    );
    expect(screen.queryByText(/^Saved: /)).toBeNull();
  });
});
