import { fireEvent, screen } from "@testing-library/preact";
import { describe, expect, test, vi } from "vitest";
import { NOT_ME, THATS_ME, YOUR_NAME } from "./CartForm";
import { freshCart, storeCart } from "./cart";
import { say } from "./i18n";
import { CATALOGUE } from "./test-catalogue";
import { answering, json } from "./test-fetch";
import {
  fillMove,
  open,
  pick,
  ready,
  save,
  saves,
  selected,
  server,
} from "./test-scan-page";

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
