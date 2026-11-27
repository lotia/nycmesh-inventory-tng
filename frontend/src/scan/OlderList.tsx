/** What older builds left on this browser, listed to add to the cart or discard. */
import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { useSignal } from "@preact/signals";
import { useEffect, useMemo, useRef } from "preact/hooks";
import { say } from "../shared/i18n";
import { MAKE_CHANGES } from "../shared/outbox-notes";
import { type Event as Reported, telemetry } from "../shared/telemetry";
import { type Catalogue, KIND_WORDS, type Kind } from "./cart";
import {
  ADD_TO_CART,
  AlertWords,
  ConfirmDiscard,
  DISCARD,
  keyedByWords,
  NoteSaid,
  RULED_LIST,
} from "./Notes";
import {
  type Older,
  type OlderStore,
  type Outcome,
  olderId,
} from "./older-builds";
import { toCart, withRetired } from "./older-to-cart";
import { indexOf } from "./search";
import { SETTLE_MS, useKeptSpace } from "./settling";

/** An older build's cart, filled for each kind (olderKind). */
const CART_KIND_WORDS: Record<Kind, MessageDescriptor> = {
  move: msg`A cart: moving stock`,
  receipt: msg`A cart: receiving stock`,
  install: msg`A cart: taking it to an install`,
  open: msg`A cart: opening packages`,
};

export const CHECKED_OUT = msg`Checked out items. Add to cart or discard before sending`;
export const RETIRED_ITEM = msg`Item retired`;
export const CANNOT_ADD = msg`Cannot Add to Cart`;
export const SAYING_WHAT_IS_ON = msg`Saying what is on open packages`;
export const SOMETHING = msg`Something`;
export const A_CART_SOMETHING = msg`A cart: something`;
/** Where an older build's save was for, clause by clause (olderWhere):
 * each clause is whole, so a translator words it alone, and they follow
 * in this order, joined by spaces, rather than as a message for each of
 * the 20 ways they combine. */
export const AT_PLACE = msg({
  comment:
    "A clause of where a save was for; clauses that apply follow in this order, joined by spaces: at or from, to, for install. {place} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "at {place}",
});
export const FROM_PLACE = msg({
  comment:
    "A clause of where a save was for; clauses that apply follow in this order, joined by spaces: at or from, to, for install. {place} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "from {place}",
});
export const TO_PLACE = msg({
  comment:
    "A clause of where a save was for; clauses that apply follow in this order, joined by spaces: at or from, to, for install. {place} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "to {place}",
});
export const FOR_INSTALL = msg({
  comment:
    "A clause of where a save was for; clauses that apply follow in this order, joined by spaces: at or from, to, for install.",
  message: "for install {number}",
});
export const BY = msg({
  comment:
    "The clauses saying where an older build's save was for, then the volunteer's name.",
  message: "{where}, by {name}",
});
export const PLACE_GONE = msg`a place no longer in the catalogue`;
export const ADDED_NOT_SENT = msg`Added but not sent`;
export const ADDED_ON_NOT_SENT = msg`Added on {when} but not sent`;
export const KEPT_NOT_SENT = msg`Kept on this device, not sent.`;
export const KEPT_ON_NOT_SENT = msg`Kept on this device {when}, not sent.`;

/**
 * What older builds left and the page did not put back in the cart itself,
 * each in words, to add to the cart (older-to-cart.ts) or discard
 * (older-builds.ts). Discarding asks first, here on the page. A line that
 * cannot go back says why under it, and can be discarded on its own,
 * without asking: focus then goes to its entry's heading, and taps on the
 * entry's buttons wait a moment (SETTLE_MS). A tap that takes its entry
 * away makes every entry's buttons wait, and focus goes to the heading of
 * the entry that moved up, or of the list; the last entry taken away,
 * the list's space stays blank a moment (useKeptSpace), and focus goes to
 * what follows (`focusAfter`). One holding an item retired
 * since, and out of the catalogue, names it from the catalogue's retired
 * items, and goes in the cart only once that line is discarded.
 */
