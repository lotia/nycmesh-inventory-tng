import { signal } from "@preact/signals";
import { act, fireEvent, render, screen } from "@testing-library/preact";
import type { ComponentProps } from "preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { telemetry } from "../shared/telemetry";
import { fakeCamera as camera, pass } from "../testing/camera";
import { setVisibility, show, stubVisibility } from "../testing/visibility";
import { DecoderUnavailable, type Session } from "./camera";
import type { LabelKind } from "./camera-report";
import { freshCart } from "./cart";
import {
  type Acted,
  DONE,
  IN_USE,
  INSECURE,
  NO_CAMERA,
  NO_DECODER,
  NOT_ALLOWED,
  NOT_OPENED,
  POINT,
  RELOAD_PAGE,
  type Refusal,
  Scanner,
  STARTING,
  TORCH,
  TORCH_FAILED,
  TYPING,
  UPGRADE_BELOW,
} from "./Scanner";

// The camera's clock, faked: each test lets as many reads' time pass as it
// needs (testing/setup.ts makes it real again). The fake clock also keeps up
// with real time, so a read a test did not let pass still comes, only later.
// Unmounts go in act: outside it, Preact closes the camera a frame later.
// The page shown, unless a test hides it (show).
beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  stubVisibility();
});
afterEach(() => vi.restoreAllMocks());

/** A Scanner on `open`'s camera, reading nothing and done or reloaded
 * to no effect unless a test says. */
function scanner(props: ScannerProps) {
  return render(scannerOf(props));
}

type ScannerProps = Pick<ComponentProps<typeof Scanner>, "open"> &
  Partial<ComponentProps<typeof Scanner>>;

/** The Scanner element `scanner` renders. */
function scannerOf(props: ScannerProps) {
  return (
    <Scanner
      cart={freshCart()}
      onCode={() => said("")}
      onDone={() => {}}
      onPicture={() => {}}
      reads={signal(true)}
      typing={signal(false)}
      tooOld={false}
      reload={() => {}}
      reloadKeepsCart
      {...props}
    />
  );
}

const said = (words: string, label: LabelKind = "item"): Acted => ({
  said: words,
  label,
});

/** The camera sessions reported, as telemetry.record took them. */
function reported() {
  const record = vi.spyOn(telemetry, "record");
  return () =>
    record.mock.calls
      .map(([event]) => event)
      .filter((event) => event.kind === "camera_session");
}

/** A read that answers only when `finish` is called. */
function slowRead() {
  let finish: (codes: string[]) => void = () => {};
  const read = vi.fn(
    () =>
      new Promise<string[]>((resolve) => {
        finish = resolve;
      }),
  );
  return { read, finish: (codes: string[]) => finish(codes) };
}

test("a label in view is taken once, and said what it did", async () => {
  const { state, open } = camera();
  const onCode = vi.fn((code: string) => said(`Read ${code}.`));
  scanner({ open, onCode });
  await screen.findByText(say(POINT));
  state.inView = ["B3ZT9W"];
  await pass();
  await screen.findByText("Read B3ZT9W.");
  // Still in view a moment later: not taken again.
  await pass(2);
  expect(onCode).toHaveBeenCalledTimes(1);
});

test("no label is read while reading is paused, and reading resumes after", async () => {
  const { state, open } = camera();
  const onCode = vi.fn((code: string) => said(`Read ${code}.`));
  const reads = signal(true);
  scanner({ open, onCode, reads });
  await screen.findByText(say(POINT));
  reads.value = false;
  state.inView = ["B3ZT9W"];
  await pass(3);
  expect(onCode).not.toHaveBeenCalled();
  // The status line keeps its last words.
  expect(screen.getByRole("status").textContent).toBe(say(POINT));
  reads.value = true;
  await screen.findByText("Read B3ZT9W.");
  expect(onCode).toHaveBeenCalledTimes(1);
});

