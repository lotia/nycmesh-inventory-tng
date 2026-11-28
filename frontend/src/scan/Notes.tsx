/** What the scan page says in notes and alerts, its lists' and the
 * camera's, the words of buttons both lists show, and asking before a
 * discard. */
import { msg } from "@lingui/core/macro";
import { Fragment } from "preact";
import { useLayoutEffect, useRef } from "preact/hooks";
import { say } from "../shared/i18n";
import { type Note, type Said, wordsOf } from "../shared/outbox-notes";
import { focusAndIgnoreTaps } from "../shared/settling";

/** Puts what the older list or a refused batch offers in an empty cart
 * (Ali, 2026-11-20, inventory-tng-r87e). */
export const ADD_TO_CART = msg`Add to cart`;
export const DISCARD = msg`Discard`;
export const CONFIRM_DISCARD = msg`Confirm discard`;
export const KEEP_IT = msg`Keep it`;

/** A list whose entries are ruled apart: the cart's lines, the outbox. */
export const RULED_LIST = "list-none p-0 *:border-t *:border-line *:py-3";

/** A box that asks the volunteer to act, as the upgrade and the name check
 * do, or says a save is held for the upgrade. */
export const CAUTION_BOX =
  "flex flex-col items-start gap-3 border-2 border-caution p-3";

/**
 * Why something cannot go back, in the alert's style. Not role="alert": it
 * is there as the list is shown, and several at once, read out together,
 * would drown each other and the page.
 */
export function NoteSaid({
  note,
  then,
}: {
  /** Null for none: `then` alone. */
  note: string | Note | null;
  then?: string | undefined;
}) {
  const shown: Note | null = typeof note === "string" ? { bold: [note] } : note;
  const problems = shown?.problems ?? [];
  // A div, not a p: a refusal's problems are a list. Spaced as a p is.
  return (
    <div class="alert my-3">
      {shown !== null && <AlertWords note={shown} />}
      {problems.length > 0 && (
        // Of a refusal, the problems after "Not recorded.", not in bold, each
        // on a line of its own (Ali, 2026-11-20, inventory-tng-61ys), words
        // an older build kept in them as they are.
        <SaidList said={problems} class="m-0" />
      )}
      {then !== undefined && (
        <>
          {note !== null && " "}
          <strong>{then}</strong>
        </>
      )}
    </div>
  );
}

/** Row 15's confirmation, asked before a discard. It comes on a tap,
 * under the finger: taps wait a moment, and focus goes to Keep it, the
 * safe choice, which an ignored tap focuses again (Ali, 2026-11-27,
 * inventory-tng-0c2z; docs/decisions/0019, Double taps). */
export function ConfirmDiscard({
  onDiscard,
  onKeep,
  describedBy,
}: {
  onDiscard: (event: MouseEvent) => void;
  onKeep: (event: MouseEvent) => void;
  describedBy?: string;
}) {
  const keep = useRef<HTMLButtonElement>(null);
  useLayoutEffect(() => {
    focusAndIgnoreTaps(document, () => keep.current);
  }, []);
  return (
    <div class="flex flex-col items-start gap-3">
      <div role="alert">{say(CONFIRM_DISCARD)}</div>
      <div class="actions">
        <button
          type="button"
          class="danger"
          aria-describedby={describedBy}
          onClick={onDiscard}
        >
          {say(DISCARD)}
        </button>
        <button type="button" ref={keep} onClick={onKeep}>
          {say(KEEP_IT)}
        </button>
      </div>
    </div>
  );
}

/**
 * A failure or refusal note's words, or why the camera could not be
 * used, in bold, the counts the page wrote
 * in it in bold italic, never a number of a name it quotes (Ali,
 * inventory-tng-93z), then any words after them (Note.plain) not in
 * bold. Inline, to sit in a p: a refusal's problems (Note.problems) are
 * NoteSaid's to list.
 */
export function AlertWords({ note: { bold, plain = [] } }: { note: Note }) {
  return (
    <>
      <strong>
        <Counted said={bold} />
      </strong>
      {plain.length > 0 && <Counted said={plain} />}
    </>
  );
}

/** Each of `said` on a line of its own, keyed by what it says. */
export function SaidList({
  said,
  class: spacing = "",
}: {
  said: readonly Said[];
  /** Spacing beyond the bare list's. */
  class?: string;
}) {
  const keys = keyedByWords(said.map(wordsOf));
  return (
    <ul class={`${spacing} list-none p-0`.trim()}>
      {said.map((each, at) => (
        <li key={keys[at]}>
          <Counted said={each} />
        </li>
      ))}
    </ul>
  );
}

/** `said`, each number standing alone in the page's own words in italic,
 * whole, in either decimal separator and grouped or not, as the words
 * (i18n's sayAmount) and the boxes (quantity's boxAmount) write one; not
 * digits joined to letters, such as LiteBeam 5AC's. */
export function Counted({ said }: { said: Said }) {
  return (
    <>
      {said.map((part, at) => (
        <Fragment key={at}>
          {typeof part === "string"
            ? part
                .split(/\b(\d+(?:[.,]\d+)*)\b/)
                .map((bit, nth) =>
                  nth % 2 === 1 ? <em key={nth}>{bit}</em> : bit,
                )
            : part.quoted}
        </Fragment>
      ))}
    </>
  );
}

/** A key for each of `says` by what it says, numbered among those saying
 * the same, so that it stays with its words, not its place. */
export function keyedByWords(says: readonly string[]): string[] {
  const seen = new Map<string, number>();
  return says.map((words) => {
    const nth = (seen.get(words) ?? 0) + 1;
    seen.set(words, nth);
    return `${words} ${nth}`;
  });
}
