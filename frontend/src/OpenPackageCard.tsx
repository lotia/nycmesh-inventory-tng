/**
 * An open package where a cart's item comes from, as a card of its own
 * (docs/decisions/0020, 2026-11-12): what it shows now and after the cut
 * typed, and whether nothing usable is left on it, which follows the cut
 * (usedUpOf) until the volunteer ticks or unticks it.
 */
import { useId } from "preact/hooks";
import {
  cutOf,
  type Ending,
  type Item,
  type Line,
  type Offered,
  usedUpOf,
} from "./cart";
import { Field } from "./Field";
import { decimal } from "./quantity";

type Taken = NonNullable<Line["open"]>[string];

/** Nothing taken yet. */
export const NOTHING_TAKEN: Taken = { amount: "", whole: false };

/** The open package's word, for "this spool": its package's name, its
 * first letter small unless the first word is a name of its own, such as
 * "UniFi" or "RJ45". */
export function packageWord(open: Pick<Offered, "package_name">): string {
  const name = open.package_name;
  const [first = ""] = name.split(" ");
  return first.slice(1) === first.slice(1).toLowerCase()
    ? name.charAt(0).toLowerCase() + name.slice(1)
    : name;
}

/** The note when the box follows the cut (Ali, 2026-11-12). */
export function endingNote(ending: Ending, word: string, unit: string): string {
  const left = unit === "each" ? "any is left" : "cable is left";
  switch (ending) {
    case "used":
      return `Your cut uses up the ${word}, so it's ticked. Untick it if ${left}. An administrator confirms.`;
    case "short":
      return `This ${word} will be unusable after this cut, so it's ticked. Untick it if you'll keep the short end.`;
    case "over":
      return `Your cut is more than the ${word} shows, so it's ticked. It's saved as you typed it, and an administrator will check the ${word}.`;
  }
}

/** What is left after the cut, in words; blank before one is typed. */
function afterWords(after: number | undefined, unit: string): string {
  if (after === undefined) {
    return "";
  }
  return after < 0
    ? `${decimal(-after)} ${unit} over`
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
  const unit = item.unit;
  const word = packageWord(open);
  const cut = cutOf(open, taken);
  const usedUp = usedUpOf(open, taken);
  const empty = open.left <= 0;
  const opened = open.opened_at.slice(0, 10);
  const note =
    cut?.ending != null && usedUp ? endingNote(cut.ending, word, unit) : "";
  return (
    <fieldset
      class={`flex flex-col gap-2 border-2 border-line p-3 ${empty ? "bg-band" : ""}`}
    >
      <legend class="flex flex-wrap items-center gap-2 px-1">
        <strong>{open.package_name}</strong>
        <span class="tag">open</span>
        <small>opened {opened}</small>
        {empty && <strong class="text-sm uppercase">Shows nothing left</strong>}
      </legend>
      <div class="grid grid-cols-2 gap-2">
        <div>
          <div class="hint">Now</div>
          <div id={nowId}>
            <span class="sr-only">Now: </span>
            {decimal(open.left)} {unit}
          </div>
        </div>
        <div>
          <div class="hint">After this cut</div>
          <div id={afterId}>
            <span class="sr-only">After this cut: </span>
            {afterWords(cut?.after, unit)}
          </div>
        </div>
      </div>
      <Field
        quiet
        label={`Cut or take from the ${word} opened ${opened} (${unit})`}
      >
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
              onTake({
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
              onTake(
                event.currentTarget.checked
                  ? { amount: decimal(open.left), whole: true }
                  : NOTHING_TAKEN,
              )
            }
          />
          All of it, moved still open
        </label>
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
            Nothing usable left on this {word}
          </label>
        )}
        <p aria-live="polite" class={note === "" ? "" : "warning mt-2"}>
          {note}
        </p>
      </div>
    </fieldset>
  );
}