test("while typing, the status line says reading waits, then what it said before", async () => {
  const { open } = camera();
  const typing = signal(false);
  scanner({ open, typing });
  const status = await screen.findByText(say(POINT));
  act(() => {
    typing.value = true;
  });
  expect(status.textContent).toBe(say(TYPING));
  act(() => {
    typing.value = false;
  });
  expect(status.textContent).toBe(say(POINT));
});

test("too old, the status line says to upgrade, over typing and what a label did", async () => {
  const { state, open } = camera();
  const onCode = vi.fn((code: string) => said(`Read ${code}.`));
  const typing = signal(true);
  const view = scanner({ open, onCode, typing, tooOld: true });
  const status = await screen.findByText(say(UPGRADE_BELOW));
  act(() => {
    typing.value = false;
  });
  state.inView = ["B3ZT9W"];
  await pass();
  expect(onCode).toHaveBeenCalledTimes(1);
  expect(status.textContent).toBe(say(UPGRADE_BELOW));
  view.rerender(scannerOf({ open, onCode, typing, tooOld: false }));
  expect(status.textContent).toBe("Read B3ZT9W.");
});

test("a tap on the picture takes focus from a text box, and from nothing else", async () => {
  const { open } = camera();
  scanner({ open });
  await screen.findByText(say(POINT));
  const picture = document.querySelector("video") as HTMLVideoElement;
  const box = document.createElement("input");
  const tick = document.createElement("input");
  tick.type = "checkbox";
  document.body.append(box, tick);
  box.focus();
  fireEvent.click(picture);
  expect(document.activeElement).toBe(document.body);
  tick.focus();
  fireEvent.click(picture);
  expect(document.activeElement).toBe(tick);
  box.remove();
  tick.remove();
});

test("a label held in view through a pause is not taken again after it", async () => {
  const { state, open } = camera();
  const onCode = vi.fn((code: string) => said(`Read ${code}.`));
  const reads = signal(true);
  scanner({ open, onCode, reads });
  await screen.findByText(say(POINT));
  state.inView = ["B3ZT9W"];
  await screen.findByText("Read B3ZT9W.");
  reads.value = false;
  // Paused longer than a repeat's quiet time.
  await pass(15);
  reads.value = true;
  await pass(3);
  expect(onCode).toHaveBeenCalledTimes(1);
  // Taken away for the quiet time and shown again, it is a scan.
  state.inView = [];
  await pass(11);
  state.inView = ["B3ZT9W"];
  await pass(2);
  expect(onCode).toHaveBeenCalledTimes(2);
});

test("a label read as reading pauses is not taken", async () => {
  const { read, finish } = slowRead();
  const { open } = camera({ read });
  const onCode = vi.fn((code: string) => said(`Read ${code}.`));
  const reads = signal(true);
  scanner({ open, onCode, reads });
  await screen.findByText(say(POINT));
  await pass();
  expect(read).toHaveBeenCalledTimes(1);
  reads.value = false;
  await act(async () => finish(["B3ZT9W"]));
  await pass(3);
  expect(onCode).not.toHaveBeenCalled();
  // Paused, the camera is not asked again.
  expect(read).toHaveBeenCalledTimes(1);
});

test("a slow read thrown away as reading pauses counts toward the pause", async () => {
  const { read, finish } = slowRead();
  const { open } = camera({ read });
  const onCode = vi.fn((code: string) => said(`Read ${code}.`));
  const reads = signal(true);
  scanner({ open, onCode, reads });
  await screen.findByText(say(POINT));
  await pass();
  await act(async () => finish(["B3ZT9W"]));
  await screen.findByText("Read B3ZT9W.");
  await pass();
  expect(read).toHaveBeenCalledTimes(2);
  // Most of a repeat's quiet time passes before it answers, as the
  // reading pauses; the label is held in view throughout.
  await vi.advanceTimersByTimeAsync(1800);
  reads.value = false;
  await act(async () => finish(["B3ZT9W"]));
  await pass(15);
  reads.value = true;
  await pass();
  expect(read).toHaveBeenCalledTimes(3);
  await act(async () => finish(["B3ZT9W"]));
  await pass();
  expect(onCode).toHaveBeenCalledTimes(1);
});

