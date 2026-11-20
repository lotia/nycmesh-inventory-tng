/** The carts this device has saved: waiting, sending, held, refused or recorded. */
import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { useSignal } from "@preact/signals";
import { useEffect, useMemo, useRef } from "preact/hooks";
import { say, sayParts } from "../shared/i18n";
import {
  type Body,
  batchId,
  type Entry,
  type Outbox,
  type Recorded,
} from "../shared/outbox";
import {
  MAKE_CHANGES,
  noteOf,
  recordedHeading,
  wordsOf,
} from "../shared/outbox-notes";
import { ADD_THE_REST, REST_IN_CART, restOf } from "./add-the-rest";
import { batchWords } from "./batch";
import type { Catalogue } from "./cart";
import {
  AlertWords,
  CAUTION_BOX,
  Counted,
  keyedByWords,
  NoteSaid,
  RULED_LIST,
} from "./Notes";
import { type NotPut, olderSaid } from "./OlderList";
import { linesSaid } from "./older-builds";
import {
  ADD_DIFFERENCE,
  changesShown,
  differenceOf,
  IN_CART,
  recordedLines,
} from "./recorded-elsewhere";

export const CARTS_FROM_DEVICE = msg`Carts from this device`;
export const REGISTER_TO_SEND_WAITING = msg`<0>Register this browser</0> to send what is waiting.`;
export const RECORDED_IN = msg`Recorded (transaction {transaction}).`;
export const SENDING = msg`Sending…`;
/** A save waiting while the page is too old to send it (Ali, 2026-11-16,
 * inventory-tng-n7yv): the banner says what to do. */
export const HELD = msg`Held until you upgrade.`;
export const DISMISS = msg`Dismiss`;

/**
 * Unseen and not read out, but keeping its room, so what is below does not
 * move under a finger as it comes or goes (Ali, 2026-11-19,
 * inventory-tng-2lad). aria-hidden as well, since jsdom applies no styles.
 */
const KEEPS_ROOM = { class: "invisible", "aria-hidden": "true" } as const;

/** Whether the browser says it is online, following it as it changes. */
function useOnline(): boolean {
  const online = useSignal(navigator.onLine);
  useEffect(() => {
    const follow = () => {
      online.value = navigator.onLine;
    };
    follow();
    window.addEventListener("online", follow);
    window.addEventListener("offline", follow);
    return () => {
      window.removeEventListener("online", follow);
      window.removeEventListener("offline", follow);
    };
  }, []);
  return online.value;
}

/** Puts what a save recorded elsewhere adds in the cart (CartForm,
 * addDifference): why not, if it was not. */
export type AddDifference = (entry: Entry, difference: Body) => NotPut | null;

/** Puts the lines of a batch refused (422) its problems do not name in the
 * cart (CartForm, addRest): why not, if it was not. */
export type AddRest = (entry: Entry, rest: Body) => NotPut | null;

/**
 * What this browser sent, or is sending, and what became of each. A
 * refusal or failure says so in the alert's style, and a refused batch
 * lists its lines, to compare with what was recorded. One sent under its
 * cart's key and refused as another tab recorded it first shows what that
 * tab recorded and what differs (RecordedElsewhere), or, from a server
 * that does not say, what is left to do (SCAN_AGAIN_CHANGED), as the older
 * list does, while the page refreshes the stock (Ali, inventory-tng-g0e,
 * d; inventory-tng-0gg). One refused (422) whose problems name lines
 * offers Add the rest to cart (add-the-rest.ts).
 */
