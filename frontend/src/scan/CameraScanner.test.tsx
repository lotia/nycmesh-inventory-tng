/**
 * What can be asserted about the camera in a browser that has none.
 *
 * Up to the point a stream is granted: which cameras are offered, that picking
 * one is remembered and asked for exactly, and that each way of being refused
 * says what to do instead.
 *
 * And past it, which is newer. jsdom will hand back a stream if a test writes
 * one, so the *lifecycle* around a stream is testable here even though nothing
 * flowing through it is: that the preview is given the stream before it is
 * asked to play, that everything taken is let go of however the open ends, and
 * that a stream or a decoder arriving after teardown is released anyway --
 * three of which were bugs a review caught.
 *
 * Nothing here decodes anything, and the decoder is mocked for that reason as
 * much as any: see the header of testFixtures.ts. The loop's own rules are in
 * decodeLoop.test.ts.
 */
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CameraScanner, refusal } from "./CameraScanner";
import { constraints, STORAGE_KEY } from "./cameras";
import { DECODE_INTERVAL_MS } from "./decodeLoop";
import type { CodeDetector, Decoded } from "./decoder";
import {
  clearMediaDevices,
  deferred,
  type FakeStream,
  fakeStream,
  stubMediaDevices,
  videoInput,
} from "./testFixtures";

/**
 * The decoder, held at arm's length.
 *
 * `loadDetector` dynamically imports a WebAssembly module, which Node cannot
 * instantiate from a Vite asset URL -- so without this the component stops at
 * the download and everything after it is unreachable, which is exactly the
 * part the lifecycle rules live in. What is mocked is the module boundary
 * decoder.ts exists to be; that the real one fetches its binary from this
 * origin is decoder.test.ts's assertion.
 */
const decoder = vi.hoisted(() => {
  const detect = vi.fn(async (): Promise<Decoded[]> => []);
  const detector: CodeDetector = { detect };
  const state = {
    detect,
    detector,
    /** What `loadDetector` answers with. Replaced by `holdDownload` below. */
    loading: Promise.resolve(detector),
    /**
     * Keep the megabyte in flight, the way a phone on a basement connection
     * does, and answer with the way to let it land.
     */
    holdDownload(): () => void {
      const download = deferred<typeof detector>();
      state.loading = download.promise;
      return () => {
        download.resolve(detector);
      };
    },
  };
  return state;
});

vi.mock("./decoder", () => ({ loadDetector: () => decoder.loading }));

/** A device with cameras that will not open, which is every device in jsdom. */
function refusing(...devices: MediaDeviceInfo[]): ReturnType<typeof vi.fn> {
  const getUserMedia = vi.fn(async () => {
    throw new DOMException("Denied", "NotAllowedError");
  });
  stubMediaDevices({ getUserMedia, enumerateDevices: async () => devices });
  return getUserMedia;
}

function open(onCode: (code: string) => void = vi.fn()) {
  return render(<CameraScanner onCode={onCode} />);
}

/**
 * A device whose camera opens, with `play` answered.
 *
 * jsdom leaves `HTMLMediaElement.play` unimplemented, so it is spied rather
 * than stubbed globally: the spy records the element it was called on, which
 * is what lets "the stream was assigned before play" be asserted rather than
 * assumed. A test that wants either spy to answer differently re-mocks the one
 * it gets back.
 *
 * @param devices what `enumerateDevices` answers -- asked afresh every call,
 * and told whether a stream has been granted yet, because that is when a
 * browser puts names on them.
 */
function granting(devices: (granted: boolean) => MediaDeviceInfo[]) {
  const { stream, stops } = fakeStream();
  let granted = false;
  const getUserMedia = vi.fn(async () => {
    granted = true;
    return stream;
  });
  stubMediaDevices({ getUserMedia, enumerateDevices: async () => devices(granted) });
  let showing: MediaProvider | null = null;
  const played = vi
    .spyOn(HTMLMediaElement.prototype, "play")
    .mockImplementation(async function playing(this: HTMLVideoElement) {
      showing = this.srcObject;
    });
  return {
    getUserMedia,
    played,
    stream,
    stops,
    showingWhenPlayed: () => showing,
  };
}

