/**
 * An open package where a cart's item comes from, as a card of its own
 * (docs/decisions/0020, 2026-11-12): what it shows now and after the cut
 * typed, and whether nothing usable is left on it, which follows the cut
 * (usedUpOf) until the volunteer ticks or unticks it. Flagged when it
 * shows nothing left, or is a short end.
 */
import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { useId } from "preact/hooks";
import { countedWhole, isLength, isShortEnd } from "../amount-rules";
import { Field } from "../shared/Field";
import { say, sayAmount } from "../shared/i18n";
import { boxAmount } from "../shared/quantity";
import type { Item } from "./cart";
import {
  cutOf,
  type Ending,
  type Offered,
  type Taken,
  usedUpOf,
} from "./offered";

/** How to read the length printed along an open package of an item in a
 * length (any unit but each), in the words the count page uses
 * (web.formatting.PRINTED_LENGTH, which a backend test keeps the same;
 * inventory-tng-pg7). */
export const PRINTED_LENGTH = msg`Read the length printed on the cable where it leaves the spool. If it counts up from the start, what's left is the package size minus the mark.`;

/** The notes when the box follows the cut (Ali, 2026-11-12), by how the
 * cut ends it; an item counted in each says "taking" and "any", in a
 * length "cut" and "cable". Each names the box it ticks, {checkbox}, by
 * its label, NOTHING_USABLE (Ali, 2026-11-24, inventory-tng-uwi3). */
export const USED_EACH = msg`Taking these uses up the {name}, so “{checkbox}” is ticked. Untick it if any is left. An administrator confirms.`;
export const USED_CABLE = msg`Your cut uses up the {name}, so “{checkbox}” is ticked. Untick it if cable is left. An administrator confirms.`;
export const SHORT = msg`This {name} will be unusable after this cut, so “{checkbox}” is ticked. Untick it if you'll keep the short end.`;
export const SHORT_EACH = msg`After taking these, too few are left in the {name} to use, so “{checkbox}” is ticked. Untick it if you'll keep them.`;
export const OVER_EACH = msg`You're taking more than the {name} shows, so “{checkbox}” is ticked. It's recorded as you typed it, and an administrator will check the {name}.`;
export const OVER = msg`Your cut is more than the {name} shows, so “{checkbox}” is ticked. It's recorded as you typed it, and an administrator will check the {name}.`;

export const OPEN = msg({ context: "state", message: "open" });
export const OPENED = msg`opened {date}`;
export const SHOWS_NOTHING_LEFT = msg`Shows nothing left`;
/** The flag on a short end's card, by what is on it now (Ali,
 * 2026-11-18, inventory-tng-j5c2); of a box of pieces, TOO_FEW_LEFT (Ali,
 * 2026-11-24, inventory-tng-uwi3). */
export const SHORT_END = msg`Short end`;
export const TOO_FEW_LEFT = msg`Too few left`;
export const NOW = msg`Now`;
/** NOW, for a screen reader, before what is on it now. */
export const NOW_SAID = msg`Now:`;
export const AFTER_THIS_CUT = msg`After this cut`;
/** AFTER_THIS_CUT, for a screen reader, before what is left after it. */
export const AFTER_THIS_CUT_SAID = msg`After this cut:`;
/** What the cut takes past what the package shows. */
export const AMOUNT_OVER = msg`{amount} {unit} over`;
export const CUT_OR_TAKE = msg`Cut or take from the {name} opened {date} ({unit})`;
export const ALL_OF_IT = msg`All of it, moved still open`;
export const WHATS_ON_NOW = msg`What's on the {name} now ({unit}), if you can tell`;
export const WHATS_ON_AFTER = msg`What's on the {name} after this cut ({unit}), if you can tell`;
export const WRONG_LENGTH = msg`Shows the wrong length?`;
export const NOTHING_USABLE = msg`Nothing usable left on it`;

/** The card of an open package of an item counted each, worded for
 * pieces, not a length (Ali, 2026-11-23, inventory-tng-uwi3): "Now 300
 * left", "After taking 20". */
