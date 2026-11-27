/**
 * A moment's wait after a tap moved what was under the finger, so the
 * second tap of a double tap lands on nothing new (Ali, 2026-11-27,
 * inventory-tng-r3jx; docs/decisions/0019, Double taps). The scan page's
 * hooks that keep a space or settle a list are in scan/settling-hooks.tsx.
 */

/** How long taps wait, and a space taken away stays, after a tap took
 * something away, in milliseconds. */
export const SETTLE_MS = 500;

/** Focus `to`, if there is one, without scrolling it into view: the page
 * moving would put something new under the finger (Ali, 2026-11-27). */
export const focusInPlace = (to: HTMLElement | null | undefined) =>
  to?.focus({ preventScroll: true });

/** Focus `to`, scrolled into view if it is not: only from a tap that
 * asked to go to it, far from the tap, as Change goes to Your name. */
export const focusInView = (to: HTMLElement | null | undefined) => to?.focus();

/** Each page's wait, while taps are ignored: till when, and what an
 * ignored tap focuses. */
const waits = new WeakMap<
  Document,
  { until: number; refocus: () => HTMLElement | null | undefined }
>();

/** The page's wait, if taps are still ignored. */
function waiting(document: Document) {
  const wait = waits.get(document);
  return wait !== undefined && performance.now() < wait.until
    ? wait
    : undefined;
}

/**
 * Ignore every tap on the page for SETTLE_MS, once a tap took away
 * something with controls below it, which moved up under the finger:
 * the click, and the focus a pressed box or list would take. An ignored
 * pointer tap focuses `refocus()`, without scrolling, as it would have
 * moved focus; a key's press (detail 0) leaves focus where the volunteer
 * moved it. Called again, the wait starts again, with the new `refocus`.
 */
export function ignoreTaps(
  document: Document,
  refocus: () => HTMLElement | null | undefined = () => null,
) {
  if (!waits.has(document)) {
    // Caught on the way down, before any control's own handler.
    document.addEventListener(
      "click",
      (event) => {
        const wait = waiting(document);
        if (wait === undefined) {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        if (event.detail > 0) {
          focusInPlace(wait.refocus());
        }
      },
      true,
    );
    document.addEventListener(
      "mousedown",
      (event) => {
        if (waiting(document) !== undefined) {
          event.preventDefault();
        }
      },
      true,
    );
  }
  waits.set(document, { until: performance.now() + SETTLE_MS, refocus });
}

/** Take taps on the page again at once: for tests, so one test's wait is
 * not left to the next. */
export function stopIgnoringTaps(document: Document) {
  const wait = waits.get(document);
  if (wait !== undefined) {
    wait.until = -Infinity;
  }
}