test("Done stops the camera", async () => {
  const { state, open } = camera();
  const onDone = vi.fn();
  const { unmount } = scanner({ open, onDone });
  // There while it starts, too, with the picture shown; what it says
  // then, and while it points, is a status, not an alert.
  screen.getByRole("button", { name: say(DONE) });
  expect(document.querySelector("video")?.hidden).toBe(false);
  expect(screen.getByRole("status").textContent).toBe(say(STARTING));
  await screen.findByText(say(POINT));
  expect(screen.getByRole("status").textContent).toBe(say(POINT));
  expect(screen.queryByRole("alert")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: say(DONE) }));
  expect(onDone).toHaveBeenCalled();
  act(unmount);
  await vi.waitFor(() => expect(state.stopped).toBe(true));
});

test("the torch shows where the camera has one, and switches", async () => {
  const lit: boolean[] = [];
  const { open } = camera({
    torch: async (on) => {
      lit.push(on);
    },
  });
  scanner({ open });
  const torch = await screen.findByRole("button", { name: say(TORCH) });
  fireEvent.click(torch);
  await vi.waitFor(() =>
    expect(torch.getAttribute("aria-pressed")).toBe("true"),
  );
  fireEvent.click(torch);
  await vi.waitFor(() => expect(lit).toEqual([true, false]));
});

test("no torch, no button", async () => {
  scanner({ open: camera().open });
  await screen.findByText(say(POINT));
  expect(screen.queryByRole("button", { name: say(TORCH) })).toBeNull();
});

test("a camera closed before it opened is stopped when it does", async () => {
  const read = vi.fn(async () => []);
  const { state, open } = camera({ read });
  let opened: (session: Session) => void = () => {};
  const late = () =>
    new Promise<Session>((resolve) => {
      opened = resolve;
    });
  const { unmount } = scanner({ open: late });
  // Closed, and only then does the camera open.
  act(unmount);
  opened(await open());
  await vi.waitFor(() => expect(state.stopped).toBe(true));
  // Nor is it read.
  await pass();
  expect(read).not.toHaveBeenCalled();
});

test("a frame that cannot be read yet is skipped", async () => {
  const read = vi
    .fn<() => Promise<string[]>>()
    .mockRejectedValueOnce(new Error("not ready"))
    .mockResolvedValue(["K7QX2M"]);
  scanner({
    open: camera({ read }).open,
    onCode: (code) => said(`Read ${code}.`),
  });
  await screen.findByText(say(POINT));
  await pass(2);
  await screen.findByText("Read K7QX2M.");
});

test("a slow read is waited for, not piled on", async () => {
  const { read, finish } = slowRead();
  scanner({
    open: camera({ read }).open,
    onCode: (code) => said(`Read ${code}.`),
  });
  await screen.findByText(say(POINT));
  // Three reads' time, the first still reading: no second is begun.
  await pass(3);
  expect(read).toHaveBeenCalledTimes(1);
  finish(["B3ZT9W"]);
  await screen.findByText("Read B3ZT9W.");
});

test("a torch that won't switch says so", async () => {
  const { open } = camera({
    torch: async () => {
      throw new DOMException("busy", "NotReadableError");
    },
  });
  scanner({ open });
  fireEvent.click(await screen.findByRole("button", { name: say(TORCH) }));
  await screen.findByText(say(TORCH_FAILED));
});

test("a label read as the camera closes is not taken", async () => {
  const { read, finish } = slowRead();
  const onCode = vi.fn(() => said(""));
  const { unmount } = scanner({ open: camera({ read }).open, onCode });
  await screen.findByText(say(POINT));
  await pass();
  expect(read).toHaveBeenCalledTimes(1);
  act(unmount);
  finish(["B3ZT9W"]);
  await pass();
  expect(onCode).not.toHaveBeenCalled();
});