/** The camera every test that does not care about the picker opens. */
const ONE_CAMERA = () => [videoInput("back", "Back Camera")];

/**
 * Let everything the open sequence is waiting on settle, and time pass.
 *
 * Inside `act` because the camera list arrives as a state update on a promise
 * nothing rendered is waiting for, and on fake timers for the reason
 * decodeLoop.test.ts gives -- which the caller turns on, because most tests in
 * this file wait on the DOM instead and `waitFor` never settles under them.
 */
async function settle(ms = 0): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

/** Really off, which is every track stopped and not merely the first. */
function cameraIsOff(stops: FakeStream["stops"]): void {
  for (const stop of stops) {
    expect(stop).toHaveBeenCalled();
  }
}

/** Pick a camera from the list, the way somebody taps it. */
function pick(label: string): void {
  fireEvent.mouseDown(screen.getByRole("combobox", { name: /camera/i }));
  fireEvent.click(within(screen.getByRole("listbox")).getByRole("option", { name: label }));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  clearMediaDevices();
  vi.restoreAllMocks();
  vi.useRealTimers();
  // By hand: `restoreAllMocks` restores what `spyOn` replaced and leaves a
  // `vi.fn` alone, so a canned decode set by one test would otherwise be
  // answered to every test below it. `mockReset` puts back the implementation
  // `vi.fn` was created with, which is "no codes in this frame".
  decoder.detect.mockReset();
  // The one detector, not a fresh object wearing its `detect`: a test that
  // held a download open replaced `loading`, and what it has to be put back to
  // is what every other test was given.
  decoder.loading = Promise.resolve(decoder.detector);
});

describe("opening the camera", () => {
  it("says what to do instead where there is no camera API at all", async () => {
    // jsdom, unstubbed, is an insecure origin as far as this code can tell --
    // which is the same shape as the phone on plain HTTP this message is for.
    // Scanner.tsx offers the button anyway so that somebody arrives here; what
    // they get is the sentence and not a preview that will never show a thing.
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent(/served over HTTPS/);
    expect(screen.queryByLabelText("Camera preview")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /camera/i })).not.toBeInTheDocument();
  });

  it("says what to do instead when permission is refused", async () => {
    refusing(videoInput("back", "Back Camera"));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /not letting the page use the camera/,
    );
  });

  it("lets go of a stream that arrived after the scanner was closed", async () => {
    // A stream granted after the cleanup ran has to let go of itself: see
    // `release` in CameraScanner.tsx for what it costs when it does not.
    const { stream, stops } = fakeStream();
    const granted = deferred<MediaStream>();
    stubMediaDevices({
      getUserMedia: vi.fn(() => granted.promise),
      enumerateDevices: async () => [videoInput("back", "Back Camera")],
    });

    const { unmount } = open();
    unmount();
    granted.resolve(stream);

    await waitFor(() => cameraIsOff(stops));
  });

  it("asks for the back camera when nobody has chosen one", async () => {
    // Against `constraints` rather than a literal: what the constraint object
    // contains is settled once, in cameras.test.ts. What is asserted here is
    // that this component asks for the one belonging to the chosen lens.
    const getUserMedia = refusing(videoInput("back", "Back Camera"));
    open();
    await waitFor(() => expect(getUserMedia).toHaveBeenCalled());
    expect(getUserMedia).toHaveBeenCalledWith(constraints(null));
  });
});

