/** A moment's wait after a tap moved what was under the finger, so the
 * second tap of a double tap lands on nothing new. */
import { useSignal } from "@preact/signals";
import { useEffect } from "preact/hooks";

/** How long taps wait, and a list's emptied space stays, after a tap took
 * something away, in milliseconds. */
export const SETTLE_MS = 500;

/** Focus `to`, if there is one, without scrolling it into view: the page
 * moving would put something new under the finger (Ali, 2026-11-27). */
export const focusInPlace = (to: HTMLElement | null) =>
  to?.focus({ preventScroll: true });

/**
 * The space a list took, kept blank for SETTLE_MS once a tap took its last
 * entry away, so what is below it does not move up under the finger (Ali,
 * 2026-11-27, inventory-tng-lm1b). `keep` is given the list's height as it
 * was before the tap; `space` is the blank, or null once it has gone, and
 * calls `onTap` when tapped, as a tap on nothing may move focus.
 */
export function useKeptSpace() {
  const height = useSignal<number | null>(null);
  useEffect(() => {
    if (height.value === null) {
      return;
    }
    const wait = setTimeout(() => {
      height.value = null;
    }, SETTLE_MS);
    return () => clearTimeout(wait);
  }, [height.value]);
  return {
    keep: (tall: number) => {
      height.value = tall;
    },
    space: (onTap: () => void) =>
      height.value === null ? null : (
        <div
          aria-hidden="true"
          style={{ height: `${height.value}px` }}
          onClick={onTap}
        />
      ),
  };
}
