import { act, fireEvent, screen, within } from "@testing-library/preact";
import { describe, expect, type MockInstance, test, vi } from "vitest";
import { messages as pseudo } from "../locales/pseudo.po";
import { say, speak } from "../shared/i18n";
import {
  NO_ANSWER,
  NOT_RECORDED,
  OFFLINE,
  PROBLEMS,
} from "../shared/outbox-notes";
import { CATALOGUE } from "../testing/catalogue";
import { deferred, json, saved } from "../testing/fetch";
import { KEY, moveBody, outboxEntry, storeOutbox } from "../testing/saves";
import {
  expectBold,
  expectRefusal,
  fillMove,
  find,
  open,
  pick,
  ready,
  save,
  server,
} from "../testing/scan-page";
import { ITEMS, MOVE } from "./batch";
import { FROM, SENDING_WHAT, TO, YOUR_NAME } from "./CartForm";
import { KIND_WORDS } from "./cart";
import { DISMISS, SENDING } from "./OutboxList";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

/** The browser says it is `online`, or not, and tells the page so. */
function setOnline(onLine: MockInstance<() => boolean>, online: boolean) {
  onLine.mockReturnValue(online);
  act(() => {
    window.dispatchEvent(new Event(online ? "online" : "offline"));
  });
}

describe("the batches listed in the outbox", () => {
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
        element?.classList.contains("alert") === true &&
        element.textContent ===
          `Not recorded.Line 2: ${say(PROBLEMS.not_whole)}`,
    );
    // Only say(NOT_RECORDED) in bold; in what follows, the line's number
    // the page wrote in italic (Ali, inventory-tng-93z); each problem on a
    // line of its own (Ali, 2026-11-20, inventory-tng-61ys).
    expectRefusal(
      note,
      say(NOT_RECORDED),
      ["2"],
      [`Line 2: ${say(PROBLEMS.not_whole)}`],
    );
    const entry = note.closest("li") as HTMLElement;
    // Numbered as the problems name them.
    expect(
      [...entry.querySelectorAll(":scope > ul li")].map(
        (each) => each.textContent,
      ),
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
    // Each Dismiss is described by its batch's words, read out with it, so
    // two named alike are told apart (inventory-tng-zwnj).
    const dismiss = (what: string) =>
      screen.getByRole("button", { name: say(DISMISS), description: what });
    expect(row(first).contains(dismiss(first))).toBe(true);
    fireEvent.click(dismiss(second));
    expect(screen.queryByText(second)).toBeNull();
    screen.getByText(first);
    expect(outbox.entries.value).toHaveLength(1);
    expect(outbox.entries.value[0]).not.toHaveProperty("dismissed");
  });

  test("a batch being sent keeps Dismiss's room, unseen, unfocusable and not read out, and shows it once saved (Ali, 2026-11-19, inventory-tng-2lad)", async () => {
    const answer = deferred();
    open(server(() => answer.promise).api);
    await ready();
    fillMove();
    save();
    const entry = screen
      .getByText("Move: 1 item from Hub to Van")
      .closest("li") as HTMLElement;
    within(entry).getByText(say(SENDING));
    const room = within(entry).getByText(say(DISMISS));
    expect(room.tagName).toBe("BUTTON");
    expect(room.classList).toContain("invisible");
    expect(room.getAttribute("aria-hidden")).toBe("true");
    expect(room.tabIndex).toBe(-1);
    expect(
      within(entry).queryByRole("button", { name: say(DISMISS) }),
    ).toBeNull();
    answer.resolve(saved());
    await within(entry).findByText("Recorded (transaction 77).");
    const dismiss = within(entry).getByRole("button", { name: say(DISMISS) });
    expect(dismiss.classList).not.toContain("invisible");
    expect(dismiss.tabIndex).toBe(0);
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
    const { api } = server(saved, named);
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
    const { api } = server(saved, named);
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
    expect(note.parentElement?.classList).toContain("alert");
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