test("a session is reported as the camera closes, with its labels by kind", async () => {
  const sessions = reported();
  const { state, open } = camera();
  const onCode = (code: string) =>
    said("", code.startsWith("P") ? "place" : "item");
  const { unmount } = scanner({ open, onCode });
  await screen.findByText(say(POINT));
  state.inView = ["B3ZT9W", "PL4CE1"];
  await pass();
  expect(sessions()).toEqual([]);
  act(unmount);
  await vi.waitFor(() =>
    expect(sessions()).toEqual([
      expect.objectContaining({
        outcome: "opened",
        decoder: "native",
        labels: { item: 1, package: 0, place: 1, unknown: 0 },
      }),
    ]),
  );
});

/** The camera refused with `error`, its scanner's buttons, the sessions
 * reported, and a rerender saying whether a reload keeps the cart. The
 * alert says the refusal's bold heading, then `hint`, the usual one
 * unless a test says. */
async function refusedWith(
  error: unknown,
  refusal: Refusal,
  { reloadKeepsCart = true, hint = refusal.plain } = {},
) {
  const sessions = reported();
  const reload = vi.fn();
  const props = {
    open: async (): Promise<Session> => {
      throw error;
    },
    reload,
    reloadKeepsCart,
  };
  const shown = scanner(props);
  const bold = await screen.findByText(say(refusal.bold));
  expect(bold.tagName).toBe("STRONG");
  // A refusal is a problem: said as an alert, and no status remains.
  expect(screen.getByRole("alert").textContent).toBe(
    `${say(refusal.bold)} ${say(hint)}`,
  );
  expect(screen.queryByRole("status")).toBeNull();
  // No picture to show: the empty video is hidden, not left as a box.
  expect(document.querySelector("video")?.hidden).toBe(true);
  const reloadButton = screen.queryByRole("button", { name: say(RELOAD_PAGE) });
  // Only Reload page, where offered: no Done.
  expect(screen.queryAllByRole("button")).toEqual(
    reloadButton === null ? [] : [reloadButton],
  );
  const rerender = (keeps: boolean) =>
    shown.rerender(scannerOf({ ...props, reloadKeepsCart: keeps }));
  return { sessions, reload, reloadButton, rerender };
}

// One error for each outcome refused, and the hint while a reload or
// restart would empty the cart: to record it first (Ali,
// inventory-tng-0y0u, inventory-tng-xtig). Which error is which outcome
// is camera-report.test.ts's table (outcomeOf); off HTTPS, which has no
// such hint, is tested on its own.
const REFUSED = [
  [
    new DOMException("no", "NotAllowedError"),
    "not_allowed",
    NOT_ALLOWED,
    NOT_ALLOWED.notKept,
  ],
  [
    new DOMException("busy", "NotReadableError"),
    "in_use",
    IN_USE,
    IN_USE.notKept,
  ],
  [
    new DOMException("none", "NotFoundError"),
    "not_found",
    NO_CAMERA,
    NO_CAMERA.notKept,
  ],
  [
    new DecoderUnavailable("offline"),
    "decoder_failed",
    NO_DECODER,
    NO_DECODER.notKept,
  ],
  [new Error("?"), "other", NOT_OPENED, NOT_OPENED.notKept],
] as const;

test.each(REFUSED)(
  "%s: the volunteer is told what the report says",
  async (error, outcome, refusal) => {
    const { sessions, reloadButton } = await refusedWith(error, refusal);
    expect(reloadButton !== null).toBe(refusal.reload);
    expect(sessions()).toEqual([
      expect.objectContaining({ outcome, decoder: "none" }),
    ]);
  },
);

test.each(REFUSED)(
  "%s, a reload emptying the cart: the hint says to record it first",
  async (error, _outcome, refusal, hint) => {
    const { reloadButton } = await refusedWith(error, refusal, {
      reloadKeepsCart: false,
      hint,
    });
    expect(reloadButton !== null).toBe(refusal.reload);
  },
);

