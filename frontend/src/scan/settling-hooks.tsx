/** The scan page's hooks that keep the space of what a tap took away,
 * or settle a list's taps, so the second tap of a double tap lands on
 * nothing new (shared/settling.ts, docs/decisions/0019, Double taps). */
import { useSignal } from "@preact/signals";
import type { RefObject } from "preact";
import { useEffect, useRef } from "preact/hooks";
import { focusInPlace, ignoreTaps, SETTLE_MS } from "../shared/settling";

/**
 * The space something took, kept blank for SETTLE_MS once a tap took it
 * away, such as a list's last entry or the scan page's name check, so what
 * is below it does not move up under the finger (Ali, 2026-11-27,
 * inventory-tng-lm1b, inventory-tng-m6dc). `keep` is given its height as
 * it was before the tap; `space` is the blank, or null once it has gone,
 * and calls `onTap` when tapped, as a tap on nothing may move focus.
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
 * The space of one thing drawn (`shown`, its ref) that a tap takes away,
 * kept blank for SETTLE_MS (useKeptSpace), such as the scan page's name
 * check (Ali, 2026-11-27, inventory-tng-m6dc). `going` measures it, called
 * as the tap lands, and keeps nothing if it is not drawn; `space` is the
 * blank, whose tap focuses `refocus()` without scrolling, as a tap on
 * nothing may move focus.
 */
export function useKeptSpaceOf<T extends HTMLElement>(
  refocus: () => HTMLElement | null,
) {
  const shown = useRef<T>(null);
  const kept = useKeptSpace();
  return {
    shown,
    going: () => {
      if (shown.current !== null) {
        kept.keep(shown.current.getBoundingClientRect().height);
      }
    },
    space: () => kept.space(() => focusInPlace(refocus())),
  };
}

/**
 * A list whose entries' taps may take an entry away, or change one but
 * leave it listed (`stayed`): either moves what is below up under the
 * finger, so taps on the page are ignored a moment (ignoreTaps; Ali,
 * inventory-tng-0su, inventory-tng-ensw, inventory-tng-34ns,
 * inventory-tng-r3jx). Focus then goes, without scrolling, to the heading
 * of the entry that moved up, found by its id, so one listed or gone
 * meanwhile in another tab does not send focus to another; or of the list
 * (`heading`); or, none listed, to `after`, and the list's space stays
 * blank a moment (useKeptSpace, `space`); after `stayed`, to `on`'s. An
 * ignored pointer tap puts focus back there, where Enter would otherwise
 * later tap what it landed on (inventory-tng-bz73).
 *
 * `listed` gives the entries' ids in the order they are drawn, as they
 * are kept: called as the list is drawn, and again once a tap has run.
 * The heading of the entry drawn at place `at` has the id
 * `${headingIds}-${at}`.
 */
export function useSettledList({
  listed,
  headingIds,
  after,
}: {
  listed: () => string[];
  headingIds: string;
  after: RefObject<HTMLElement | null>;
}) {
  const drawn = listed();
  // As last drawn, for a tap ignored after this render.
  const lastDrawn = useRef(drawn);
  lastDrawn.current = drawn;
  // The entry whose heading to focus once the list is drawn again, and
  // the one gone by the tap, which a render from before it went still
  // draws.
  const refocus = useRef<{ to: string | null; gone: string | null } | null>(
    null,
  );
  const heading = useRef<HTMLHeadingElement>(null);
  const list = useRef<HTMLElement>(null);
  const kept = useKeptSpace();
  /** The heading of `on`, as drawn, or the list's if it is not listed,
   * or, none listed, what follows the list (Ali, 2026-11-27,
   * inventory-tng-lm1b). */
  const headingOf = (on: string | null) => {
    const at = on === null ? -1 : lastDrawn.current.indexOf(on);
    return (
      (at < 0 ? null : document.getElementById(`${headingIds}-${at}`)) ??
      heading.current ??
      after.current
    );
  };
  /** Taps wait, and focus goes to `to`'s heading once `gone` is drawn
   * no more. */
  const settle = (to: string | null, gone: string | null) => {
    refocus.current = { to, gone };
    ignoreTaps(document, () => headingOf(to));
  };
  // After every render: the one after a tap draws what it changed.
  useEffect(() => {
    const want = refocus.current;
    if (want === null) {
      return;
    }
    if (want.gone !== null && drawn.includes(want.gone)) {
      return;
    }
    refocus.current = null;
    focusInPlace(headingOf(want.to));
  });
  return {
    /** For the list's section, measured as a tap lands. */
    list,
    /** For the list's heading, focusable (tabIndex -1). */
    heading,
    /** What is drawn in the list's place once nothing is listed. */
    space: () => kept.space(() => focusInPlace(after.current)),
    /** A tap changed `on` but left it listed. */
    stayed: (on: string) => settle(on, null),
    /** `tap`, on one of `on`'s buttons, settling the list if it took `on`
     * away. */
    tapOn: (on: string, tap: () => void) => () => {
      // Measured before the tap redraws the list, drawn as it was tapped.
      const tall = (list.current as HTMLElement).getBoundingClientRect().height;
      tap();
      const left = listed();
      if (!left.includes(on)) {
        // The first below it still listed, which moved up in its place.
        const next =
          drawn.slice(drawn.indexOf(on) + 1).find((id) => left.includes(id)) ??
          null;
        settle(next, on);
        if (left.length === 0) {
          kept.keep(tall);
        }
      }
    },
  };
}
