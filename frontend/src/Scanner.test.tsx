import { fireEvent, render, screen } from "@testing-library/preact";
import { expect, test, vi } from "vitest";
import { DecoderUnavailable, type Session } from "./camera";
import { Scanner } from "./Scanner";
import { fakeCamera as camera } from "./test-camera";

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
  const onCode = vi.fn((code: string) => `Read ${code}.`);
  render(<Scanner open={open} onCode={onCode} onDone={() => {}} />);
  await screen.findByText("Point the camera at a label.");
  state.inView = ["B3ZT9W"];
  await screen.findByText("Read B3ZT9W.");
  // Still in view a moment later: not taken again.
  await new Promise((resolve) => setTimeout(resolve, 450));
  expect(onCode).toHaveBeenCalledTimes(1);
});

test("Done stops the camera", async () => {
  const { state, open } = camera();
  const onDone = vi.fn();
  const { unmount } = render(
    <Scanner open={open} onCode={() => ""} onDone={onDone} />,
  );
  await screen.findByText("Point the camera at a label.");
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  expect(onDone).toHaveBeenCalled();
  unmount();
  await vi.waitFor(() => expect(state.stopped).toBe(true));
});

test("the torch shows where the camera has one, and switches", async () => {
  const lit: boolean[] = [];
  const { open } = camera({
    torch: async (on) => {
      lit.push(on);
    },
  });
  render(<Scanner open={open} onCode={() => ""} onDone={() => {}} />);
  const torch = await screen.findByRole("button", { name: "Torch" });
  fireEvent.click(torch);
  await vi.waitFor(() =>
    expect(torch.getAttribute("aria-pressed")).toBe("true"),
  );
  fireEvent.click(torch);
  await vi.waitFor(() => expect(lit).toEqual([true, false]));
});

test("no torch, no button", async () => {
  render(<Scanner open={camera().open} onCode={() => ""} onDone={() => {}} />);
  await screen.findByText("Point the camera at a label.");
  expect(screen.queryByRole("button", { name: "Torch" })).toBeNull();
});

test("a camera refused, or none, says so and leaves the lists", async () => {
  const refused = async () => {
    throw new DOMException("no", "NotAllowedError");
  };
  render(<Scanner open={refused} onCode={() => ""} onDone={() => {}} />);
  await screen.findByText(/The camera isn't allowed for this page/);
  const missing = async () => {
    throw new DOMException("none", "NotFoundError");
  };
  render(<Scanner open={missing} onCode={() => ""} onDone={() => {}} />);
  await screen.findByText(/No camera could be opened/);
});

test("a camera closed before it opened is stopped when it does", async () => {
  const { state, open } = camera();
  let opened: (session: Session) => void = () => {};
  const late = () =>
    new Promise<Session>((resolve) => {
      opened = resolve;
    });
  const { unmount } = render(
    <Scanner open={late} onCode={() => ""} onDone={() => {}} />,
  );
  unmount();
  // Closed, and only then does the camera open.
  await new Promise((resolve) => setTimeout(resolve, 20));
  opened(await open());
  await vi.waitFor(() => expect(state.stopped).toBe(true));
});

test("a frame that cannot be read yet is skipped", async () => {
  const read = vi
    .fn<() => Promise<string[]>>()
    .mockRejectedValueOnce(new Error("not ready"))
    .mockResolvedValue(["K7QX2M"]);
  render(
    <Scanner
      open={camera({ read }).open}
      onCode={(code) => `Read ${code}.`}
      onDone={() => {}}
    />,
  );
  await screen.findByText("Read K7QX2M.");
});

test("a slow read is waited for, not piled on", async () => {
  const { read, finish } = slowRead();
  render(
    <Scanner
      open={camera({ read }).open}
      onCode={(code) => `Read ${code}.`}
      onDone={() => {}}
    />,
  );
  await screen.findByText("Point the camera at a label.");
  await new Promise((resolve) => setTimeout(resolve, 650));
  expect(read).toHaveBeenCalledTimes(1);
  finish(["B3ZT9W"]);
  await screen.findByText("Read B3ZT9W.");
});

test("a decoder that didn't load says so, not that there is no camera", async () => {
  const offline = async () => {
    throw new DecoderUnavailable("offline");
  };
  render(<Scanner open={offline} onCode={() => ""} onDone={() => {}} />);
  await screen.findByText(/The label reader didn't load/);
});

test("a torch that won't switch says so", async () => {
  const { open } = camera({
    torch: async () => {
      throw new DOMException("busy", "NotReadableError");
    },
  });
  render(<Scanner open={open} onCode={() => ""} onDone={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Torch" }));
  await screen.findByText("The torch wouldn't switch.");
});

test("a label read as the camera closes is not taken", async () => {
  const { read, finish } = slowRead();
  const onCode = vi.fn(() => "");
  const { unmount } = render(
    <Scanner open={camera({ read }).open} onCode={onCode} onDone={() => {}} />,
  );
  await screen.findByText("Point the camera at a label.");
  await new Promise((resolve) => setTimeout(resolve, 250));
  unmount();
  await new Promise((resolve) => setTimeout(resolve, 20));
  finish(["B3ZT9W"]);
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(onCode).not.toHaveBeenCalled();
});