test("the hint follows whether a reload keeps the cart", async () => {
  const { rerender } = await refusedWith(
    new DOMException("no", "NotAllowedError"),
    NOT_ALLOWED,
  );
  const alert = screen.getByRole("alert");
  rerender(false);
  expect(alert.textContent).toBe(
    `${say(NOT_ALLOWED.bold)} ${say(NOT_ALLOWED.notKept)}`,
  );
  screen.getByRole("button", { name: say(RELOAD_PAGE) });
  rerender(true);
  expect(alert.textContent).toBe(
    `${say(NOT_ALLOWED.bold)} ${say(NOT_ALLOWED.plain)}`,
  );
});

test("off HTTPS, the volunteer is told what the report says", async () => {
  vi.stubGlobal("isSecureContext", false);
  try {
    const refused = await refusedWith(
      new TypeError("no mediaDevices"),
      INSECURE,
    );
    const { sessions, reloadButton } = refused;
    expect(reloadButton).toBeNull();
    expect(sessions()).toEqual([
      expect.objectContaining({ outcome: "insecure" }),
    ]);
    // The https address needs no reload here: the hint stays.
    refused.rerender(false);
    expect(screen.getByRole("alert").textContent).toBe(
      `${say(INSECURE.bold)} ${say(INSECURE.plain)}`,
    );
  } finally {
    vi.unstubAllGlobals();
  }
});

test("Reload page takes the focus, and reloads the page", async () => {
  const { reload, reloadButton } = await refusedWith(
    new DOMException("busy", "NotReadableError"),
    IN_USE,
  );
  await vi.waitFor(() => expect(document.activeElement).toBe(reloadButton));
  fireEvent.click(reloadButton as HTMLElement);
  expect(reload).toHaveBeenCalledOnce();
});

test("with no Reload page, the refusal's words take the focus", async () => {
  await refusedWith(new DOMException("none", "NotFoundError"), NO_CAMERA);
  await vi.waitFor(() =>
    expect(document.activeElement).toBe(screen.getByRole("alert")),
  );
});

test("Reload page leaves the focus where the volunteer put it", async () => {
  const typing = document.body.appendChild(document.createElement("input"));
  try {
    typing.focus();
    await refusedWith(new DOMException("busy", "NotReadableError"), IN_USE);
    await pass();
    expect(document.activeElement).toBe(typing);
  } finally {
    typing.remove();
  }
});

test("Done while the camera opens is reported as cancelled, not refused", async () => {
  const sessions = reported();
  let abort: (error: unknown) => void = () => {};
  const opening = () =>
    new Promise<Session>((_, reject) => {
      abort = reject;
    });
  const { unmount } = scanner({ open: opening });
  act(unmount);
  // The picture's play interrupted as its video left the page.
  abort(new DOMException("interrupted", "AbortError"));
  await vi.waitFor(() =>
    expect(sessions()).toEqual([
      expect.objectContaining({ outcome: "cancelled", decoder: "none" }),
    ]),
  );
});

test("a camera that opens while the page is hidden is reported then", async () => {
  const sessions = reported();
  const { open } = camera();
  setVisibility("hidden");
  scanner({ open });
  await vi.waitFor(() =>
    expect(sessions()).toEqual([
      expect.objectContaining({ outcome: "opened" }),
    ]),
  );
  // Shown again: what follows is a session of its own.
  show("visible");
  window.dispatchEvent(new Event("pagehide"));
  expect(sessions()).toEqual([
    expect.objectContaining({ outcome: "opened" }),
    expect.objectContaining({ outcome: "resumed" }),
  ]);
});

test("a page hidden or left with the camera open reports it then", async () => {
  const sessions = reported();
  const { open } = camera();
  scanner({ open });
  await screen.findByText(say(POINT));
  show("hidden");
  window.dispatchEvent(new Event("pagehide"));
  expect(sessions()).toEqual([expect.objectContaining({ outcome: "opened" })]);
});
