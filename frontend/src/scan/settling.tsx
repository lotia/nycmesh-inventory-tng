/** A moment's wait after a tap moved what was under the finger, so the
 * second tap of a double tap lands on nothing new. */
import { useSignal } from "@preact/signals";
import type { RefObject } from "preact";
import { useEffect, useRef } from "preact/hooks";

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

/**
 * Taps on a list's entries, ignored a moment once a tap took an entry
 * away, as the second tap of a double tap would land on what moved up in
 * its place; or once a tap changed `on` but left it listed (`stayed`), on
 * `on`'s taps alone (Ali, inventory-tng-0su, inventory-tng-ensw,
 * inventory-tng-34ns). Focus then goes, without scrolling, to the heading
 * of the entry that moved up (`placeId`), or of the list (`heading`), or,
 * none listed, to `after`, and the list's space stays blank a moment
 * (useKeptSpace, `space`); after `stayed`, to `on`'s. A pointer's ignored
 * tap focused the button as it landed, where Enter would later tap it, so
 * focus goes back there (inventory-tng-bz73); a key's press (detail 0)
 * leaves focus where the volunteer moved it.
 *
 * `drawn` is the entries' ids as drawn, in order, and `listed` gives them
 * as they are kept once a tap has run.
 */
export function useSettledList({
  drawn,
  listed,
  placeId,
  after,
}: {
  drawn: string[];
  listed: () => string[];
  placeId: (at: number) => string;
  after: RefObject<HTMLElement | null>;
}) {
  // When an entry last went by a tap, and the place it went from.
  const entryGone = useRef({ when: -Infinity, at: 0 });
  // When a tap last changed each but left it listed.
  const stayedAt = useRef(new Map<string, number>());
  // The heading to focus once the list is drawn again: `on`'s, or, `on`
  // gone from place `at`, the one at that place once `on` is not listed.
  const refocus = useRef<{ on: string; at: number | null } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const list = useRef<HTMLElement>(null);
  const kept = useKeptSpace();
  const placeOf = (on: string) => drawn.indexOf(on);
  const focusPlace = (at: number) =>
    focusInPlace(
      document.getElementById(placeId(at)) ?? heading.current ?? after.current,
    );
  // After every render: the one after a tap draws what it changed.
  useEffect(() => {
    const want = refocus.current;
    if (want === null) {
      return;
    }
    const n = placeOf(want.on);
    if (want.at !== null && n >= 0) {
      // A render from before it went.
      return;
    }
    refocus.current = null;
    focusPlace(want.at ?? n);
  });
  return {
    /** For the list's section, measured as a tap lands. */
    list,
    /** For the list's heading, focusable (tabIndex -1). */
    heading,
    /** What is drawn in the list's place once nothing is listed. */
    space: () => kept.space(() => focusInPlace(after.current)),
    /** A tap changed `on` but left it listed. */
    stayed: (on: string) => {
      stayedAt.current.set(on, performance.now());
      refocus.current = { on, at: null };
    },
    /** `tap`, on one of `on`'s buttons, unless taps are waiting. */
    unlessSettling:
      (on: string, tap: () => void) =>
      (event: MouseEvent): void => {
        const now = performance.now();
        const entryWaits = now - entryGone.current.when < SETTLE_MS;
        if (
          entryWaits ||
          now - (stayedAt.current.get(on) ?? -Infinity) < SETTLE_MS
        ) {
          if (event.detail > 0) {
            focusPlace(entryWaits ? entryGone.current.at : placeOf(on));
          }
          return;
        }
        // Measured before the tap redraws the list, drawn as it was tapped.
        const tall = (list.current as HTMLElement).getBoundingClientRect()
          .height;
        tap();
        const left = listed();
        if (!left.includes(on)) {
          const at = placeOf(on);
          entryGone.current = { when: performance.now(), at };
          refocus.current = { on, at };
          if (left.length === 0) {
            kept.keep(tall);
          }
        }
      },
  };
}
