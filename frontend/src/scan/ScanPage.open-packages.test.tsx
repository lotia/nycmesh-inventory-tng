import { fireEvent, screen, within } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { CATALOGUE, openSpool } from "../testing/catalogue";
import { answering, json, saved } from "../testing/fetch";
import { find, open, pick, ready, server, tap } from "../testing/scan-page";
import { FROM, TO, WHERE } from "./CartForm";
import { KIND_WORDS } from "./cart";
import {
  ALL_OF_IT,
  NOTHING_USABLE,
  PRINTED_LENGTH,
  SHORT_END,
  SHOWS_NOTHING_LEFT,
  WRONG_LENGTH,
} from "./OpenPackageCard";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

describe("open packages", () => {
  /** The cable at the hub, with these open packages there. */
  async function cableWith(...opens: ReturnType<typeof openSpool>[]) {
    const { api } = server(saved, {
      ...CATALOGUE,
      open_packages: opens,
    });
    open(api);
    await ready();
    pick(say(FROM), "10");
    find("cat6");
    tap(screen.getByRole("button", { name: "Cat6 cable" }));
  }

  const CUT = "Cut or take from the Spool opened 2026-11-03 (m)";
  const NOTHING_LEFT = say(NOTHING_USABLE);
  const ticked = () =>
    (screen.getByLabelText(NOTHING_LEFT) as HTMLInputElement).checked;
  // The card's note, not the item search's live region.
  const note = () =>
    screen.getByText(
      (_, element) =>
        element?.getAttribute("aria-live") === "polite" &&
        element.closest("fieldset") !== null,
    ).textContent;

  test("an open package is a card: now, after this cut, and a box that ticks itself", async () => {
    await cableWith(openSpool());
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
      "Your cut uses up the Spool, so “Nothing usable left on it” is ticked. Untick it if cable is left. An administrator confirms.",
    );
    // Unticked by the volunteer: no note.
    tap(screen.getByLabelText(NOTHING_LEFT));
    expect(ticked()).toBe(false);
    expect(note()).toBe("");
    // Another cut: the box follows it again.
    fireEvent.input(field, { target: { value: "150" } });
    expect(within(card).getByText("30 m over")).toBeDefined();
    expect(ticked()).toBe(true);
    expect(note()).toBe(
      "Your cut is more than the Spool shows, so “Nothing usable left on it” is ticked. It's recorded as you typed it, and an administrator will check the Spool.",
    );
    // Back to a cut that leaves some: unticked again.
    fireEvent.input(field, { target: { value: "20" } });
    expect(ticked()).toBe(false);
    // Moved whole: no cut, no box.
    tap(screen.getByLabelText(say(ALL_OF_IT)));
    expect(field.value).toBe("120");
    expect(field.disabled).toBe(true);
    expect(screen.queryByLabelText(NOTHING_LEFT)).toBeNull();
    tap(screen.getByLabelText(say(ALL_OF_IT)));
    expect(field.value).toBe("");
    // Another place: what was taken from this one's packages goes.
    fireEvent.input(field, { target: { value: "20" } });
    pick(say(FROM), "12");
    pick(say(FROM), "10");
    expect((screen.getByLabelText(CUT) as HTMLInputElement).value).toBe("");
  });

  test("what is on it now: asked for when a cut passes it, or by a button", async () => {
    await cableWith(openSpool());
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
    tap(screen.getByRole("button", { name: say(WRONG_LENGTH) }));
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
    await cableWith(openSpool({ short_below: "15.000" }));
    fireEvent.input(screen.getByLabelText(CUT), { target: { value: "110" } });
    expect(ticked()).toBe(true);
    expect(note()).toBe(
      "This Spool will be unusable after this cut, so “Nothing usable left on it” is ticked. Untick it if you'll keep the short end.",
    );
  });

  test("several open packages: fewest left first, ties by id, one showing nothing last", async () => {
    // As on the stock page (Ali, 2026-11-18, inventory-tng-qskv).
    await cableWith(
      openSpool({
        id: 6,
        key: "22222222-2222-4222-8222-222222222222",
        left: "-4.000",
      }),
      openSpool({
        id: 9,
        key: "33333333-3333-4333-8333-333333333333",
        left: "40.000",
        opened_at: "2026-11-05T10:00:00Z",
      }),
      openSpool(),
      openSpool({
        id: 7,
        key: "44444444-4444-4444-8444-444444444444",
        left: "40.000",
        opened_at: "2026-11-04T10:00:00Z",
      }),
    );
    const cards = screen.getAllByRole("group", { name: /^Spool open/ });
    expect(
      cards.map((card) => [
        within(card).getAllByText(/ m$/)[0]?.textContent,
        /opened (\d{4}-\d\d-\d\d)/.exec(card.textContent ?? "")?.[1],
      ]),
    ).toEqual([
      ["Now: 40 m", "2026-11-04"],
      ["Now: 40 m", "2026-11-05"],
      ["Now: 120 m", "2026-11-03"],
      ["Now: -4 m", "2026-11-03"],
    ]);
    expect(
      within(cards[3] as HTMLElement).getByText(say(SHOWS_NOTHING_LEFT)),
    ).toBeDefined();
    expect(
      within(cards[0] as HTMLElement).queryByText(say(SHOWS_NOTHING_LEFT)),
    ).toBeNull();
    // Ordered by what is on each now, not after a cut being typed, so no
    // card moves under the volunteer's thumb.
    fireEvent.input(within(cards[2] as HTMLElement).getByLabelText(CUT), {
      target: { value: "100" },
    });
    expect(
      screen
        .getAllByRole("group", { name: /^Spool open/ })
        .map((card) => within(card).getAllByText(/ m$/)[0]?.textContent),
    ).toEqual(["Now: 40 m", "Now: 40 m", "Now: 120 m", "Now: -4 m"]);
    expect(within(cards[2] as HTMLElement).getByText("20 m")).toBeDefined();
  });

  test("several open packages: short ends after the usable ones, before one showing nothing", async () => {
    // Ali, 2026-11-18, inventory-tng-qskv.
    await cableWith(
      openSpool({
        id: 6,
        key: "22222222-2222-4222-8222-222222222222",
        left: "0",
      }),
      openSpool({
        id: 9,
        key: "33333333-3333-4333-8333-333333333333",
        left: "10.000",
        short_below: "15.000",
      }),
      openSpool({ short_below: "15.000" }),
      openSpool({
        id: 7,
        key: "44444444-4444-4444-8444-444444444444",
        left: "40.000",
        short_below: "15.000",
      }),
    );
    expect(
      screen
        .getAllByRole("group", { name: /^Spool open/ })
        .map((card) => within(card).getAllByText(/ m$/)[0]?.textContent),
    ).toEqual(["Now: 40 m", "Now: 120 m", "Now: 10 m", "Now: 0 m"]);
  });

  test("a short end's card is flagged, a usable one and one showing nothing are not", async () => {
    // Ali, 2026-11-18, inventory-tng-j5c2.
    await cableWith(
      openSpool({
        id: 6,
        key: "22222222-2222-4222-8222-222222222222",
        left: "0",
        short_below: "15.000",
      }),
      openSpool({
        id: 9,
        key: "33333333-3333-4333-8333-333333333333",
        left: "10.000",
        short_below: "15.000",
      }),
      openSpool({ short_below: "15.000" }),
    );
    const cards = screen.getAllByRole("group", { name: /^Spool open/ });
    const flagged = cards.map(
      (card) => within(card).queryByText(say(SHORT_END)) !== null,
    );
    expect(flagged).toEqual([false, true, false]);
    // One showing nothing is never a short end too, whatever its package.
    within(cards[2] as HTMLElement).getByText(say(SHOWS_NOTHING_LEFT));
    // In the legend, so read out with the card, as "Shows nothing left" is.
    expect(
      screen.getByRole("group", {
        name: `Spool open opened 2026-11-03 ${say(SHORT_END)}`,
      }),
    ).toBe(cards[1]);
    // A cut typed on the usable one does not flag it: by Now, as the order.
    fireEvent.input(within(cards[0] as HTMLElement).getByLabelText(CUT), {
      target: { value: "110" },
    });
    expect(
      within(cards[0] as HTMLElement).queryByText(say(SHORT_END)),
    ).toBeNull();
  });

  test("an open package is named by its package, though the catalogue has it no more", async () => {
    // A retired spool with none sealed left is not among the cable's
    // packages, but its open one is still taken from (inventory-tng-4w4).
    await cableWith(
      openSpool({ package_id: 1009, package_name: "Old reel", left: "40.000" }),
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
    tap(screen.getByLabelText(say(KIND_WORDS.open)));
    expect(screen.getByLabelText(say(WHERE))).toBeDefined();
    expect(screen.queryByLabelText(say(TO))).toBeNull();
    // Only whole packages are opened: the radio's box sealed here, not a
    // radio.
    pick(say(WHERE), "10");
    find("beam");
    tap(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect(
      (screen.getByLabelText("Box (4 each)") as HTMLInputElement).value,
    ).toBe("1");
  });

  test("opening, a place picked fills what was added before and not typed", async () => {
    open();
    await ready();
    tap(screen.getByLabelText(say(KIND_WORDS.open)));
    const box = () => (screen.getByLabelText(/^Box/) as HTMLInputElement).value;
    // No place yet: the radio's one package (inventory-tng-prp).
    find("beam");
    tap(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect(box()).toBe("1");
    // None sealed at the van; one at the hub.
    pick(say(WHERE), "12");
    expect(box()).toBe("");
    pick(say(WHERE), "10");
    expect(box()).toBe("1");
    // Picked again, then through a place with none: still two.
    find("beam");
    tap(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    pick(say(WHERE), "12");
    pick(say(WHERE), "10");
    expect(box()).toBe("2");
    // Switched to another kind and back, the place kept: as it was.
    tap(screen.getByLabelText(say(KIND_WORDS.move)));
    tap(screen.getByLabelText(say(KIND_WORDS.open)));
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
    tap(screen.getByLabelText(say(KIND_WORDS.open)));
    const field = (name: RegExp) =>
      (screen.getByLabelText(name) as HTMLInputElement).value;
    // At the van only the old crate is sealed (inventory-tng-f9h).
    pick(say(WHERE), "12");
    find("beam");
    tap(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    expect([field(/^Box/), field(/^Old crate/)]).toEqual(["", "1"]);
    // At the hub both are: neither is picked for the volunteer.
    pick(say(WHERE), "10");
    fireEvent.input(screen.getByLabelText(/^Old crate/), {
      target: { value: "" },
    });
    find("beam");
    tap(screen.getByRole("button", { name: "LiteBeam 5AC" }));
    // Nor a loose one: only whole packages are opened.
    expect([field(/^Box/), field(/^Old crate/), field(/^Loose/)]).toEqual([
      "",
      "",
      "",
    ]);
  });
});