export const AMOUNT_LEFT = msg`{amount} left`;
/** The heading over what is left, before anything is typed to take. */
export const AFTER_TAKING_SOME = msg`After taking`;
/** AFTER_TAKING_SOME, for a screen reader, before what is left after it. */
export const AFTER_TAKING_SOME_SAID = msg`After taking:`;
export const AFTER_TAKING = msg`After taking {amount}`;
/** AFTER_TAKING, for a screen reader, before what is left after it. */
export const AFTER_TAKING_SAID = msg`After taking {amount}:`;
export const PIECES_OVER = msg`{amount} over`;
export const TAKE = msg`Take from the {name} opened {date}`;
export const HOW_MANY_NOW = msg`How many are left in the {name} now, if you can tell`;
export const HOW_MANY_AFTER = msg`How many will be left in the {name} after taking these, if you can tell`;
export const WRONG_NUMBER = msg`Shows the wrong number left?`;

/** Nothing taken yet. */
export const NOTHING_TAKEN: Taken = { amount: "", whole: false };

/** The card's words that differ by how its item is counted: the notes
 * by how the cut ends it, its short end's flag, its boxes' labels and the
 * button that asks what is on it. */
type Words = Record<
  Ending | "shortEnd" | "take" | "askNow" | "askAfter" | "wrong",
  MessageDescriptor
>;

const PIECES_WORDS: Words = {
  used: USED_EACH,
  short: SHORT_EACH,
  over: OVER_EACH,
  shortEnd: TOO_FEW_LEFT,
  take: TAKE,
  askNow: HOW_MANY_NOW,
  askAfter: HOW_MANY_AFTER,
  wrong: WRONG_NUMBER,
};

const LENGTH_WORDS: Words = {
  used: USED_CABLE,
  short: SHORT,
  over: OVER,
  shortEnd: SHORT_END,
  take: CUT_OR_TAKE,
  askNow: WHATS_ON_NOW,
  askAfter: WHATS_ON_AFTER,
  wrong: WRONG_LENGTH,
};

/** The card's words for an item in `unit`: pieces, or a length. */
const wordsFor = (unit: string): Words =>
  countedWhole(unit) ? PIECES_WORDS : LENGTH_WORDS;

/** The note when the box follows the cut (Ali, 2026-11-12). */
export function endingNote(ending: Ending, name: string, unit: string): string {
  return say(wordsFor(unit)[ending], { name, checkbox: say(NOTHING_USABLE) });
}

/** An amount on it, in words: "300 left" of pieces, else "300 m". */
function leftWords(left: number, unit: string): string {
  return countedWhole(unit)
    ? say(AMOUNT_LEFT, { amount: sayAmount(left) })
    : `${sayAmount(left)} ${unit}`;
}

/** What is left after the cut, in words; blank before one is typed. */
function afterWords(after: number | undefined, unit: string): string {
  if (after === undefined) {
    return "";
  }
  if (after >= 0) {
    return leftWords(after, unit);
  }
  const amount = sayAmount(-after);
  return countedWhole(unit)
    ? say(PIECES_OVER, { amount })
    : say(AMOUNT_OVER, { amount, unit });
}

/** The heading over what is left after the cut, and what a screen reader
 * reads before it: "After this cut" of a length, "After taking 20" of
 * pieces, once typed. */
function afterHeading(
  taking: number | undefined,
  unit: string,
): [string, string] {
  if (!countedWhole(unit)) {
    return [say(AFTER_THIS_CUT), say(AFTER_THIS_CUT_SAID)];
  }
  if (taking === undefined) {
    return [say(AFTER_TAKING_SOME), say(AFTER_TAKING_SOME_SAID)];
  }
  const amount = sayAmount(taking);
  return [say(AFTER_TAKING, { amount }), say(AFTER_TAKING_SAID, { amount })];
}

