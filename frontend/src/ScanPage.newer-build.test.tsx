import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import {
  BUILD_KEY,
  Builds,
  MUST_UPGRADE as MUST_UPGRADE_PHRASE,
  UPDATED as UPDATED_PHRASE,
  UPGRADE,
} from "./build";
import { addOne, STORAGE_KEY as CART_KEY, freshCart, storeCart } from "./cart";
import { say } from "./i18n";
import { ADD_TO_CART, CHECKED_OUT } from "./OlderList";
import { CARTS_FROM_DEVICE, DISMISS, HELD, SENDING } from "./OutboxList";
import { Outbox } from "./outbox";
import {
  ADDED_BACK as ADDED_BACK_PHRASE,
  NOTHING_IN_CART,
  ScanPage,
} from "./ScanPage";
import { answering, deferred, json, saved, TOO_OLD } from "./test-fetch";
import {
  keep,
  moveBody,
  olderSave,
  outboxEntry,
  storeOutbox,
} from "./test-saves";
import { fillMove, open, ready, save, server } from "./test-scan-page";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

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
    screen.queryByRole("button", { name: say(UPGRADE) });
  const UPDATED = say(UPDATED_PHRASE);
  const MUST_UPGRADE = say(MUST_UPGRADE_PHRASE);
  /** The banner's words, if it shows. */
  const banner = () =>
    screen
      .queryAllByRole("alert")
      .find((each) => each.textContent?.startsWith(UPDATED))
      ?.querySelector("div")?.textContent ?? null;

  test("with nothing waiting and the cart empty, it offers the reload", async () => {
    const { reload } = openMine();
    await ready();
    expect(screen.queryByRole("alert")).toBeNull();
    newerTab();
    await vi.waitFor(() => expect(banner()).toBe(UPDATED));
    fireEvent.click(reloadButton() as HTMLElement);
    expect(reload).toHaveBeenCalledOnce();
  });

  test("with the cart filled, it says nothing until the cart is saved and sent", async () => {
    openMine();
    await ready();
    fillMove();
    newerTab();
    await vi.waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(reloadButton()).toBeNull();
    save();
    await vi.waitFor(() => expect(banner()).toBe(UPDATED));
    expect(reloadButton()).not.toBeNull();
  });

  test("with the cart filled, then emptied in another tab, it offers the reload", async () => {
    openMine();
    await ready();
    fillMove();
    newerTab();
    expect(banner()).toBeNull();
    storeCart(freshCart());
    window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
    await vi.waitFor(() => expect(banner()).toBe(UPDATED));
    expect(reloadButton()).not.toBeNull();
  });

  test("with no catalogue, a cart filled in another tab still holds it back", async () => {
    const { api } = answering(() => json({}, 500));
    const { outbox } = openMine(api);
    await screen.findByText(/Couldn't load the catalogue/);
    storeCart(addOne(freshCart(), 100));
    window.dispatchEvent(new StorageEvent("storage", { key: CART_KEY }));
    newerTab();
    await vi.waitFor(() => expect(outbox.builds.newer.value).toBe("other_tab"));
    expect(banner()).toBeNull();
    expect(reloadButton()).toBeNull();
  });

  test("offline, with saves waiting, it says nothing until they have gone", async () => {
    let online = false;
    const { api } = server(() =>
      online ? saved() : Promise.reject(new TypeError("offline")),
    );
    const { outbox } = openMine(api);
    await ready();
    fillMove();
    save();
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
    );
    newerTab();
    await vi.waitFor(() => expect(outbox.builds.newer.value).toBe("other_tab"));
    expect(banner()).toBeNull();
    expect(reloadButton()).toBeNull();
    online = true;
    await outbox.send();
    await vi.waitFor(() => expect(banner()).toBe(UPDATED));
  });

  /** The list of carts from this device. */
  const list = () =>
    screen
      .getByRole("heading", { name: say(CARTS_FROM_DEVICE) })
      .closest("section") as HTMLElement;

  /** That the list's `count` saves each say they are held, in the banner's
   * caution style, to stand out without reading as a failure, and not read
   * out: every entry is there as the page loads (Ali, 2026-11-16,
   * inventory-tng-odn, inventory-tng-n7yv). None says it is on its way. */
  function expectAllHeld(count: number) {
    const notes = within(list()).getAllByText(say(HELD));
    expect(notes).toHaveLength(count);
    for (const note of notes) {
      expect(note.classList).toContain("border-caution");
    }
    expect(within(list()).queryByText(say(SENDING))).toBeNull();
    expect(within(list()).queryByText(MUST_UPGRADE)).toBeNull();
    expect(within(list()).queryByRole("alert")).toBeNull();
  }

  test("saves from a build too old are kept, not refused, each held, and the upgrade asked for", async () => {
    const first = deferred();
    let answer: () => Response | Promise<Response> = () => first.promise;
    const { api } = server(() => answer());
    const { outbox, reload } = openMine(api);
    await ready();
    fillMove();
    save();
    fillMove();
    save();
    // The first is out; the second waits its turn, said to be on its way.
    await vi.waitFor(() =>
      expect(within(list()).getAllByText(say(SENDING))).toHaveLength(2),
    );
    // Any send after is answered so too, each with a Response of its own.
    answer = () => json(TOO_OLD, 409);
    first.resolve(json(TOO_OLD, 409));
    await vi.waitFor(() => expect(banner()).toBe(MUST_UPGRADE));
    expect(outbox.entries.value.map((each) => each.state)).toEqual([
      "waiting",
      "waiting",
    ]);
    expectAllHeld(2);
    // One saved after the banner shows is held too, never sent.
    fillMove();
    save();
    await vi.waitFor(() => expectAllHeld(3));
    expect(screen.queryByRole("button", { name: say(DISMISS) })).toBeNull();
    fireEvent.click(reloadButton() as HTMLElement);
    expect(reload).toHaveBeenCalledOnce();
    // Loaded again, as the build now current: each is sent as the build
    // that queued it, which the server still holds back, so each is listed
    // as an older build's, the first put back in the empty cart.
    cleanup();
    const upgraded = new Outbox(
      api,
      localStorage,
      new Builds("2026-11-12T11:00:00.000Z"),
    );
    render(<ScanPage api={api} outbox={upgraded} reload={vi.fn()} />);
    await screen.findByText(say(ADDED_BACK_PHRASE));
    expect(upgraded.builds.newer.value).toBeNull();
    await vi.waitFor(() =>
      expect(
        within(
          screen
            .getByRole("heading", { name: say(CHECKED_OUT) })
            .closest("section") as HTMLElement,
        ).getAllByRole("button", { name: say(ADD_TO_CART) }),
      ).toHaveLength(2),
    );
    expect(screen.queryByText(say(HELD))).toBeNull();
  });

  test("saves held for the upgrade do not ask to register, which would not send them", async () => {
    let answer = () => json({ reason: "unregistered" }, 403);
    const { api } = server(() => answer());
    const { outbox } = openMine(api);
    await ready();
    fillMove();
    save();
    fillMove();
    save();
    await vi.waitFor(() =>
      expect(outbox.entries.value.map((each) => each.waitingFor)).toEqual([
        "registration",
        "registration",
      ]),
    );
    // Registered, the first sent again is held back as too old.
    answer = () => json(TOO_OLD, 409);
    await outbox.send();
    await vi.waitFor(() => expect(banner()).toBe(MUST_UPGRADE));
    expectAllHeld(2);
    expect(within(list()).queryByRole("link")).toBeNull();
  });

  test("a save on its way is said in plain small text", async () => {
    const answer = deferred();
    open(server(() => answer.promise).api);
    await ready();
    fillMove();
    save();
    const list = screen
      .getByRole("heading", { name: say(CARTS_FROM_DEVICE) })
      .closest("section") as HTMLElement;
    const note = within(list).getByText(say(SENDING));
    expect(note.tagName).toBe("SMALL");
    expect(note.className).toBe("");
  });

  test("too old, a newer build's save waiting here is not counted, as that build sends it", async () => {
    // Saved by a build after MINE.
    const newer = "2026-11-12T11:00:00.000Z";
    storeOutbox([outboxEntry({ build: newer })], newer);
    const { api } = server(() => Promise.reject(new TypeError("offline")));
    const { outbox } = openMine(api);
    await ready();
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
    );
    outbox.builds.learn("too_old");
    await vi.waitFor(() => expect(banner()).toBe(UPDATED));
  });

  test("once it is out, what an older build left is not put in the empty cart, which would hold the reload back", async () => {
    keep("inventory.outbox.v3", [
      olderSave(
        moveBody({
          lines: [{ item_id: 100, quantity: "4", package_id: 1000 }],
        }),
        { queuedAt: 1_700_000_000_000 },
      ),
    ]);
    openMine();
    newerTab();
    await ready();
    // Once the page has done what it does as the catalogue arrives.
    await act(() => new Promise((done) => setTimeout(done, 20)));
    expect(banner()).toBe(UPDATED);
    screen.getByText(say(NOTHING_IN_CART));
    screen.getByRole("button", { name: say(ADD_TO_CART) });
    expect(localStorage.getItem("inventory.outbox.v3")).not.toBeNull();
  });

  test("too old with nothing waiting, it offers the reload alone", async () => {
    const { outbox } = openMine();
    await ready();
    outbox.builds.learn("too_old");
    await vi.waitFor(() => expect(banner()).toBe(UPDATED));
    expect(reloadButton()).not.toBeNull();
  });

  test("too old with the cart filled, it asks for the upgrade, as the cart is kept", async () => {
    const { outbox } = openMine();
    await ready();
    fillMove();
    outbox.builds.learn("too_old");
    await vi.waitFor(() => expect(banner()).toBe(MUST_UPGRADE));
    expect(reloadButton()).not.toBeNull();
  });
});
