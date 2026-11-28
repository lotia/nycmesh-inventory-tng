import { fireEvent, screen } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { SETTLE_MS } from "../shared/settling";
import { CATALOGUE } from "../testing/catalogue";
import { answering, json } from "../testing/fetch";
import {
  expectSpaceBlank,
  fillMove,
  open,
  pick,
  ready,
  save,
  saves,
  selected,
  server,
} from "../testing/scan-page";
import { CHANGE, NOT_ME, THATS_ME, YOUR_NAME } from "./CartForm";
import { freshCart, storeCart } from "./cart";

// The camera, faked: it sees whatever a test puts in `inView`.
vi.mock("./camera", () => ({ openCamera: vi.fn() }));

const IDLE = CATALOGUE.settings.name_check_minutes * 60_000;

/** A cart that was last used this long ago, by Ada. */
const usedAgo = (ms: number) =>
  storeCart({ ...freshCart(), volunteerId: 1, usedAt: Date.now() - ms });

describe("whose name it is", () => {
  test("Save shows whose name it is saved under, without asking", async () => {
    const { api, sent } = server();
    open(api);
    await ready();
    fillMove();
    pick(say(YOUR_NAME), "2");
    expect(screen.getByText(/Recording as/).textContent).toBe(
      "Recording as Grace. Change",
    );
    expect(screen.queryByText(/Are you/)).toBeNull();
    save();
    await screen.findByText("Recorded (transaction 77).");
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
    fireEvent.click(screen.getByRole("button", { name: say(THATS_ME) }));
    expect(screen.queryByText(/Are you/)).toBeNull();
    expect(selected(say(YOUR_NAME))).toBe("1");
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
    fireEvent.click(await screen.findByRole("button", { name: say(THATS_ME) }));
    expect(screen.getByText(/A new cart is ready/)).toBeDefined();
  });

  test("not me clears the name and goes to the list", async () => {
    usedAgo(IDLE + 1000);
    open();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: say(NOT_ME) }));
    expect(selected(say(YOUR_NAME))).toBe("");
    expect(document.activeElement).toBe(screen.getByLabelText(say(YOUR_NAME)));
    expect(screen.queryByText(/Recording as/)).toBeNull();
  });

  /** The name check's space, kept blank once answered: what is drawn just
   * before the Your name field. */
  const keptSpace = () =>
    nameList().closest("form > *")?.previousElementSibling as HTMLElement;
  const nameList = () => screen.getByLabelText(say(YOUR_NAME));

  /** That answering with `answer`, by a pointer's tap, keeps the name
   * check's 120 px blank until taps are taken again, focuses the name list
   * once, with `focused` (without scrolling, unless said), and an ignored
   * tap there focuses it again (Ali, 2026-11-27, inventory-tng-m6dc). */
  async function expectSpaceKept(
    answer: string,
    focused: unknown[] = [{ preventScroll: true }],
  ) {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const tall = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ height: 120 } as DOMRect);
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    usedAgo(IDLE + 1000);
    open();
    await ready();
    fireEvent.click(screen.getByRole("button", { name: answer }), {
      detail: 1,
    });
    expect(screen.queryByText(/Are you/)).toBeNull();
    expect(document.activeElement).toBe(nameList());
    expect(focus.mock.calls).toEqual([focused]);
    await expectSpaceBlank(keptSpace(), 120, nameList);
    expect(keptSpace()).toBeNull();
    tall.mockRestore();
    focus.mockRestore();
  }

  test("that's me keeps the name check's space blank a moment, and focuses the name list (inventory-tng-m6dc)", async () => {
    await expectSpaceKept(say(THATS_ME));
    expect(selected(say(YOUR_NAME))).toBe("1");
  });

  test("not me keeps the name check's space blank a moment, and focuses the name list (inventory-tng-m6dc)", async () => {
    await expectSpaceKept(say(NOT_ME));
    expect(selected(say(YOUR_NAME))).toBe("");
  });

  test("Change, with the name check shown, keeps its space blank a moment, and brings the name list into view (inventory-tng-m6dc)", async () => {
    // Far below the list, Change scrolls to it, as it did before.
    await expectSpaceKept(say(CHANGE), []);
    expect(selected(say(YOUR_NAME))).toBe("1");
  });

  test("Change, with no name check shown, keeps no space and brings the name list into view", async () => {
    open();
    await ready();
    pick(say(YOUR_NAME), "1");
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    fireEvent.click(screen.getByRole("button", { name: say(CHANGE) }));
    expect(document.activeElement).toBe(nameList());
    expect(focus.mock.calls).toEqual([[]]);
    expect(keptSpace()).toBeNull();
    focus.mockRestore();
    // The page scrolled under the finger: the second tap of a double tap,
    // on a kind's radio there, is ignored, focus back on the name list
    // (inventory-tng-r3jx).
    const kind = screen.getAllByRole("radio")[1] as HTMLInputElement;
    fireEvent.click(kind, { detail: 1 });
    expect(kind.checked).toBe(false);
    expect(document.activeElement).toBe(nameList());
  });

  test("answered by a key, focus goes to the name list and stays where keys then move it (inventory-tng-m6dc)", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    usedAgo(IDLE + 1000);
    open();
    await ready();
    // A key's click has detail 0.
    fireEvent.click(screen.getByRole("button", { name: say(THATS_ME) }), {
      detail: 0,
    });
    expect(document.activeElement).toBe(nameList());
    // Tab on to a kind's radio, within the moment: nothing takes focus back.
    const kind = screen.getAllByRole("radio")[0] as HTMLElement;
    kind.focus();
    await vi.advanceTimersByTimeAsync(SETTLE_MS);
    expect(document.activeElement).toBe(kind);
  });

  test("picking no name in the list clears it", async () => {
    open();
    await ready();
    pick(say(YOUR_NAME), "1");
    expect(screen.getByText(/Recording as/)).toBeDefined();
    pick(say(YOUR_NAME), "");
    expect(selected(say(YOUR_NAME))).toBe("");
    expect(screen.queryByText(/Recording as/)).toBeNull();
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
