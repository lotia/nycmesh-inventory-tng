/**
 * One canvas, one bounded frame, per open camera.
 *
 * `barcode-detector` accepts anything a canvas can draw, but what it does with
 * each kind is not the same. Handed an `HTMLVideoElement` it builds a *fresh*
 * `OffscreenCanvas` and 2D context on every call, draws the frame at whatever
 * resolution the camera answered with, and reads it back; handed an
 * `ImageData` it uses it as it stands and allocates nothing of its own.
 * (`barcode-detector` 3.2.2, `dist/es/zxing-exported.js`: `detect` calls one
 * converter, whose `ImageData` branch returns its argument and whose element
 * branch calls `createCanvas` -> `drawImage` -> `getImageData`.)
 *
 * So this module does the drawing, once, into a canvas it keeps -- and does it
 * scaled, so the pixel count is ours rather than the camera's. What remains
 * per frame is the `ImageData` `getImageData` has to allocate and the
 * grayscale copy `zxing-wasm` makes of it on the way into the WASM heap; both
 * are now bounded by `WORKING_EDGE` instead of growing with the lens.
 */

/**
 * The longest edge of a frame handed to the decoder, in pixels.
 *
 * A QR needs roughly three pixels per module to decode, and the codes on these
 * labels are 21 to 33 modules across (decision 0011 section 3), so a label
 * filling a third of the frame is still comfortably readable at 640 -- while a
 * phone that answers `getUserMedia` with 1920x1080 would otherwise cost nine
 * times the work per frame for no more decodes.
 */
export const WORKING_EDGE = 640;

export interface Size {
  width: number;
  height: number;
}

/**
 * The size to decode at: the frame, shrunk until its longest edge fits.
 *
 * Never enlarged. Upscaling invents no detail for the decoder and would make a
 * cheap camera cost more than an expensive one.
 */
export function workingSize(width: number, height: number): Size {
  const longest = Math.max(width, height);
  if (longest <= WORKING_EDGE) {
    return { width, height };
  }
  const scale = WORKING_EDGE / longest;
  // Floored at one pixel rather than rounded to none: a frame wider than 640
  // times its own height would otherwise round its short edge to zero, and the
  // grabber below reads that as "no frame yet" and never draws again.
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

/**
 * A function that turns the current video frame into pixels, reusing one canvas.
 *
 * The canvas is created on first use and kept in the closure, so the caller
 * holds it by holding the function and lets go of it by letting go -- which is
 * what the effect that opens the camera does, one grabber per stream.
 *
 * Answers `null` rather than throwing when there is nothing to draw yet or no
 * 2D context to draw with; the caller passes the video element on instead and
 * lets the decoder decide, which is what happened before this file existed.
 */
export function frameGrabber(): (video: HTMLVideoElement) => ImageData | null {
  let canvas: HTMLCanvasElement | null = null;
  let context: CanvasRenderingContext2D | null = null;
  return (video: HTMLVideoElement): ImageData | null => {
    const { width, height } = workingSize(video.videoWidth, video.videoHeight);
    // A stream that has not produced a frame yet reports 0x0, and a canvas
    // cannot be that size.
    if (width === 0 || height === 0) {
      return null;
    }
    canvas ??= document.createElement("canvas");
    // `willReadFrequently` asks for a CPU-backed canvas. Every frame drawn
    // here is read straight back, so the GPU round trip the default is
    // optimised for is exactly what this must not pay. Kept beside the canvas
    // rather than asked for per frame: a second `getContext` on the same
    // canvas answers the same object and ignores these options anyway, and
    // this runs five times a second for as long as the camera is open.
    context ??= canvas.getContext("2d", { willReadFrequently: true });
    if (context === null) {
      return null;
    }
    // Assigning either dimension clears the canvas, so it is done only when
    // the camera's shape actually changed -- once per stream, in practice.
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    context.drawImage(video, 0, 0, width, height);
    return context.getImageData(0, 0, width, height);
  };
}