describe("choosing the lens", () => {
  it("offers every camera the device reports", async () => {
    refusing(videoInput("back", "Back Camera"), videoInput("wide", "Back Ultra Wide Camera"));
    open();

    fireEvent.mouseDown(await screen.findByRole("combobox", { name: /camera/i }));
    const options = within(screen.getByRole("listbox")).getAllByRole("option");
    expect(options.map((option) => option.textContent)).toEqual([
      "Back Camera",
      "Back Ultra Wide Camera",
    ]);
  });

  it("is not offered where there is only one camera to choose", async () => {
    refusing(videoInput("back", "Back Camera"));
    open();
    await screen.findByRole("alert");
    expect(screen.queryByRole("combobox", { name: /camera/i })).not.toBeInTheDocument();
  });

  it("asks for the chosen one exactly, so nothing can substitute the ultra-wide", async () => {
    const getUserMedia = refusing(
      videoInput("wide", "Back Ultra Wide Camera"),
      videoInput("back", "Back Camera"),
    );
    open();
    await screen.findByRole("combobox", { name: /camera/i });

    pick("Back Camera");
    await waitFor(() => expect(getUserMedia).toHaveBeenLastCalledWith(constraints("back")));
  });

  it("remembers it, so a phone that answers badly costs one tap ever", async () => {
    refusing(videoInput("wide", "Back Ultra Wide Camera"), videoInput("back", "Back Camera"));
    open();
    await screen.findByRole("combobox", { name: /camera/i });

    pick("Back Camera");
    await waitFor(() => expect(window.localStorage.getItem(STORAGE_KEY)).toBe('"back"'));
  });

  it("opens on the remembered one next time", async () => {
    window.localStorage.setItem(STORAGE_KEY, '"back"');
    const getUserMedia = refusing(videoInput("back", "Back Camera"));
    open();
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledWith(constraints("back")));
  });

  it("forgets a remembered camera the device no longer has", async () => {
    // Otherwise the choice is a dead end: the picker is only offered where the
    // device reports more than one camera, and a browser that mints new device
    // ids per session reports one until permission has been granted.
    window.localStorage.setItem(STORAGE_KEY, '"gone"');
    const getUserMedia = vi.fn(async () => {
      throw new DOMException("Not there", "OverconstrainedError");
    });
    stubMediaDevices({ getUserMedia, enumerateDevices: async () => [videoInput("back")] });
    open();

    await waitFor(() => expect(window.localStorage.getItem(STORAGE_KEY)).toBe("null"));
    await waitFor(() => expect(getUserMedia).toHaveBeenLastCalledWith(constraints(null)));
  });

  it("carries on without a picker if the device will not say what it has", async () => {
    stubMediaDevices({
      getUserMedia: vi.fn(async () => {
        throw new DOMException("Denied", "NotAllowedError");
      }),
      enumerateDevices: async () => {
        throw new Error("no");
      },
    });
    open();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: /camera/i })).not.toBeInTheDocument();
  });
});

