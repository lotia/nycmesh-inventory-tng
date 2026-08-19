/**
 * What can be asserted about the frame grabber in a browser with no pixels.
 *
 * jsdom has a `<canvas>` element and no 2D context behind it, so the one thing
 * stubbed here is `getContext` -- the single call jsdom cannot answer. The
 * canvas itself is jsdom's own, so its width and height behave as the real
 * ones do, and `document.createElement` is the real one, which is what lets
 * "only one canvas is ever made" be asserted rather than assumed.
 *
 * Nothing here pretends to decode anything: what a QR looks like to zxing is
 * not this file's business, and the header of CameraScanner.tsx says why no
 * test in this directory tries.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { frameGrabber, WORKING_EDGE, workingSize } from "./frame";

/** A video element as this module reads one: two numbers. */
function playing(videoWidth: number, videoHeight: number): HTMLVideoElement {
  return { videoWidth, videoHeight } as HTMLVideoElement;
}

interface Drawn {
  canvases: HTMLCanvasElement[];
  drawImage: ReturnType<typeof vi.fn>;
  getImageData: ReturnType<typeof vi.fn>;
}

/**
 * Give every canvas this test makes a 2D context that records what it is told.
 *
 * `document.createElement` still makes the element; only the context, which
 * jsdom has none of, is answered here.
 */
function recording(): Drawn {
  const drawImage = vi.fn();
  const getImageData = vi.fn(
    (_x: number, _y: number, width: number, height: number) =>
      ({ width, height }) as unknown as ImageData,
  );
  const canvases: HTMLCanvasElement[] = [];
  const create = document.createElement.bind(document);
  vi.spyOn(document, "createElement").mockImplementation((tag: string) => {
    const element = create(tag);
    if (tag === "canvas") {
      const canvas = element as HTMLCanvasElement;
      vi.spyOn(canvas, "getContext").mockReturnValue({
        drawImage,
        getImageData,
      } as unknown as CanvasRenderingContext2D);
      canvases.push(canvas);
    }
    return element;
  });
  return { canvases, drawImage, getImageData };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the size a frame is decoded at", () => {
  it("shrinks a frame bigger than the working size, keeping its shape", () => {
    expect(workingSize(1920, 1080)).toEqual({ width: 640, height: 360 });
    expect(workingSize(1080, 1920)).toEqual({ width: 360, height: 640 });
  });

  it("leaves a frame that already fits alone, rather than inventing detail", () => {
    expect(workingSize(640, 480)).toEqual({ width: 640, height: 480 });
    expect(workingSize(320, 240)).toEqual({ width: 320, height: 240 });
  });

  it("bounds the longest edge whichever way the phone is held", () => {
    for (const [width, height] of [
      [4032, 3024],
      [3024, 4032],
      [1280, 720],
    ]) {
      const bounded = workingSize(width, height);
      expect(Math.max(bounded.width, bounded.height)).toBeLessThanOrEqual(WORKING_EDGE);
    }
  });
});

describe("grabbing a frame", () => {
  it("draws the camera's frame scaled down to the working size", () => {
    const { drawImage, getImageData } = recording();
    const frame = frameGrabber()(playing(1280, 720));

    expect(drawImage).toHaveBeenCalledWith(expect.anything(), 0, 0, 640, 360);
    expect(getImageData).toHaveBeenCalledWith(0, 0, 640, 360);
    expect(frame).toEqual({ width: 640, height: 360 });
  });

  it("keeps one canvas across every frame, rather than one per decode", () => {
    // The whole point of the module: the decoder's own path allocates a fresh
    // canvas and context per call, five times a second for as long as the
    // camera is open.
    const { canvases, drawImage } = recording();
    const grab = frameGrabber();
    const video = playing(1280, 720);

    grab(video);
    grab(video);
    grab(video);

    expect(canvases).toHaveLength(1);
    expect(drawImage).toHaveBeenCalledTimes(3);
    expect(canvases[0].width).toBe(640);
    expect(canvases[0].height).toBe(360);
  });

  it("gives each open camera its own canvas", () => {
    const { canvases } = recording();
    frameGrabber()(playing(640, 480));
    frameGrabber()(playing(640, 480));

    expect(canvases).toHaveLength(2);
  });

  it("answers nothing, and makes no canvas, before the stream has a frame", () => {
    const { canvases } = recording();

    expect(frameGrabber()(playing(0, 0))).toBeNull();
    expect(canvases).toHaveLength(0);
  });

  it("answers nothing where the browser has no 2D context to draw with", () => {
    // jsdom, unstubbed, which is also the shape of a browser that refuses a
    // context. The caller hands the video element to the decoder instead.
    expect(frameGrabber()(playing(640, 480))).toBeNull();
  });
});