export function OutboxList({
  outbox,
  catalogue,
  cartKey,
  onAddDifference,
  onAddRest,
}: {
  outbox: Outbox;
  /** To name the lines of one refused; null until it is loaded. */
  catalogue: Catalogue | null;
  /** The cart's batch key, if it has one. */
  cartKey: string | undefined;
  onAddDifference: AddDifference;
  onAddRest: AddRest;
}) {
  const online = useOnline();
  // Under which save (batchId), why its difference, or its rest, did not
  // go in the cart.
  const notAdded = useSignal<{ on: string; why: NotPut } | null>(null);
  const stored = outbox.entries.value;
  // Of each refused, its lines, read again only as the outbox or
  // catalogue changes. By batchId, not key: one key may name two batches
  // (Outbox.queue).
  const linesOf = useMemo(
    () =>
      new Map(
        catalogue === null
          ? []
          : stored
              .filter((entry) => entry.state === "refused")
              .map((entry) => [
                batchId(entry.body),
                linesSaid(entry, catalogue),
              ]),
      ),
    [stored, catalogue],
  );
  const entries = outbox.shown().reverse();
  if (entries.length === 0) {
    return null;
  }
  // Too old, nothing this tab sends is taken, so none waiting is sent
  // until the page is loaded again (Outbox.send).
  const held = outbox.builds.newer.value === "too_old";
  // Registering sends nothing held: the banner asks for the upgrade.
  const needsRegistering =
    !held && entries.some((e) => e.waitingFor === "registration");
  return (
    <section>
      <h2>{say(CARTS_FROM_DEVICE)}</h2>
      {needsRegistering && (
        <p>
          {sayParts(REGISTER_TO_SEND_WAITING, {}, [
            (inside) => <a href="/register">{inside}</a>,
          ])}
        </p>
      )}
      <ul class={RULED_LIST}>
        {entries.map((entry) => {
          const id = batchId(entry.body);
          const { reason } = entry;
          const elsewhere =
            reason?.code === "recorded_elsewhere" ? reason : undefined;
          const recorded = catalogue === null ? undefined : elsewhere?.recorded;
          const lines = recorded === undefined ? (linesOf.get(id) ?? []) : [];
          // Offered with the cart beside it, once the catalogue is loaded.
          const rest = catalogue === null ? null : restOf(entry);
          return (
            <li key={id} class="flex flex-col items-start gap-1">
              <div>{batchWords(entry.body, entry.summary)}</div>
              {entry.state === "saved" ? (
                <small>
                  {say(RECORDED_IN, { transaction: entry.transactionId })}
                </small>
              ) : held && entry.state === "waiting" ? (
                // Asked for, an upgrade is no failure: the banner's caution,
                // not role="alert", as every one is there as the page loads.
                <div class={CAUTION_BOX}>{say(HELD)}</div>
              ) : reason === null || reason.code === "must_upgrade" ? (
                // On its way: a newer build's tab sends one an older build
                // held back here, under that build, as too old (Outbox).
                <small>{say(SENDING)}</small>
              ) : (
                // Not role="alert": every one is there as the page loads.
                <NoteSaid note={noteOf(reason, online)} />
              )}
              {elsewhere !== undefined && elsewhere.recorded === undefined && (
                <h3>{say(MAKE_CHANGES)}</h3>
              )}
              {lines.length > 0 && (
                <ul class="list-none p-0">
                  {keyedByWords(lines).map((key, at) => (
                    <li key={key}>{lines[at]}</li>
                  ))}
                </ul>
              )}
              {recorded !== undefined && catalogue !== null && (
                <RecordedElsewhere
                  entry={entry}
                  recorded={recorded}
                  catalogue={catalogue}
                  added={elsewhere?.added === true}
                  cartKey={cartKey}
                  onAdd={(difference) => {
                    const why = onAddDifference(entry, difference);
                    notAdded.value = why === null ? null : { on: id, why };
                  }}
                />
              )}
              {rest !== null && (
                <PutInCart
                  label={ADD_THE_REST}
                  inCartWords={REST_IN_CART}
                  added={reason?.code === "refused" && reason.added === true}
                  inCart={cartKey === rest.key}
                  onPut={() => {
                    const why = onAddRest(entry, rest);
                    notAdded.value = why === null ? null : { on: id, why };
                  }}
                />
              )}
              {notAdded.value?.on === id && (
                <p role="alert">
                  <AlertWords
                    note={{ bold: [olderSaid(notAdded.value.why, true)] }}
                  />
                </p>
              )}
              {entry.state === "waiting" ? (
                // Its room kept while it is sent, so the entry does not grow
                // as it finishes.
                <button type="button" tabIndex={-1} {...KEEPS_ROOM}>
                  {say(DISMISS)}
                </button>
              ) : (
                <button type="button" onClick={() => outbox.dismiss(entry)}>
                  {say(DISMISS)}
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * A save refused as another tab recorded its cart first, once the server
 * said what it recorded (Ali, inventory-tng-0gg): that, under its heading,
 * then what the save holds otherwise, each with why it cannot go back, if
 * it cannot, and under one it holds less of, that an administrator will
 * correct it; and a button that puts what it adds in the cart, until it
 * has, which an empty cart takes without it (CartForm), then that it is
 * there, until recorded (2026-11-15).
 */
function RecordedElsewhere({
  entry,
  recorded,
  catalogue,
  added,
  cartKey,
  onAdd,
}: {
  entry: Entry;
  recorded: Recorded;
  catalogue: Catalogue;
  /** Its difference is in the cart already. */
  added: boolean | undefined;
  /** The cart's batch key, if it has one. */
  cartKey: string | undefined;
  onAdd: (difference: Body) => void;
}) {
  const { was, changes, difference } = useMemo(
    () => ({
      was: recordedLines(recorded, catalogue),
      changes: changesShown(recorded, entry.body, catalogue),
      difference: differenceOf(recorded.batch, entry.body),
    }),
    [recorded, entry.body, catalogue],
  );
  // Keyed by what each says, two saying the same apart.
  const wasKeys = keyedByWords(was.map(wordsOf));
  const changeKeys = keyedByWords(changes.map(({ words }) => wordsOf(words)));
  return (
    <>
      <h3>{recordedHeading(recorded)}</h3>
      <ul class="list-none p-0">
        {was.map((said, at) => (
          <li key={wasKeys[at]}>
            <Counted said={said} />
          </li>
        ))}
      </ul>
      {changes.length > 0 && (
        <>
          <h3>{say(MAKE_CHANGES)}</h3>
          <ul class="list-none p-0">
            {changes.map(({ words, note }, at) => (
              <li key={changeKeys[at]} class="flex flex-col items-start gap-1">
                <div>
                  <Counted said={words} />
                </div>
                {note !== null && <NoteSaid note={note} />}
              </li>
            ))}
          </ul>
        </>
      )}
      {difference !== null && (
        <PutInCart
          label={ADD_DIFFERENCE}
          inCartWords={IN_CART}
          added={added === true}
          inCart={cartKey === difference.key}
          onPut={() => onAdd(difference)}
        />
      )}
    </>
  );
}

/**
 * What an outbox entry offers to put in an empty cart (CartForm): a button
 * until it has (Add these to cart, Add the rest to cart), then words that
 * it is there, until recorded.
 */
function PutInCart({
  label,
  inCartWords,
  added,
  inCart,
  onPut,
}: {
  label: MessageDescriptor;
  inCartWords: MessageDescriptor;
  /** It was put in the cart. */
  added: boolean;
  /** The cart holds it still. */
  inCart: boolean;
  onPut: () => void;
}) {
  // Once shown, the in-cart words keep their room when recorded, so Dismiss
  // below does not move; one already recorded as it is first drawn needs
  // none.
  const shownInCart = useRef(false);
  shownInCart.current ||= inCart;
  if (!added) {
    return (
      <button type="button" onClick={onPut}>
        {say(label)}
      </button>
    );
  }
  return shownInCart.current ? (
    <p {...(inCart ? {} : KEEPS_ROOM)}>{say(inCartWords)}</p>
  ) : null;
}
