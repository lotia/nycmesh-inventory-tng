/**
 * An open package where a cart's item comes from, as a card of its own
 * (docs/decisions/0020, 2026-11-12): what it shows now and after the cut
 * typed, and whether nothing usable is left on it, which follows the cut
 * (usedUpOf) until the volunteer ticks or unticks it.
 */
import { msg } from "@lingui/core/macro";
import { useId } from "preact/hooks";
import type { Item } from "./cart";
import { Field } from "./Field";
import { say } from "./i18n";
import {
  cutOf,
  type Ending,
  type Offered,
  type Taken,
  usedUpOf,
} from "./offered";
import { decimal } from "./quantity";

/** How to read the length printed along an open package of an item in a
 * length (any unit but each), in the words the count page uses
 * (web.formatting.PRINTED_LENGTH, which a backend test keeps the same;
 * inventory-tng-pg7). */
export const PRINTED_LENGTH = msg`Read the length printed on the cable where it leaves the spool. If it counts up from the start, what's left is the package size minus the mark.`;

/** The notes when the box follows the cut (Ali, 2026-11-12), by how the
 * cut ends it; an item counted in each says "any", in a length "cable". */
export const USED_EACH = msg`Your cut uses up the {name}, so it's ticked. Untick it if any is left. An administrator confirms.`;
export const USED_CABLE = msg`Your cut uses up the {name}, so it's ticked. Untick it if cable is left. An administrator confirms.`;
export const SHORT = msg`This {name} will be unusable after this cut, so it's ticked. Untick it if you'll keep the short end.`;
export const OVER = msg`Your cut is more than the {name} shows, so it's ticked. It's recorded as you typed it, and an administrator will check the {name}.`;

export const OPEN = msg({ context: "state", message: "open" });
export const OPENED = msg`opened {date}`;
export const SHOWS_NOTHING_LEFT = msg`Shows nothing left`;
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

/** Nothing taken yet. */
export const NOTHING_TAKEN: Taken = { amount: "", whole: false };

/** The note when the box follows the cut (Ali, 2026-11-12). */
export function endingNote(ending: Ending, name: string, unit: string): string {
  const note = {
    used: unit === "each" ? USED_EACH : USED_CABLE,
    short: SHORT,
    over: OVER,
  }[ending];
  return say(note, { name });
}

/** What is left after the cut, in words; blank before one is typed. */
function afterWords(after: number | undefined, unit: string): string {
  if (after === undefined) {
    return "";
  }
  return after < 0
    ? say(AMOUNT_OVER, { amount: decimal(-after), unit })
    : `${decimal(after)} ${unit}`;
}

export function OpenPackageCard({
  open,
  item,
  taken,
  moving,
  onTake,
}: {
  open: Offered;
  item: Item;
  taken: Taken;
  /** Whether it can move whole. */
  moving: boolean;
  onTake: (next: Taken) => void;
}) {
  const nowId = useId();
  const afterId = useId();
  const hintId = useId();
  const unit = item.unit;
  const printed = unit !== "each";
  // Its package as named, never a word made from the name
  // (inventory-tng-b1z): "the Spool 305 m".
  const name = open.package_name;
  const cut = cutOf(open, taken);
  const usedUp = usedUpOf(open, taken);
  const empty = open.left <= 0;
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
  return (
    <fieldset
      class={`flex flex-col gap-2 border-2 border-line p-3 ${empty ? "bg-band" : ""}`}
    >
      <legend class="flex flex-wrap items-center gap-2 px-1">
        <strong>{open.package_name}</strong>
        <span class="tag">{say(OPEN)}</span>
        <small>{say(OPENED, { date: opened })}</small>
        {empty && (
          <strong class="text-sm uppercase">{say(SHOWS_NOTHING_LEFT)}</strong>
        )}
      </legend>
      <div class="grid grid-cols-2 gap-2">
        <div>
          <div class="hint">{say(NOW)}</div>
          <div id={nowId}>
            <span class="sr-only">{say(NOW_SAID)} </span>
            {decimal(open.left)} {unit}
          </div>
        </div>
        <div>
          <div class="hint">{say(AFTER_THIS_CUT)}</div>
          <div id={afterId}>
            <span class="sr-only">{say(AFTER_THIS_CUT_SAID)} </span>
            {afterWords(cut?.after, unit)}
          </div>
        </div>
      </div>
      <Field quiet label={say(CUT_OR_TAKE, { name, date: opened, unit })}>
        {(id) => (
          <input
            id={id}
            inputMode="decimal"
            aria-describedby={`${nowId} ${afterId}`}
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
                  ? { amount: decimal(open.left), whole: true }
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
          label={say(cut === null ? WHATS_ON_NOW : WHATS_ON_AFTER, {
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
          {say(WRONG_LENGTH)}
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
