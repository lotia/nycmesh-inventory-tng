import { fireEvent, render, screen, within } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { openCamera } from "./camera";
import { STORAGE_KEY as CART_KEY, freshCart, storeCart } from "./cart";
import { Outbox } from "./outbox";
import { ScanPage } from "./ScanPage";
import { MOST_SECONDS_TO_SAVE, telemetry } from "./telemetry";
import { fakeCamera } from "./test-camera";
import { CATALOGUE } from "./test-catalogue";
import { answering, json, saved } from "./test-fetch";

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
  render(<ScanPage api={api} outbox={outbox} />);
  return outbox;
}

const IDLE = CATALOGUE.settings.name_check_minutes * 60_000;

const pick = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } });

const selected = (label: string) =>
  (screen.getByLabelText(label) as HTMLSelectElement).value;

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
  test("an open package where it comes from can be cut, or taken whole", async () => {
    const opened = {
      id: 5,
      key: "11111111-1111-4111-8111-111111111111",
      item_id: 101,
      package_id: 1001,
      location_id: 10,
      opened_at: "2026-11-03T10:00:00Z",
      left: "120.000",
    };
    const { api } = answering((request) =>
      new URL(request.url).pathname === "/api/catalogue"
        ? json({ ...CATALOGUE, open_packages: [opened] }, 200)
        : saved(),
    );
    open(api);
    await ready();
    pick("From", "10");
    find("cat6");
    fireEvent.click(screen.getByRole("button", { name: "Cat6 cable" }));
    const label = "From the open Spool: 120 m left, opened 2026-11-03";
    const field = screen.getByLabelText(label) as HTMLInputElement;
    fireEvent.input(field, { target: { value: "20" } });
    expect(field.value).toBe("20");
    fireEvent.click(screen.getByLabelText("All of it, moved still open"));
    expect(field.value).toBe("120");
    expect(field.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("All of it, moved still open"));
    expect(field.value).toBe("");
    fireEvent.input(field, { target: { value: "20" } });
    // All of it typed: asked whether it is used up.
    fireEvent.input(field, { target: { value: "120" } });
    fireEvent.click(
      screen.getByLabelText("That takes all it shows: is it used up?"),
    );
    fireEvent.input(field, { target: { value: "20" } });
    expect(
      (
        screen.getByLabelText(
          "Used up: what is left is no use",
        ) as HTMLInputElement
      ).checked,
    ).toBe(true);
    // Another place: what was taken from this one's packages goes.
    pick("From", "12");
    pick("From", "10");
    expect((screen.getByLabelText(label) as HTMLInputElement).value).toBe("");
  });

  test("opening packages asks where, not from and to; an item adds its package", async () => {
    open();
    await ready();
    fireEvent.click(screen.getByLabelText("Opening packages"));
    expect(screen.getByLabelText("Where")).toBeDefined();
    expect(screen.queryByLabelText("To")).toBeNull();
    // Only whole packages are opened: the radio's one box, not a radio.
    find("beam");
    fireEvent.click(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect(
      (screen.getByLabelText("Box (4 each)") as HTMLInputElement).value,
    ).toBe("1");
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
});

describe("scanning labels with the camera", () => {
  /** Shows the camera these labels: it reads them within a moment. */
  function scan(...codes: string[]) {
    camera.state.inView = codes;
  }

  async function opened() {
    open();
    await ready();
    camera.state.inView = [];
    fireEvent.click(
      screen.getByRole("button", { name: "Scan with the camera" }),
    );
    await screen.findByText("Point the camera at a label.");
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
