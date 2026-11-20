import { act, fireEvent, render, screen } from "@testing-library/preact";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { say } from "../shared/i18n";
import { telemetry } from "../shared/telemetry";
import { fakeCamera as camera, pass } from "../testing/camera";
import { setVisibility, show, stubVisibility } from "../testing/visibility";
import { DecoderUnavailable, type Session } from "./camera";
import type { LabelKind } from "./camera-report";
import {
  type Acted,
  DONE,
  IN_USE,
  INSECURE,
  NO_CAMERA,
  NO_DECODER,
  NOT_ALLOWED,
  POINT,
  RELOAD_PAGE,
  type Refusal,
  Scanner,
  TORCH,
  TORCH_FAILED,
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
  render(
    <Scanner open={open} onCode={onCode} onDone={() => {}} reload={() => {}} />,
  );
  await screen.findByText(say(POINT));
  state.inView = ["B3ZT9W"];
  await pass();
  await screen.findByText("Read B3ZT9W.");
  // Still in view a moment later: not taken again.
  await pass(2);
  expect(onCode).toHaveBeenCalledTimes(1);
});

test("Done stops the camera", async () => {
  const { state, open } = camera();
  const onDone = vi.fn();
  const { unmount } = render(
    <Scanner
      open={open}
      onCode={() => said("")}
      onDone={onDone}
      reload={() => {}}
    />,
  );
  // There while it starts, too.
  screen.getByRole("button", { name: say(DONE) });
  await screen.findByText(say(POINT));
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
  render(
    <Scanner
      open={open}
      onCode={() => said("")}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
  const torch = await screen.findByRole("button", { name: say(TORCH) });
  fireEvent.click(torch);
  await vi.waitFor(() =>
    expect(torch.getAttribute("aria-pressed")).toBe("true"),
  );
  fireEvent.click(torch);
  await vi.waitFor(() => expect(lit).toEqual([true, false]));
});

test("no torch, no button", async () => {
  render(
    <Scanner
      open={camera().open}
      onCode={() => said("")}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
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
  const { unmount } = render(
    <Scanner
      open={late}
      onCode={() => said("")}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
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
  render(
    <Scanner
      open={camera({ read }).open}
      onCode={(code) => said(`Read ${code}.`)}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
  await screen.findByText(say(POINT));
  await pass(2);
  await screen.findByText("Read K7QX2M.");
});

test("a slow read is waited for, not piled on", async () => {
  const { read, finish } = slowRead();
  render(
    <Scanner
      open={camera({ read }).open}
      onCode={(code) => said(`Read ${code}.`)}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
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
  render(
    <Scanner
      open={open}
      onCode={() => said("")}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: say(TORCH) }));
  await screen.findByText(say(TORCH_FAILED));
});

test("a label read as the camera closes is not taken", async () => {
  const { read, finish } = slowRead();
  const onCode = vi.fn(() => said(""));
  const { unmount } = render(
    <Scanner
      open={camera({ read }).open}
      onCode={onCode}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
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
  const { unmount } = render(
    <Scanner open={open} onCode={onCode} onDone={() => {}} reload={() => {}} />,
  );
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

/** The status line's words: the refusal's bold heading, then its hint. */
const refusalWords = (refusal: Refusal) =>
  `${say(refusal.bold)} ${say(refusal.plain)}`;

/** The camera refused with `error`, its scanner's buttons, and the
 * sessions reported. */
async function refusedWith(error: unknown, refusal: Refusal) {
  const sessions = reported();
  const reload = vi.fn();
  const refused = async () => {
    throw error;
  };
  render(
    <Scanner
      open={refused}
      onCode={() => said("")}
      onDone={() => {}}
      reload={reload}
    />,
  );
  const bold = await screen.findByText(say(refusal.bold));
  expect(bold.tagName).toBe("STRONG");
  expect(screen.getByRole("status").textContent).toBe(refusalWords(refusal));
  const reloadButton = screen.queryByRole("button", { name: say(RELOAD_PAGE) });
  // Only Reload page, where offered: no Done.
  expect(screen.queryAllByRole("button")).toEqual(
    reloadButton === null ? [] : [reloadButton],
  );
  return { sessions, reload, reloadButton };
}

// One error for each outcome refused; which error is which outcome is
// camera-report.test.ts's table (outcomeOf).
test.each([
  [new DOMException("no", "NotAllowedError"), "not_allowed", NOT_ALLOWED],
  [new DOMException("busy", "NotReadableError"), "in_use", IN_USE],
  [new DOMException("none", "NotFoundError"), "not_found", NO_CAMERA],
  [new DecoderUnavailable("offline"), "decoder_failed", NO_DECODER],
  [new Error("?"), "other", NO_CAMERA],
] as const)(
  "%s: the volunteer is told what the report says",
  async (error, outcome, refusal) => {
    const { sessions, reloadButton } = await refusedWith(error, refusal);
    expect(reloadButton !== null).toBe(refusal.reload);
    expect(sessions()).toEqual([
      expect.objectContaining({ outcome, decoder: "none" }),
    ]);
  },
);

test("off HTTPS, the volunteer is told what the report says", async () => {
  vi.stubGlobal("isSecureContext", false);
  try {
    const { sessions, reloadButton } = await refusedWith(
      new TypeError("no mediaDevices"),
      INSECURE,
    );
    expect(reloadButton).toBeNull();
    expect(sessions()).toEqual([
      expect.objectContaining({ outcome: "insecure" }),
    ]);
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
  const { unmount } = render(
    <Scanner
      open={opening}
      onCode={() => said("")}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
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
  render(
    <Scanner
      open={open}
      onCode={() => said("")}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
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
  render(
    <Scanner
      open={open}
      onCode={() => said("")}
      onDone={() => {}}
      reload={() => {}}
    />,
  );
  await screen.findByText(say(POINT));
  show("hidden");
  window.dispatchEvent(new Event("pagehide"));
  expect(sessions()).toEqual([expect.objectContaining({ outcome: "opened" })]);
});
