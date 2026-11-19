/** What the scan page's lists say in notes and alerts, and asking before a discard. */
import { msg } from "@lingui/core/macro";
import { Fragment } from "preact";
import { say } from "../shared/i18n";
import type { Note, Said } from "../shared/outbox-notes";

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
  return (
    <p class="alert">
      {note !== null && (
        <AlertWords note={typeof note === "string" ? { bold: [note] } : note} />
      )}
      {then !== undefined && (
        <>
          {note !== null && " "}
          <strong>{then}</strong>
        </>
      )}
    </p>
  );
}

/** Row 15's confirmation, asked before a discard. */
export function ConfirmDiscard({
  onDiscard,
  onKeep,
  describedBy,
}: {
  onDiscard: () => void;
  onKeep: () => void;
  describedBy?: string;
}) {
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
        <button type="button" onClick={onKeep}>
          {say(KEEP_IT)}
        </button>
      </div>
    </div>
  );
}

/**
 * A failure or refusal note's words, in bold, the counts the page wrote
 * in it in bold italic, never a number of a name it quotes (Ali,
 * inventory-tng-93z); of a refusal, the problems after "Not recorded."
 * (Note.plain) not in bold, words an older build kept in them as they
 * are.
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

/** `said`, each number standing alone in the page's own words in italic;
 * not digits joined to letters, such as LiteBeam 5AC's. */
export function Counted({ said }: { said: Said }) {
  return (
    <>
      {said.map((part, at) => (
        <Fragment key={at}>
          {typeof part === "string"
            ? part
                .split(/\b(\d+(?:\.\d+)?)\b/)
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
