/**
 * The loop's rules, with no camera and no decoder in sight.
 *
 * Every assertion here is about a policy this project chose: when a tick is
 * skipped, when a decode is thrown away, what a failure means. Nothing here
 * decodes anything -- see the header of testFixtures.ts for what that means
 * and where the question is answered instead.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DECODE_FAILURE_LIMIT, DECODE_INTERVAL_MS, type Decoding, decodeLoop } from "./decodeLoop";
import type { Decoded } from "./decoder";
import { deferred } from "./testFixtures";

/** A frame, in the only sense this module has of one: whatever `detect` took. */
const FRAME = { width: 640, height: 360 } as ImageData;

/** Answered by a detector that found something, without claiming it read it. */
function found(...codes: string[]): Decoded[] {
  return codes.map((rawValue) => ({ rawValue }));
}

interface Running {
  detect: ReturnType<typeof vi.fn>;
  onCode: ReturnType<typeof vi.fn>;
  /** What the volunteer would be told, if anything. */
  onFailure: ReturnType<typeof vi.fn>;
  /** Close the scanner, which is the one thing the component's cleanup does. */
  stop: () => void;
}

/** Start a loop over a detector under this test's control. */
function running(
  detect: Decoding["detect"],
  frame: Decoding["frame"] = () => FRAME,
  onCode: Decoding["onCode"] = () => {},
): Running {
  const detecting = vi.fn(detect);
  const coded = vi.fn(onCode);
  const onFailure = vi.fn();
  const stop = decodeLoop({ detect: detecting, frame, onCode: coded, onFailure });
  return { detect: detecting, onCode: coded, onFailure, stop };
}

/** Let the interval fire `ticks` times, and every decode it started settle. */
async function tick(ticks = 1): Promise<void> {
  for (let fired = 0; fired < ticks; fired += 1) {
    await vi.advanceTimersByTimeAsync(DECODE_INTERVAL_MS);
  }
}

beforeEach(() => {
  // Every test in this file drives the interval by hand: a real wait of
  // DECODE_INTERVAL_MS per tick is a flake on a loaded CI runner, and seconds
  // across the file.
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("the decode loop", () => {
  it("hands on every code in a frame, and lets the cart decide what is new", async () => {
    // Two codes in one frame is a shelf with two labels in shot, and both are
    // scans. Repeats are not filtered here either -- see `onCode`.
    const scan = running(async () => found("7QK3M2XV9A", "4NP8R7T2WQ"));

    await tick();

    expect(scan.onCode.mock.calls).toEqual([["7QK3M2XV9A"], ["4NP8R7T2WQ"]]);
  });

  it("grabs a fresh frame every tick, rather than decoding one twice", async () => {
    const frame = vi.fn(() => FRAME);
    running(async () => [], frame);

    await tick(3);

    expect(frame).toHaveBeenCalledTimes(3);
  });

  it("skips a tick while the last one is still decoding", async () => {
    // The busy rule in decodeLoop.ts.
    const decoding = deferred<Decoded[]>();
    const scan = running(() => decoding.promise);

    await tick(4);
    expect(scan.detect).toHaveBeenCalledTimes(1);

    decoding.resolve([]);
    await tick();
    expect(scan.detect).toHaveBeenCalledTimes(2);
  });

  it("carries on from a frame that will not decode, rather than ending on it", async () => {
    // Why a refusal is not an error: see `read` in decodeLoop.ts. What is
    // asserted here is the cost of getting it wrong -- an unhandled rejection
    // five times a second, and a `busy` never dropped, which would end the
    // scanner silently with the preview still moving.
    const failed = vi.fn();
    process.on("unhandledRejection", failed);
    const scan = running(async () => {
      throw new Error("no code in this frame");
    });

    try {
      await tick(2);
    } finally {
      // However this test ends: a listener left on `process` outlives the file
      // and watches every rejection in the worker after it.
      process.off("unhandledRejection", failed);
    }

    expect(failed).not.toHaveBeenCalled();
    expect(scan.detect).toHaveBeenCalledTimes(2);
    expect(scan.onCode).not.toHaveBeenCalled();
  });

  it("throws away a decode that lands after the scanner closed", async () => {
    // The stopped-after-await rule in decodeLoop.ts, and the bug it was
    // written for: a volunteer taps Stop while a frame is mid-decode.
    const decoding = deferred<Decoded[]>();
    const scan = running(() => decoding.promise);

    await tick();
    scan.stop();
    decoding.resolve(found("7QK3M2XV9A"));
    await tick();

    expect(scan.onCode).not.toHaveBeenCalled();
  });

  it("stops for good when told to, leaving no timer behind", async () => {
    const scan = running(async () => found("7QK3M2XV9A"));

    scan.stop();
    await tick(3);

    expect(vi.getTimerCount()).toBe(0);
    expect(scan.detect).not.toHaveBeenCalled();
  });

  it("reads a handful of refusals as frames, not as a dead scanner", async () => {
    // Most frames are a shelf or a label mid-focus. A run shorter than the
    // limit is the ordinary case and must stay silent.
    const { onFailure } = running(() => Promise.reject(new Error("no")));

    await tick(DECODE_FAILURE_LIMIT - 1);

    expect(onFailure).not.toHaveBeenCalled();
  });

  it("says the scanner has stopped once refusals stop looking like luck", async () => {
    const { onFailure, detect } = running(() => Promise.reject(new Error("no")));

    await tick(DECODE_FAILURE_LIMIT);

    expect(onFailure).toHaveBeenCalled();
    // Stopped, not merely reported: a lens kept powered for a decoder that
    // cannot read costs battery for nothing.
    const calls = detect.mock.calls.length;
    await tick(5);
    expect(detect.mock.calls).toHaveLength(calls);
  });

  it("forgets the run of refusals as soon as a frame decodes", async () => {
    let refuse = true;
    const { onFailure } = running(() =>
      refuse ? Promise.reject(new Error("no")) : Promise.resolve(found("NM-1")),
    );

    await tick(DECODE_FAILURE_LIMIT - 1);
    refuse = false;
    await tick(1);
    refuse = true;
    await tick(DECODE_FAILURE_LIMIT - 1);

    expect(onFailure).not.toHaveBeenCalled();
  });

  it("says so when the frame source throws, rather than rejecting into nothing", async () => {
    // Deliberately outside the swallow, because it is a bug rather than a bad
    // frame -- and `onFailure` says what used to become of it instead.
    const { onFailure } = running(
      () => Promise.resolve(found("NM-1")),
      () => {
        throw new Error("the canvas is tainted");
      },
    );

    await tick(1);

    expect(onFailure).toHaveBeenCalled();
  });

  it("says so when onCode throws", async () => {
    const { onFailure } = running(
      () => Promise.resolve(found("NM-1")),
      () => FRAME,
      () => {
        throw new Error("the cart blew up");
      },
    );

    await tick(1);

    expect(onFailure).toHaveBeenCalled();
  });
});