describe("with a stream granted", () => {
  it("shows the preview the stream before asking it to play", async () => {
    // The other order plays an element with nothing in it: on iOS that is a
    // `play()` that resolves against no source and a preview that stays black
    // until something else re-renders it.
    const camera = granting(ONE_CAMERA);
    open();

    await waitFor(() => expect(camera.played).toHaveBeenCalled());

    expect(camera.showingWhenPlayed()).toBe(camera.stream);
  });

  it("stops every track when the volunteer closes the scanner", async () => {
    const camera = granting(ONE_CAMERA);
    const { unmount } = open();
    await waitFor(() => expect(camera.played).toHaveBeenCalled());

    unmount();

    cameraIsOff(camera.stops);
  });

  it("stops the camera when the open fails after the stream was granted", async () => {
    // The decoder's megabyte lost on a basement connection, or a `play` the
    // browser refuses. `failure` is not a dependency of the effect, so nothing
    // runs its cleanup afterwards: without a release here the tracks stay live
    // for the whole mount, behind a warning saying the camera did not open.
    const camera = granting(ONE_CAMERA);
    camera.played.mockRejectedValue(new DOMException("No", "NotSupportedError"));
    open();

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    cameraIsOff(camera.stops);
  });

  it("names the cameras once permission has let it read them", async () => {
    // Why there are two enumerations is in CameraScanner.tsx. What it buys the
    // volunteer is this: "Camera 2" is something they can pick by trying it,
    // and "Back Ultra Wide Camera" is the one they are looking for.
    const camera = granting((granted) =>
      granted
        ? [videoInput("back", "Back Camera"), videoInput("wide", "Back Ultra Wide Camera")]
        : [videoInput("back"), videoInput("wide")],
    );

    open();
    await waitFor(() => expect(camera.played).toHaveBeenCalled());
    fireEvent.mouseDown(await screen.findByRole("combobox", { name: /camera/i }));

    await waitFor(() => {
      const options = within(screen.getByRole("listbox")).getAllByRole("option");
      expect(options.map((option) => option.textContent)).toEqual([
        "Back Camera",
        "Back Ultra Wide Camera",
      ]);
    });
  });

  it("scans on when the device will not say what cameras it has", async () => {
    // A device that refuses the second enumeration must not cost the volunteer
    // the scanner, and must not be reported as the camera having failed to
    // open at all -- it did open, and it is decoding.
    vi.useFakeTimers();
    granting((granted) => {
      if (granted) {
        throw new DOMException("No", "NotReadableError");
      }
      return [videoInput("back", "Back Camera")];
    });
    decoder.detect.mockResolvedValue([{ rawValue: "7QK3M2XV9A" }]);
    const scanned = vi.fn();

    open(scanned);
    await settle(DECODE_INTERVAL_MS);

    expect(scanned).toHaveBeenCalledWith("7QK3M2XV9A");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("starts no decode loop when the decoder lands after the scanner closed", async () => {
    // The same shape as the late stream above, one await further along: the
    // volunteer taps Stop while the decoder is still downloading. A loop
    // started past that point is an interval nothing holds and nothing can
    // clear, grabbing a camera frame five times a second for the life of the
    // tab -- so the stream is let go of instead and no loop is started at all.
    vi.useFakeTimers();
    const camera = granting(ONE_CAMERA);
    const landed = decoder.holdDownload();
    const { unmount } = open();
    await settle();
    // Pinned, or this passes for the reason the late-stream test above already
    // covers: the open really is suspended on the decoder and nothing else.
    expect(camera.played).toHaveBeenCalled();

    unmount();
    const [stop] = camera.stops;
    const stoppedByCleanup = stop.mock.calls.length;
    landed();
    await settle();

    expect(vi.getTimerCount()).toBe(0);
    // And the late arrival was really handled rather than merely never having
    // happened: it let go of the stream a second time on its way out. Stopping
    // a stopped track is a no-op in a browser.
    expect(stop.mock.calls.length).toBeGreaterThan(stoppedByCleanup);
  });

  it("says nothing about a lens the volunteer has already left", async () => {
    // Switching camera tears the first one down while its permission prompt is
    // still up, and `getUserMedia` then rejects as a matter of course -- with
    // an AbortError, which is not a refusal. Reported, it would put a warning
    // on screen about a camera nobody is looking at any more, next to a
    // preview of the one they picked.
    const left = deferred<MediaStream>();
    const camera = granting(() => [
      videoInput("wide", "Back Ultra Wide Camera"),
      videoInput("back", "Back Camera"),
    ]);
    // The lens the volunteer leaves is the first call; the one they pick is
    // every call after it.
    camera.getUserMedia.mockImplementationOnce(() => left.promise);

    open();
    await screen.findByRole("combobox", { name: /camera/i });
    pick("Back Camera");
    left.reject(new DOMException("Aborted", "AbortError"));

    await waitFor(() => expect(camera.getUserMedia).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("hands a code straight on, without restarting the camera to do it", async () => {
    // The two rules the `latest` ref in CameraScanner.tsx exists for: the
    // newest handler is the one called, and the camera is not reopened to make
    // that true.
    vi.useFakeTimers();
    const camera = granting(ONE_CAMERA);
    decoder.detect.mockResolvedValue([{ rawValue: "7QK3M2XV9A" }]);
    const first = vi.fn();
    const latest = vi.fn();
    const { rerender } = render(<CameraScanner onCode={first} />);
    await settle();

    rerender(<CameraScanner onCode={latest} />);
    await settle(DECODE_INTERVAL_MS);

    expect(latest).toHaveBeenCalledWith("7QK3M2XV9A");
    expect(first).not.toHaveBeenCalled();
    expect(camera.getUserMedia).toHaveBeenCalledTimes(1);
  });
});

describe("why the camera did not open", () => {
  it("names the way past a camera that is gone or a device id that no longer exists", () => {
    expect(refusal(new DOMException("", "NotFoundError"))).toMatch(/Pick another one/);
    expect(refusal(new DOMException("", "OverconstrainedError"))).toMatch(/Pick another one/);
  });

  it("passes anything else on rather than inventing a reason", () => {
    expect(refusal(new Error("camera in use"))).toBe("camera in use");
  });

  it("still says something when what was thrown is not an error at all", () => {
    expect(refusal("nope")).toBe("The camera could not be opened.");
  });
});