export function OpenPackageCard({
  open,
  item,
  taken,
  moving,
  onTake,
  refusedBy,
}: {
  open: Offered;
  item: Item;
  taken: Taken;
  /** Whether it can move whole. */
  moving: boolean;
  onTake: (next: Taken) => void;
  /** The id of the words refusing what is taken, while they show. */
  refusedBy?: string | undefined;
}) {
  const nowId = useId();
  const afterId = useId();
  const hintId = useId();
  const unit = item.unit;
  const printed = isLength(unit);
  const pieces = countedWhole(unit);
  const words = wordsFor(unit);
  // Its package as named, never a word made from the name
  // (inventory-tng-b1z): "the Spool 305 m".
  const name = open.package_name;
  const cut = cutOf(open, taken);
  const usedUp = usedUpOf(open, taken);
  const empty = open.left <= 0;
  // One flag at most: a short end has something left.
  const flag = empty
    ? SHOWS_NOTHING_LEFT
    : isShortEnd(open.left, open.short_below)
      ? words.shortEnd
      : null;
  const opened = open.opened_at.slice(0, 10);
  const note =
    cut?.ending != null && usedUp ? endingNote(cut.ending, name, unit) : "";
  /** `next` taken, keeping what is on it as typed only while the box asks
   * the same: what is on it now, or after the cut. */
  const take = (next: Taken) => {
    const same = (cut === null) === (cutOf(open, next) === null);
    onTake(same || !next.now ? next : { ...next, now: "" });
  };
  // What is really on it: asked when the cut passes what it shows, or
  // when the volunteer says it shows the wrong length.
  const asking = taken.now !== undefined || cut?.ending === "over";
  const [afterTitle, afterSaid] = afterHeading(
    cut === null ? undefined : open.left - cut.after,
    unit,
  );
  return (
    <fieldset
      class={`flex flex-col gap-2 border-2 border-line p-3 ${empty ? "bg-band" : ""}`}
    >
      <legend class="flex flex-wrap items-center gap-2 px-1">
        <strong>{open.package_name}</strong>
        <span class="tag">{say(OPEN)}</span>
        <small>{say(OPENED, { date: opened })}</small>
        {flag && <strong class="text-sm uppercase">{say(flag)}</strong>}
      </legend>
      <div class="grid grid-cols-2 gap-2">
        <div>
          <div class="hint">{say(NOW)}</div>
          <div id={nowId}>
            <span class="sr-only">{say(NOW_SAID)} </span>
            {leftWords(open.left, unit)}
          </div>
        </div>
        <div>
          <div class="hint">{afterTitle}</div>
          <div id={afterId}>
            <span class="sr-only">{afterSaid} </span>
            {afterWords(cut?.after, unit)}
          </div>
        </div>
      </div>
      <Field quiet label={say(words.take, { name, date: opened, unit })}>
        {(id) => (
          <input
            id={id}
            inputMode={pieces ? "numeric" : "decimal"}
            aria-invalid={refusedBy !== undefined || undefined}
            aria-describedby={[nowId, afterId, refusedBy]
              .filter(Boolean)
              .join(" ")}
            value={taken.amount}
            disabled={taken.whole}
            onInput={(event) => {
              // A new cut: the box follows it again.
              const { usedUp: _, ...rest } = taken;
              take({
                ...rest,
                amount: event.currentTarget.value,
                whole: false,
              });
            }}
          />
        )}
      </Field>
      {/* Ticked, it stays to untick, even once it shows nothing. */}
      {moving && (!empty || taken.whole) && (
        <label class="flex items-center gap-2 font-normal">
          <input
            type="checkbox"
            checked={taken.whole}
            onChange={(event) =>
              take({
                // Moved whole, it is not used up.
                ...(taken.now === undefined ? {} : { now: taken.now }),
                ...(event.currentTarget.checked
                  ? { amount: boxAmount(open.left), whole: true }
                  : NOTHING_TAKEN),
              })
            }
          />
          {say(ALL_OF_IT)}
        </label>
      )}
      {asking ? (
        <Field
          quiet
          label={say(cut === null ? words.askNow : words.askAfter, {
            name,
            unit,
          })}
        >
          {(id) => (
            <>
              <input
                id={id}
                inputMode={printed ? "decimal" : "numeric"}
                aria-describedby={printed ? hintId : undefined}
                value={taken.now ?? ""}
                onInput={(event) =>
                  onTake({ ...taken, now: event.currentTarget.value })
                }
              />
              {printed && (
                <small id={hintId} class="hint">
                  {say(PRINTED_LENGTH)}
                </small>
              )}
            </>
          )}
        </Field>
      ) : (
        <button
          type="button"
          class="self-start"
          onClick={() => onTake({ ...taken, now: "" })}
        >
          {say(words.wrong)}
        </button>
      )}
      {/* The note is always there, so it is read out as it changes. */}
      <div>
        {!taken.whole && (
          <label class="flex items-center gap-2 font-normal">
            <input
              type="checkbox"
              checked={usedUp}
              onChange={(event) =>
                onTake({ ...taken, usedUp: event.currentTarget.checked })
              }
            />
            {say(NOTHING_USABLE)}
          </label>
        )}
        <p aria-live="polite" class={note === "" ? "" : "warning mt-2"}>
          {note}
        </p>
      </div>
    </fieldset>
  );
}
