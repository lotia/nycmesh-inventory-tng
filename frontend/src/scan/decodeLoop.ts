/**
 * The decode loop: a frame five times a second, and whatever it says.
 *
 * Pulled out of CameraScanner.tsx because every rule in it is ours rather than
 * the camera's -- skip a tick while the last one is still decoding, recheck
 * that the scanner is still open after the await, hand on every code in a
 * frame, treat a frame that will not decode as the ordinary case. None of that
 * needs a camera to be true, and two of those rules were bugs a review caught,
 * so they are worth a test that does not need one either.
 *
 * What is left in the component is wiring: where the pixels come from, what a
 * code is handed to, and when to stop. That is the same move that produced
 * frame.ts, cameras.ts and decoder.ts.
 */

import type { CodeDetector, Decoded } from "./decoder";

/** Fast enough to feel instant, slow enough to leave the phone some battery. */
export const DECODE_INTERVAL_MS = 200;

export interface Decoding {
  /**
   * Read whatever codes are in this frame.
   *
   * The decoder's own signature, from decoder.ts, so that adopting the native
   * `BarcodeDetector` stays the change of one import it says it should be. A
   * frame with nothing in it answers with an empty list; a rejection means the
   * decoder could not read the frame *at all*.
   */
  detect: CodeDetector["detect"];
  /** The current frame, as pixels where they can be had -- see frame.ts. */
  frame: () => CanvasImageSource | ImageData;
  /**
   * Every code in every frame, including the same code five times a second.
   *
   * What counts as a *new* scan is the cart's to decide -- `SCAN_DEBOUNCE_MS`
   * in cart/cartState.ts -- and a second opinion here would be a second thing
   * to get wrong.
   */
  onCode: (code: string) => void;
}

/**
 * Start decoding, and answer with the way to stop.
 *
 * Stopping is one act and this owns it: the interval is cleared *and* a decode
 * already in flight is discarded when it lands. A caller keeping its own
 * "still open" flag beside this one would be the same fact in two places, and
 * the interesting bug -- a code arriving after the volunteer left -- lives
 * exactly in the gap between them.
 */
export function decodeLoop({ detect, frame, onCode }: Decoding): () => void {
  // One tick at a time. A phone that takes longer than the interval to decode
  // a frame would otherwise queue decodes it can never catch up on, each
  // holding a frame's worth of pixels while it waited.
  let busy = false;
  let stopped = false;

  const timer = setInterval(async () => {
    if (busy) {
      return;
    }
    busy = true;
    try {
      // The refusal is caught and nothing else is: most frames are a shelf, or
      // a label mid-focus, so a decoder that will not read one is the ordinary
      // case -- while a frame source or an `onCode` that throws is a bug, and
      // swallowing it here would hide it five times a second.
      const found = await detect(frame()).catch((): Decoded[] => []);
      // The check the cleared interval cannot make -- see above.
      if (stopped) {
        return;
      }
      for (const code of found) {
        onCode(code.rawValue);
      }
    } finally {
      // Whatever happened, and only here: a tick that left this set would end
      // the scanner silently, with a preview still moving.
      busy = false;
    }
  }, DECODE_INTERVAL_MS);
  return () => {
    stopped = true;
    clearInterval(timer);
  };
}
