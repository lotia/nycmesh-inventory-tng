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

/**
 * How many refusals in a row mean the decoder has stopped rather than had a
 * bad second.
 *
 * At 5 Hz this is five seconds. A frame that will not decode is the ordinary
 * case -- a shelf, a label mid-focus -- so a handful in a row says nothing;
 * five seconds of them, with the volunteer holding a phone at a label the
 * whole time, is not luck.
 */
export const DECODE_FAILURE_LIMIT = 25;

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
  /**
   * The loop has stopped, and why, in words a volunteer can act on.
   *
   * Two things arrive here, and they are the two the loop could previously
   * only lose. A decoder refusing every frame for DECODE_FAILURE_LIMIT ticks
   * is a scanner that has died with the preview still moving -- which looks
   * exactly like a shelf with no labels on it, and said nothing. And a `frame`
   * or an `onCode` that throws is a bug, deliberately not swallowed, which
   * became an unhandled rejection five times a second that no error boundary
   * saw.
   *
   * The loop has stopped by the time this is called: keeping a lens powered
   * for a decoder that cannot read costs battery for nothing.
   *
   * It says *that* it stopped, not what to put on a screen. Turning a cause
   * into a sentence a volunteer can act on is the component's job, beside
   * `refusal()`, which already does it for a camera that would not open.
   */
  onFailure: () => void;
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
export function decodeLoop({ detect, frame, onCode, onFailure }: Decoding): () => void {
  // One tick at a time. A phone that takes longer than the interval to decode
  // a frame would otherwise queue decodes it can never catch up on, each
  // holding a frame's worth of pixels while it waited.
  let busy = false;
  let stopped = false;
  let refusals = 0;

  const stop = () => {
    stopped = true;
    clearInterval(timer);
  };

  const give_up = () => {
    stop();
    onFailure();
  };

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
      let refused = false;
      const found = await detect(frame()).catch((): Decoded[] => {
        refused = true;
        return [];
      });
      // The check the cleared interval cannot make -- see above.
      if (stopped) {
        return;
      }
      // Counted, not just swallowed. One refusal says nothing; a run of them
      // with nothing decoded in between is a decoder that has died.
      refusals = refused ? refusals + 1 : 0;
      if (refusals >= DECODE_FAILURE_LIMIT) {
        give_up();
        return;
      }
      for (const code of found) {
        onCode(code.rawValue);
      }
    } catch (error: unknown) {
      // `frame` or `onCode` threw, which is a bug rather than a bad frame. It
      // used to leave here as an unhandled rejection, five times a second,
      // seen by nothing. It stops the loop and is said instead.
      // Nothing to report to anyone if the caller has already left.
      if (!stopped) {
        give_up();
        // Worth surfacing to whatever collects errors as well: unlike a
        // decoder refusing frames, this one is a bug.
        console.error("the decode loop stopped", error);
      }
    } finally {
      // Whatever happened, and only here: a tick that left this set would end
      // the scanner silently, with a preview still moving.
      busy = false;
    }
  }, DECODE_INTERVAL_MS);
  return stop;
}