export function OlderList({
  older,
  catalogue,
  onPutBack,
  focusAfter,
}: {
  older: OlderStore;
  catalogue: Catalogue;
  /** Put one back in the cart: why not, if it was not. */
  onPutBack: (each: Older) => NotPut | null;
  /** Focus what follows the list, once nothing is listed, without
   * scrolling to it. */
  focusAfter: () => void;
}) {
  // The one whose discarding is being confirmed, and what one tap said.
  const discarding = useSignal<string | null>(null);
  const said = useSignal<{ on: string; why: OlderWhy } | null>(null);
  // When a line of each was last discarded.
  const lineGoneAt = useRef(new Map<string, number>());
  // When an entry last went by a tap, and the place it went from.
  const entryGone = useRef({ when: -Infinity, at: 0 });
  // The heading to focus once the list is drawn again: `on`'s, or, `on`
  // gone from place `at`, the one at that place once `on` is not listed.
  const refocus = useRef<{ on: string; at: number | null } | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const list = useRef<HTMLElement>(null);
  const kept = useKeptSpace();
  const listed = older.listed.value;
  // Each in words once, not on every change to the cart beside it.
  const shown = useMemo(() => {
    const offered = indexOf(catalogue).items;
    const named = withRetired(catalogue);
    return listed.map((each) => {
      const { cart, lost, parts } = toCart(each, named);
      // Keyed by what it says, not by its place, which passes to the next
      // line once it is discarded, and its Discard, focused, with it.
      const keys = keyedByWords(
        parts.map((part) => `${part.words} ${part.note ?? ""}`),
      );
      const lines = parts.map((part, at) => ({
        ...part,
        key: keys[at],
        // Goes back, but not as it is named (withRetired).
        retired:
          part.note === null &&
          part.itemId !== null &&
          !offered.has(part.itemId),
      }));
      return {
        each,
        lost,
        lines,
        // Recorded elsewhere, it never goes in a cart again.
        fills:
          cart.lines.length > 0 && each.refused?.code !== "recorded_elsewhere",
        retired: lines.some((line) => line.retired),
      };
    });
  }, [listed, catalogue]);
  /** Where `on` is listed, or -1. */
  const placeOf = (on: string) =>
    shown.findIndex(({ each }) => olderId(each) === on);
  /** Focus the heading of the entry at `at`, or the list's if none is,
   * or, none listed, what follows the list (Ali, 2026-11-27,
   * inventory-tng-lm1b). Never scrolled to: the page moving would put
   * something new under the finger (Ali, 2026-11-27). */
  const focusPlace = (at: number) => {
    const to = document.getElementById(`older-${at}`) ?? heading.current;
    if (to === null) {
      focusAfter();
    } else {
      to.focus({ preventScroll: true });
    }
  };
  // After a line's Discard, focus goes to its entry's heading, not to the
  // page; if a tap took the entry away, to the heading of the one that
  // moved up in its place, or of the list if none did (Ali, 2026-11-27,
  // inventory-tng-ensw).
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
  }, [shown]);
  if (shown.length === 0) {
    return kept.space(focusAfter);
  }
  /** A tap on one of `on`'s buttons, ignored just after a line of it was
   * discarded, or just after a tap took any entry away: the second tap of
   * a double tap would land on what moved up in its place (Ali,
   * inventory-tng-0su, inventory-tng-ensw). A pointer's tap focused that
   * button as it landed, where Enter would later discard it, so focus goes
   * back to the heading it was given (inventory-tng-bz73): of the entry at
   * the place of the one that went, or the list's, or else `on`'s. A key's
   * press (detail 0) leaves focus where the volunteer moved it. */
  const unlessSettling =
    (on: string, tap: () => void) => (event: MouseEvent) => {
      const now = performance.now();
      const entryWaits = now - entryGone.current.when < SETTLE_MS;
      if (
        entryWaits ||
        now - (lineGoneAt.current.get(on) ?? -Infinity) < SETTLE_MS
      ) {
        if (event.detail > 0) {
          focusPlace(entryWaits ? entryGone.current.at : placeOf(on));
        }
        return;
      }
      // Measured before the tap redraws the list, drawn as it was tapped.
      const tall = (list.current as HTMLElement).getBoundingClientRect().height;
      tap();
      if (!older.listed.value.some((each) => olderId(each) === on)) {
        const at = placeOf(on);
        entryGone.current = { when: performance.now(), at };
        refocus.current = { on, at };
        if (older.listed.value.length === 0) {
          kept.keep(tall);
        }
      }
    };
  /** What to tell the volunteer, under `on`, of its Discard, or a line's,
   * not done: the browser did not keep it, or it changed meanwhile, as
   * listing it again shows; of one gone, nothing. */
  const discardSaid = (on: string, done: Outcome) =>
    done === "gone" || done === "done"
      ? null
      : {
          on,
          why:
            done === "changed"
              ? ("discard_changed" as const)
              : ("discard_not_kept" as const),
        };
  return (
    <section ref={list}>
      <h2 ref={heading} tabIndex={-1}>
        {say(CHECKED_OUT)}
      </h2>
      <ul class={RULED_LIST}>
        {shown.map(({ each, lost, lines, fills, retired }, n) => {
          const on = olderId(each);
          const title = `older-${n}`;
          return (
            <li key={on} class="flex flex-col gap-2">
              <div id={title} tabIndex={-1}>
                <strong>{olderKind(each)}</strong>
                {olderWhere(each, catalogue)}
              </div>
              {each.refused === null ? (
                <small>{olderWhen(each)}</small>
              ) : (
                <NoteSaid
                  note={each.refused.note}
                  then={
                    each.refused.code === "recorded_elsewhere"
                      ? undefined
                      : say(FIX_THEN_RECORD)
                  }
                />
              )}
              {each.refused?.code === "recorded_elsewhere" && (
                <h3>{say(MAKE_CHANGES)}</h3>
              )}
              {lost !== null && <NoteSaid note={lost} />}
              {lines.length > 0 && (
                <ul class="list-none p-0">
                  {lines.map((line, at) => {
                    const named = `${title}-${at}`;
                    return (
                      <li
                        key={line.key}
                        class="flex flex-col items-start gap-1"
                      >
                        <div id={named}>
                          {line.retired && (
                            <>
                              <strong>{say(RETIRED_ITEM)}</strong>
                              <br />
                            </>
                          )}
                          {line.words}
                        </div>
                        {line.note !== null && <NoteSaid note={line.note} />}
                        {(line.note !== null || line.retired) && (
                          <button
                            type="button"
                            aria-describedby={named}
                            onClick={unlessSettling(on, () => {
                              said.value = null;
                              const done = older.dropPart(
                                each,
                                line.drop,
                                catalogue,
                              );
                              if (done === "done") {
                                lineGoneAt.current.set(on, performance.now());
                                refocus.current = { on, at: null };
                                recordOlder(each, "line_discarded");
                              } else {
                                said.value = discardSaid(on, done);
                              }
                            })}
                          >
                            {say(DISCARD)}
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
              {said.value?.on === on && (
                <p role="alert">
                  <AlertWords
                    note={{
                      bold: [olderSaid(said.value.why, fills && !retired)],
                    }}
                  />
                </p>
              )}
              {discarding.value === on ? (
                <ConfirmDiscard
                  describedBy={title}
                  onDiscard={unlessSettling(on, () => {
                    discarding.value = null;
                    const done = older.discard(each);
                    if (done === "done") {
                      recordOlder(each, "discarded");
                    } else {
                      said.value = discardSaid(on, done);
                    }
                  })}
                  onKeep={unlessSettling(on, () => {
                    discarding.value = null;
                  })}
                />
              ) : (
                <div class="actions">
                  {fills && (
                    <button
                      type="button"
                      aria-describedby={title}
                      disabled={retired}
                      onClick={unlessSettling(on, () => {
                        const why = onPutBack(each);
                        said.value = why === null ? null : { on, why };
                      })}
                    >
                      {say(retired ? CANNOT_ADD : ADD_TO_CART)}
                    </button>
                  )}
                  <button
                    type="button"
                    aria-describedby={title}
                    onClick={unlessSettling(on, () => {
                      said.value = null;
                      discarding.value = on;
                    })}
                  >
                    {say(DISCARD)}
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** Why one cannot go in the cart: it is not empty (row 17). */
export const CART_NOT_EMPTY = msg`Record or discard the current cart before adding more items`;
/** The cart one went in was not kept (row 21). */
export const NOT_KEPT = msg`This browser can't keep the cart. Clear local data, reload the page and try again.`;
/** The browser would not keep a discard. */
export const NOT_DISCARDED = msg`Unable to discard. Clear your site data to remove.`;

/** Add to cart, tapped on one changed in another tab since it was listed
 * (Ali, inventory-tng-8gj). */
export const CHANGED_ELSEWHERE = msg`That one was changed in another tab. Check it, then add it to the cart again.`;

/** CHANGED_ELSEWHERE's words once, as it is now, it offers no Add to cart
 * (Ali, inventory-tng-8gj). */
export const CHANGED_CHECK_AGAIN = msg`That one was changed in another tab. Check it again.`;

/** An entry's Discard, or a line's, tapped on one changed in another tab
 * since it was listed: nothing is discarded (Ali, inventory-tng-8gj). */
export const CHANGED_NOT_DISCARDED = msg`This item was changed in another tab. Check it before discarding.`;

/** Why one was not put in the cart: the cart is not empty, it changed
 * meanwhile, or the browser would not keep the cart. */
export type NotPut = "cart_not_empty" | "changed" | "not_kept";

/** What the older list says under one: why it was not put in the cart, or
 * not discarded (changed meanwhile, or not kept). */
type OlderWhy = NotPut | "discard_changed" | "discard_not_kept";

/** `why` in words; one changed, as it is now, that `offersAdd` (Add to
 * cart, not Cannot Add to Cart) or, if not, only to check it (Ali,
 * inventory-tng-8gj). */
export function olderSaid(why: OlderWhy, offersAdd: boolean): string {
  switch (why) {
    case "cart_not_empty":
      return say(CART_NOT_EMPTY);
    case "changed":
      return say(offersAdd ? CHANGED_ELSEWHERE : CHANGED_CHECK_AGAIN);
    case "not_kept":
      return say(NOT_KEPT);
    case "discard_changed":
      return say(CHANGED_NOT_DISCARDED);
    case "discard_not_kept":
      return say(NOT_DISCARDED);
  }
}

/** Whether it is saves or a cart, as telemetry tells them. */
export function heldIn(each: Older): "saves" | "cart" {
  return each.batch === null ? "cart" : "saves";
}

/** Tells telemetry what the volunteer did with `each`. */
export function recordOlder(
  each: Older,
  action: Extract<Reported, { kind: "older_storage" }>["action"],
) {
  telemetry.record({ kind: "older_storage", held: heldIn(each), action });
}

/** What it was for, in the words the page asks it in. */
function olderKind(each: Older): string {
  if (each.kind === "report") {
    return say(SAYING_WHAT_IS_ON);
  }
  const cart = each.batch === null;
  if (each.kind === null) {
    return say(cart ? A_CART_SOMETHING : SOMETHING);
  }
  return say((cart ? CART_KIND_WORDS : KIND_WORDS)[each.kind]);
}

/** Where, for which install, and by whom, as the catalogue names them. */
function olderWhere(each: Older, catalogue: Catalogue): string {
  const { places, volunteers } = indexOf(catalogue);
  const place = (placeId: number) => places.get(placeId) ?? say(PLACE_GONE);
  const words: string[] = [];
  const at = each.kind === "open" || each.kind === "report";
  if (each.fromId !== null) {
    words.push(say(at ? AT_PLACE : FROM_PLACE, { place: place(each.fromId) }));
  }
  if (each.toId !== null && !at) {
    words.push(say(TO_PLACE, { place: place(each.toId) }));
  }
  if (each.installNumber !== "") {
    words.push(say(FOR_INSTALL, { number: each.installNumber }));
  }
  const who =
    each.volunteerId === null ? undefined : volunteers.get(each.volunteerId);
  const where = words.length > 0 ? ` ${words.join(" ")}` : "";
  return who === undefined ? where : say(BY, { where, name: who });
}

/** What to do with one the inventory refused, after why (row 11). */
export const FIX_THEN_RECORD = msg`Add it to the cart, fix that, then record it.`;

/** When it was kept, or the cart last changed. */
function olderWhen(each: Older): string {
  if (each.at === null) {
    return say(each.batch === null ? ADDED_NOT_SENT : KEPT_NOT_SENT);
  }
  const when = new Date(each.at).toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
  return say(each.batch === null ? ADDED_ON_NOT_SENT : KEPT_ON_NOT_SENT, {
    when,
  });
}
