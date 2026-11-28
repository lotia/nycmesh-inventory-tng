/** The cart being filled: who, what is happening, where; the camera and
 * the item search, then a newer build's alert and the cart's lines below
 * them, newest first, so a scan moves nothing above the camera (Ali,
 * 2026-11-24, inventory-tng-fdnt); then Record. */
import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import {
  batch,
  computed,
  type Signal,
  useComputed,
  useSignal,
} from "@preact/signals";
import type { RefObject, TargetedSubmitEvent } from "preact";
import { useEffect, useLayoutEffect, useMemo, useRef } from "preact/hooks";
import { Field } from "../shared/Field";
import { say, sayParts } from "../shared/i18n";
import { type Body, batchId, type Entry, type Outbox } from "../shared/outbox";
import { type Said, wordsOf } from "../shared/outbox-notes";
import { focusInPlace, focusInView, ignoreTaps } from "../shared/settling";
import { MOST_SECONDS, telemetry } from "../shared/telemetry";
import { takenOff } from "./add-the-rest";
import { BackToCamera } from "./BackToCamera";
import { type Batch, batchWords, toBatch } from "./batch";
import { CartLine } from "./CartLine";
import { scrollToPicture, useWhollyOnScreen } from "./camera-in-view";
import { labelKind } from "./camera-report";
import {
  addOne,
  askWho,
  type Cart,
  CartStore,
  type Catalogue,
  freshCart,
  KIND_WORDS,
  type Kind,
  looseUnread,
  openingPackage,
  placed,
  placeSides,
  reconcile,
  SIDES,
  type Side,
  takesFromOpen,
  unitChanged,
  withLine,
  withLineChanged,
} from "./cart";
import { useEveryMinuteAndWhenShown } from "./every-minute-and-when-shown";
import { done, Finder, type Picked, refused } from "./Finder";
import { readLabel, type Scanned } from "./label-codes";
import { NewerBuild } from "./NewerBuild";
import {
  AlertWords,
  CAUTION_BOX,
  ConfirmDiscard,
  keyedByWords,
  RULED_LIST,
} from "./Notes";
import { type NotPut, OlderList, olderSaid, recordOlder } from "./OlderList";
import type { AddDifference, AddRest } from "./OutboxList";
import { type Offered, offered, offeredAt } from "./offered";
import {
  asSave,
  nextWhole,
  type Older,
  type OlderStore,
  olderId,
} from "./older-builds";
import { type Gaps, nameOf, toCart, withRetired } from "./older-to-cart";
import { differenceOf } from "./recorded-elsewhere";
import { aboutLine } from "./refused-line";
import { type Acted, POINT, Scanner } from "./Scanner";
import { indexOf } from "./search";
import { useKeptSpaceOf } from "./settling-hooks";
import { isTextBox, useTextBoxFocused } from "./text-box-focus";
import { type Removed, removeLine, undone } from "./undo-remove";
import { sealedCounts, unseen } from "./unseen-batches";

export const NOT_KEEPING = msg`This browser isn't keeping the cart, so a reload will empty it. Free some space or leave private browsing.`;
/** What a label or a tap did, said under the camera; a refusal, which
 * adds nothing, also under the item search. The cart's lines are below
 * both. */
export const NO_PACKAGE_TO_OPEN = msg`{item} comes in no package to open.`;
export const WHICH_PACKAGE = msg`{item}: type how many of which package to open.`;
export const ADDED_ONE = msg`Added one: {item}.`;
export const ADDED_PACKAGE = msg`Added a {package}: {item}.`;
export const NOT_ADDED = msg`Not added. Fix the incorrect amount below, then scan again.`;
export const UNKNOWN_LABEL = msg`That label isn't in the inventory ({code}).`;
export const PLACE_HERE = msg({
  comment:
    "{place} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "{place}: here.",
});
export const PLACE_FROM_HERE = msg({
  comment:
    "{place} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "{place}: from here.",
});
export const PLACE_TO_HERE = msg({
  comment:
    "{place} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "{place}: to here.",
});
export const PLACE_WHICH = msg({
  comment:
    "{place} is a place of any kind, such as a store, a shelf, a bin or a volunteer's home.",
  message: "{place}: from here, or to here?",
});
export const CHANGED_BEFORE_RECORD = msg`The cart was changed in another tab. Check it, then record it again.`;
/** Said when this tab changed the cart while Record checked it. */
export const CHANGED_WHILE_CHECKED = msg`The cart changed while it was being checked, so it wasn't recorded. Tap Record again.`;
export const NOT_SENT = msg`This browser wouldn't keep the cart, so it wasn't sent. Free some space or leave private browsing, then try again.`;
export const ARE_YOU = msg`Are you {name}? This phone hasn't been used for a while.`;
export const THATS_ME = msg`That's me`;
export const NOT_ME = msg`Not me`;
export const YOUR_NAME = msg`Your name`;
/** The name list's first choice, none picked. */
export const NO_NAME_PICKED = msg`Pick your name`;
export const WHAT_IS_HAPPENING = msg`What is happening`;
export const WHERE = msg`Where`;
export const FROM = msg({ context: "place", message: "From" });
export const TO = msg({ context: "place", message: "To" });
export const INSTALL_NUMBER = msg`MeshDB install number (if you know it)`;
export const CART = msg`Cart`;
export const ADDED_BACK = msg`Added back to cart. Review before Record.`;
/** Said after Remove: <0>Undo</0> is a button, which puts the line back. */
export const REMOVED_UNDO = msg({
  message: "Removed {item}. <0>Undo</0>",
  comment: "After a line is removed from the cart; Undo puts it back.",
});
export const NOTHING_IN_CART = msg`Nothing in the cart yet.`;
export const SCAN_WITH_CAMERA = msg`Scan with the camera`;
export const SENDING_WHAT = msg`Sending: {what}. A new cart is ready.`;
export const RECORDING_AS = msg`Recording as {name}.`;
export const CHANGE = msg`Change`;
export const RECORD = msg`Record`;
export const DISCARD_CART = msg`Discard cart`;
export const PICK_A_PLACE = msg`Pick a place`;

export function CartForm({
  catalogue,
  pending,
  outbox,
  cartEmpty,
  cartKept,
  cartKey,
  older,
  adder,
  rester,
  reload,
  latest,
  cartHeading,
}: {
  catalogue: Catalogue;
  /** The outbox's batches it does not show (unseen-batches.ts, unseen). */
  pending: readonly Entry[];
  outbox: Outbox;
  /** Kept as the cart changes, here or in another tab (NewerBuild). */
  cartEmpty: Signal<boolean>;
  /** Set to whether a reload brings back this cart (CartStore.write),
   * as it is written here or followed from another tab; kept again once
   * this form goes, as what it held is then gone. */
  cartKept: Signal<boolean>;
  /** Kept as the cart's batch key changes. */
  cartKey: Signal<string | undefined>;
  /** What older builds left, to put back in the cart or discard. */
  older: OlderStore;
  /** Given what puts a save's difference in this cart (addDifference). */
  adder: { current: AddDifference | null };
  /** Given what puts a refused batch's other lines in this cart
   * (addRest). */
  rester: { current: AddRest | null };
  /** Refresh this page, offered where the camera was refused (Scanner)
   * and once a newer build is out (NewerBuild). */
  reload: () => void;
  /** The catalogue as it is now, which Record checks the cart against
   * first; the one shown if it cannot be had soon (ScanPage). */
  latest: () => Promise<Catalogue>;
  /** For the Cart heading, which the outbox's list below focuses too. */
  cartHeading: RefObject<HTMLHeadingElement | null>;
}) {
  const store = useMemo(() => new CartStore(catalogue), []);
  // The open packages to take from, and how many are sealed where, as the
  // outbox's batches the catalogue does not show leave them.
  const { opens, sealed } = useMemo(
    () => ({
      opens: offered(catalogue, pending),
      sealed: sealedCounts(catalogue, pending),
    }),
    [catalogue, pending],
  );
  const cart = store.cart;
  const problems = useSignal<Said[]>([]);
  // While Record waits for the catalogue: Record does nothing meanwhile.
  const checking = useSignal(false);
  // How many times another tab gave the cart a new revision, followed
  // here, so Record can tell who changed the cart while it waited.
  const followed = useRef(0);
  // What was last sent to be recorded, worded as it is shown.
  const saved = useSignal<Batch | null>(null);
  // While Discard cart waits to be confirmed, the cart's revision then.
  const discarding = useSignal<string | null>(null);
  // An older save just put back in the cart, and what it left to do again.
  const putBack = useSignal<Gaps | null>(null);
  // The line last removed, while Undo may put it back (undo-remove.ts).
  const removed = useSignal<Removed | null>(null);
  // Where focus goes once the page has rendered, as what had it may be
  // gone: Remove's button after Remove, and Undo's after Undo.
  const focusNext = useRef<(() => HTMLElement | null) | null>(null);
  const undoButton = useRef<HTMLButtonElement>(null);
  const discardButton = useRef<HTMLButtonElement>(null);
  const scanButton = useRef<HTMLButtonElement>(null);
  const restoredRemove = useRef<HTMLButtonElement>(null);
  const restoredId = useRef<number | null>(null);
  // After a while unused, the phone may have changed hands: offer to
  // change the name, without stopping anyone saving.
  const checkName = useSignal(askWho(cart.value, catalogue));
  const nameList = useRef<HTMLSelectElement>(null);
  const nameCheck = useKeptSpaceOf<HTMLDivElement>(() => nameList.current);

  // Tabs share one cart; a refreshed catalogue clears what it no longer has.
  // Another tab's amounts in a unit this one does not show: this tab's
  // catalogue is read again, to tell which is the newer (CartStore.listen).
  useEffect(
    () =>
      store.listen((revised, behind) => {
        if (behind) {
          void latest();
        }
        if (revised) {
          followed.current += 1;
        }
        cartKept.value = true;
        problems.value = problems.value.filter(
          (problem) => !isNotKeeping(problem),
        );
      }),
    [store],
  );
  useEffect(
    () => () => {
      cartKept.value = true;
    },
    [],
  );
  // Before the page is painted, so an amount cleared as typed in a unit
  // changed since is never shown beside the new unit.
  useLayoutEffect(() => store.useCatalogue(catalogue), [catalogue]);
  const empty = cart.value.lines.length === 0;
  const batchKey = cart.value.batchKey;
  useEffect(() => {
    cartKey.value = batchKey;
  }, [batchKey]);
  useEffect(() => {
    cartEmpty.value = empty;
    // Emptied, here or in another tab, nothing is left to confirm: what
    // fills it next is asked about afresh.
    if (empty) {
      discarding.value = null;
    }
  }, [empty]);

  // Asked when the page is shown again, and checked each minute, so a hub
  // tablet left on asks too. Never, when administrators set 0.
  useEveryMinuteAndWhenShown(() => {
    if (askWho(cart.value, catalogue)) {
      checkName.value = true;
    }
  }, [catalogue]);

  /** Say so if a reload would not bring back the cart. */
  function kept(stored: boolean) {
    cartKept.value = stored;
    problems.value = stored ? [] : [[say(NOT_KEEPING)]];
  }

  /** How many of what is said above the lines and Record are shown:
   * Added back, Undo's offer, the problems, Sending; and on the lines, a
   * unit changed, above the line's boxes, which a tick may clear. */
  const saidAbove = () =>
    (putBack.value === null ? 0 : 1) +
    (removed.value?.revision === cart.value.revision ? 1 : 0) +
    problems.value.length +
    (saved.value === null ? 0 : 1) +
    cart.value.lines.filter((line) => {
      const item = indexOf(catalogue).items.get(line.itemId);
      return item !== undefined && unitChanged(line, item);
    }).length;

  /** A tap took away what was above other controls, which moved up: taps
   * are ignored a moment, and focus goes, without scrolling, to `to` once
   * it is drawn (Ali, 2026-11-27, inventory-tng-r3jx). */
  function settle(to: () => HTMLElement | null) {
    focusNext.current = to;
    ignoreTaps(document, to);
  }

  /** Change the cart. What a tap's change takes away of what is said
   * above the lines and Record moves what is below up, so taps are ignored
   * a moment, an ignored one focusing what had focus (inventory-tng-r3jx).
   * Not `tapped`, as when picked in a list's picker or read from a label,
   * or typed in a box, no finger is on the page to land again. */
  function change(next: Cart, tapped = true) {
    const said = saidAbove();
    // Focus is on an HTML or SVG element, either of which takes focus().
    const focused = document.activeElement as HTMLElement | null;
    saved.value = null;
    putBack.value = null;
    discarding.value = null;
    removed.value = null;
    focusNext.current = null;
    kept(store.change(next));
    if (saidAbove() < said && tapped && !isTextBox(focused)) {
      ignoreTaps(document, () => focused);
    }
  }

  /** Take the line for this item out, offering Undo. */
  function remove(itemId: number) {
    const taken = removeLine(cart.value, itemId);
    // Only a line shown is removed.
    /* v8 ignore next */
    if (taken === null) {
      return;
    }
    change(taken.next);
    removed.value = { ...taken.removed, revision: cart.value.revision };
    // The lines below move up: Undo, above them, takes the focus.
    settle(() => undoButton.current);
    telemetry.record({ kind: "line_removed" });
  }

  /** Put the line last removed back where it was, if the cart, here or
   * in another tab, has not changed since. */
  function undo() {
    const offer = removed.value;
    const current = store.forSaving();
    if (offer === null || current?.revision !== offer.revision) {
      removed.value = null;
      settle(() => cartHeading.current);
      return;
    }
    change(reconcile(undone(current, offer), catalogue));
    restoredId.current = offer.line.itemId;
    settle(() => restoredRemove.current);
    telemetry.record({ kind: "removal_undone" });
  }

  // Whether Undo has focus as this render begins. If the render drops the
  // offer (the cart changed here or in another tab, or Undo put nothing
  // back), focus goes to the Cart heading, not the page.
  const undoFocused =
    undoButton.current !== null &&
    document.activeElement === undoButton.current;
  // Once its target is rendered, which may be a render after the cart's;
  // without scrolling, as the page moving would put something new under
  // the finger.
  useEffect(() => {
    const want = focusNext.current;
    const target =
      want !== null
        ? want()
        : undoFocused && undoButton.current === null
          ? cartHeading.current
          : null;
    if (target !== null) {
      focusNext.current = null;
      focusInPlace(target);
    }
  });

  /** Change the cart's `fields`, `tapped` or not (change). */
  function set(fields: Partial<Cart>, tapped = true) {
    change(
      placed(cart.value, { ...cart.value, ...fields }, catalogue, sealed),
      tapped,
    );
  }

  /** Add one, and say what: opening, an item is the package it opens
   * (openingPackage), as only whole packages are opened; with no such
   * package, its line, for the volunteer to fill. A package's own label
   * is the volunteer's choice, kept as typed. Read from a label, or picked
   * in the search, whose tap Finder settles: not `tapped` (change). */
  function add(itemId: number, packageId?: number): Picked {
    const item = indexOf(catalogue).items.get(itemId);
    const opening = packageId === undefined && cart.value.kind === "open";
    const pack = opening
      ? openingPackage(item, cart.value.fromId, sealed)
      : item?.packages.find((each) => each.id === packageId);
    if (opening && pack === undefined) {
      if (item?.packages.length === 0) {
        return refused(
          say(NO_PACKAGE_TO_OPEN, { item: item.name }),
          aboutLine(cart.value, itemId),
        );
      }
      change(withLine(cart.value, itemId), false);
      return done(say(WHICH_PACKAGE, { item: item?.name }));
    }
    if (pack === undefined && looseUnread(cart.value, itemId)) {
      // Loose that reads as no amount is left as typed (cart.bumped).
      return refused(say(NOT_ADDED), aboutLine(cart.value, itemId));
    }
    const next = addOne(cart.value, itemId, pack?.id);
    // A package's own label is the volunteer's choice (Line.typed).
    change(
      packageId === undefined
        ? next
        : withLineChanged(next, itemId, (line) => ({ ...line, typed: true })),
      false,
    );
    return done(
      pack === undefined
        ? say(ADDED_ONE, { item: item?.name })
        : say(ADDED_PACKAGE, { package: pack.name, item: item?.name }),
    );
  }

  // The camera, while open, and a place it read, asking From or To.
  const scanning = useSignal(false);
  // The camera's picture, while it shows (useWhollyOnScreen, BackToCamera).
  const picture = useSignal<HTMLVideoElement | null>(null);
  const pictureOnScreen = useWhollyOnScreen(picture.value);
  const typing = useTextBoxFocused();
  const tooOld = useComputed(() => outbox.builds.newer.value === "too_old");
  const placeRead = useSignal<number | null>(null);
  const fromList = useRef<HTMLSelectElement>(null);
  const toList = useRef<HTMLSelectElement>(null);
  // The side the question was last answered with.
  const answeredSide = useRef<Side>("from");
  /** The From or To list. */
  const placeList = (side: Side) =>
    (side === "from" ? fromList : toList).current;
  const placeQuestion = useKeptSpaceOf<HTMLFieldSetElement>(() =>
    placeList(answeredSide.current),
  );
  // What the answer to that question did, in words, till the next label.
  const placeAnswer = useSignal<string | null>(null);

  /** Acts on a label, read by the camera or typed by a scanner gun; in a
   * batch, so a place's question asked again is not seen put away. */
  function actOn(read: Scanned): Picked {
    return batch(() => {
      if (read.kind === "unknown") {
        // A stray code leaves a place's question to answer.
        return refused(say(UNKNOWN_LABEL, { code: read.code }));
      }
      placeRead.value = null;
      placeAnswer.value = null;
      const { places } = indexOf(catalogue);
      if (read.kind === "place") {
        const place = places.get(read.locationId);
        const [only, other] = placeSides(cart.value);
        if (only !== undefined && other === undefined) {
          placeOn(only, read.locationId, false);
          const here =
            cart.value.kind === "open" ? PLACE_HERE : PLACE_FROM_HERE;
          return done(say(only === "from" ? here : PLACE_TO_HERE, { place }));
        }
        placeRead.value = read.locationId;
        return done(say(PLACE_WHICH, { place }));
      }
      const added = add(read.itemId, read.packageId);
      // A buzz where the phone has one (not iOS Safari).
      navigator.vibrate?.(60);
      return added;
    });
  }

  /** Fill `side` with the place, `tapped` or read from its label. */
  function placeOn(side: Side, locationId: number, tapped = true) {
    set(
      side === "from" ? { fromId: locationId } : { toId: locationId },
      tapped,
    );
    placeRead.value = null;
  }

  /** A place's question answered with From or To, saying what it did as a
   * place's label with one side open does (Ali, 2026-11-24,
   * inventory-tng-8ary). Its space stays blank a moment, so what is below
   * does not move up under the finger, and focus goes, without scrolling,
   * to the place it filled (inventory-tng-m6dc). */
  function answerPlace(side: Side, locationId: number) {
    const place = indexOf(catalogue).places.get(locationId);
    placeQuestion.going();
    answeredSide.current = side;
    focusInPlace(placeList(side));
    batch(() => {
      placeAnswer.value = say(
        side === "from" ? PLACE_FROM_HERE : PLACE_TO_HERE,
        { place },
      );
      placeOn(side, locationId);
    });
  }

  /** Whether what a label did still stands, for the camera's line
   * (Scanner's Acted): a refusal; a place's question, till it is
   * answered, then saying what the answer did, or put away, here or by a
   * label in the item search, then Point. */
  function standsFor(picked: Picked): Acted["stands"] {
    if (picked.refused) {
      return true;
    }
    const asked = placeRead.peek();
    if (asked === null) {
      return undefined;
    }
    return computed(() =>
      placeRead.value === asked ? null : (placeAnswer.value ?? POINT),
    );
  }

  /** The cart to record, as a batch, checked against `against`; or null,
   * saying what is to do first. */
  function checked(against: Catalogue, open: readonly Offered[]) {
    // Another tab may have changed or saved this cart: saving what this tab
    // shows could record it twice, so it shows that cart instead.
    const current = store.forSaving();
    if (current === null) {
      problems.value = [[say(CHANGED_BEFORE_RECORD)]];
      return null;
    }
    const batch = toBatch(current, against, open);
    if ("problems" in batch) {
      problems.value = batch.problems;
      for (const separator of batch.refused ?? []) {
        telemetry.record({ kind: "other_separator", separator });
      }
      return null;
    }
    return { current, batch };
  }

  /** Record the cart, checked first against the catalogue as it is now:
   * one that changed a unit or a package's size since clears what was
   * typed in it, said beside its box, and nothing is queued. Without an
   * answer in time, it is queued as checked against the one shown, and the
   * server refuses what changed (Ali, 2026-11-23, inventory-tng-h7ua). A
   * cart changed meanwhile is not queued: the volunteer is asked to tap
   * Record again or, if another tab changed it (whether or not this one
   * did too), to check it first (Ali, 2026-11-23, inventory-tng-h60m).
   * One emptied meanwhile has nothing to record, so nothing is said. */
  async function save(event: TargetedSubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    if (checking.value || checked(catalogue, opens) === null) {
      return;
    }
    const tapped = cart.value.revision;
    const followedThen = followed.current;
    checking.value = true;
    const against = await latest().finally(() => {
      checking.value = false;
    });
    if (cart.value.revision !== tapped) {
      if (cart.value.lines.length > 0) {
        // Beside a reload's warning, if this tab's change was not kept.
        problems.value = [
          ...problems.value.filter(isNotKeeping),
          [
            say(
              followed.current === followedThen
                ? CHANGED_WHILE_CHECKED
                : CHANGED_BEFORE_RECORD,
            ),
          ],
        ];
      }
      return;
    }
    store.useCatalogue(against);
    // With what the outbox holds that it does not show, as `pending` is.
    const ready = checked(
      against,
      offered(against, unseen(outbox.entries.value, against)),
    );
    if (ready === null) {
      return;
    }
    const { current, batch } = ready;
    // Under the cart's key, which any tab that records this cart sends.
    const queued = outbox.queue(batch.draft, batch.summary, current.batchKey);
    if (queued === "not_kept") {
      // Nothing was sent, and the cart stays to try again.
      problems.value = [[say(NOT_SENT)]];
      return;
    }
    if (queued !== "queued") {
      // Already queued, or listed as recorded elsewhere, saying so: no new
      // save, so nothing is sending and no time to save is counted.
      change(freshCart(current));
      emptied();
      return;
    }
    if (current.startedAt > 0) {
      // A cart kept overnight counts as a day, and a clock put back as none.
      const seconds = Math.min(
        Math.max((Date.now() - current.startedAt) / 1000, 0),
        MOST_SECONDS,
      );
      telemetry.record({ kind: "scan_to_save", seconds });
    }
    change(freshCart(current));
    saved.value = batch;
    emptied();
  }

  /** The cart's lines went, by Record or Discard cart, and what is below
   * them moved up: focus goes to the Cart heading. */
  function emptied() {
    settle(() => cartHeading.current);
  }

  /** Drop the cart's lines, and with them its batch key, as Record does,
   * once the volunteer has confirmed it; who and where stay, as after
   * Record. Not if another tab changed the cart since it was asked:
   * what it added was never confirmed. */
  function discard() {
    const asked = discarding.value;
    const current = store.forSaving();
    if (current === null || current.revision !== asked) {
      discarding.value = null;
      problems.value = [[say(CHANGED_BEFORE_DISCARD)]];
      settle(() => discardButton.current);
      return;
    }
    change(freshCart(current));
    emptied();
    telemetry.record({ kind: "cart_discarded" });
  }

  /** The cart as stored, to fill if empty: another tab may have filled
   * it, unheard yet, which filling it would write over. */
  function storedCart(): Cart {
    return store.forSaving() ?? cart.value;
  }

  /** Fill this cart, as stored, which must be empty, with `save`, once
   * `ready` says it still may (else why not), and `forget` it where it
   * was (false if that could not be): putInCart's, addDifference's and
   * addRest's steps; the status says what was `taken` off first. True
   * once filled, else why not. */
  function fill(
    save: Older,
    automatically: boolean,
    forget: () => boolean,
    ready: () => NotPut | null | true = () => true,
    taken: Gaps = [],
  ): NotPut | null | true {
    const current = storedCart();
    if (current.lines.length > 0) {
      return "cart_not_empty";
    }
    const { cart: filled, gaps } = toCart(save, catalogue, current);
    const why = ready();
    if (why !== true) {
      return why;
    }
    const before = cart.value;
    if (!store.change(filled)) {
      // Not kept, a reload would empty it while what it came from stays
      // listed, to be put back and saved twice: the cart stays as it was.
      cart.value = before;
      return "not_kept";
    }
    kept(true);
    if (!automatically) {
      // Put in after a save, without a tap, it leaves that save said.
      saved.value = null;
    }
    if (!forget()) {
      problems.value = [[say(NOT_REMOVED)]];
    }
    putBack.value = [...taken, ...gaps];
    return true;
  }

  /** Put an older build's save, or cart, in this cart, which must be
   * empty, and forget it there: moved, never copied, so it is saved once.
   * Why not, if it was not and there is anything to say: of one gone
   * meanwhile, nothing, as it is no longer listed to say it under. */
  function putInCart(each: Older, automatically = false): NotPut | null {
    const why = fill(
      each,
      automatically,
      () => older.remove(each) === "done",
      () => {
        // Read again first: an older tab may have sent or changed it
        // meanwhile, or another tab discarded a line of it, which this
        // would put back. Listed again, as it is now, to check.
        const now = older.compared(each);
        if (now === "unchanged") {
          return true;
        }
        older.refresh();
        return now === "changed" ? "changed" : null;
      },
    );
    if (why !== true) {
      return why;
    }
    recordOlder(
      each,
      automatically ? "put_in_cart_automatically" : "put_in_cart",
    );
    return null;
  }

  /** Put what a save recorded elsewhere adds (recorded-elsewhere.ts,
   * differenceOf) in this cart, which must be empty, under the difference's
   * own key, and mark the save so, offering it no more: by a tap, or
   * `automatically` into an empty cart. Why not, if it was not. */
  function addDifference(
    entry: Entry,
    difference: Body,
    automatically = false,
  ): NotPut | null {
    const why = fill(asSave(difference), automatically, () =>
      outbox.addedToCart(entry),
    );
    if (why !== true) {
      return why;
    }
    telemetry.record({
      kind: "difference_added",
      how: automatically ? "automatic" : "tap",
    });
    return null;
  }
  adder.current = addDifference;

  /** Put the `rest` of a batch refused (422), the lines its problems do
   * not name (add-the-rest.ts, restOf), in this cart, which must be
   * empty, and mark it so, offering it no more; the status says each line
   * taken off, and why, before what else could not go back (Ali,
   * 2026-11-20, inventory-tng-clvh). Why not, if it was not. */
  function addRest(entry: Entry, rest: Body): NotPut | null {
    const why = fill(
      asSave(rest),
      false,
      () => outbox.addedToCart(entry),
      undefined,
      takenOff(entry, catalogue),
    );
    if (why !== true) {
      return why;
    }
    telemetry.record({ kind: "rest_put_back" });
    return null;
  }
  rester.current = addRest;

  // An empty cart takes what a save recorded elsewhere adds, without a
  // tap, as it takes what an older build left (Ali, inventory-tng-0gg,
  // 2026-11-15, 2): the oldest first, each tried once a load, so one the
  // browser would not keep is not tried for ever, and stays offered. By
  // batchId, not key: one key may name two batches (Outbox.queue).
  const differencesTried = useRef(new Set<string>());
  const outboxEntries = outbox.entries.value;
  useEffect(() => {
    if (!empty) {
      return;
    }
    for (const entry of outboxEntries) {
      const { reason } = entry;
      const id = batchId(entry.body);
      if (
        reason?.code !== "recorded_elsewhere" ||
        reason.recorded === undefined ||
        reason.added === true ||
        differencesTried.current.has(id)
      ) {
        continue;
      }
      const difference = differenceOf(reason.recorded.batch, entry.body);
      if (difference === null) {
        continue;
      }
      differencesTried.current.add(id);
      const why = addDifference(entry, difference, true);
      if (why === "cart_not_empty") {
        // Filled in another tab meanwhile: tried again once it is empty.
        differencesTried.current.delete(id);
      } else if (why !== null) {
        problems.value = [[olderSaid(why, true)]];
      }
      return;
    }
  }, [empty, outboxEntries, catalogue]);

  // An empty cart takes what an older build left that goes back whole,
  // without a tap: the oldest first, one at a time. Each is tried once a
  // load, so one the browser would not keep is not tried for ever; one
  // changed meanwhile, again as it is now, as listed again.
  // Not once a newer build is out: it puts them back itself, and a cart
  // filled here would hold its reload back (NewerBuild).
  const triedAutomatically = useRef(new Set<string>());
  const listed = older.listed.value;
  const newer = outbox.builds.newer.value;
  useEffect(() => {
    if (!empty || newer !== null) {
      return;
    }
    const next = nextWhole(listed, catalogue, triedAutomatically.current);
    if (next === undefined) {
      return;
    }
    const id = olderId(next);
    triedAutomatically.current.add(id);
    const why = putInCart(next, true);
    if (why === "cart_not_empty" || why === "changed") {
      // The cart filled in another tab meanwhile, tried again once it is
      // empty; or this changed, tried again as listed now. Unsaid.
      triedAutomatically.current.delete(id);
    } else if (why !== null) {
      problems.value = [[olderSaid(why, true)]];
    }
  }, [empty, listed, catalogue, newer]);

  const sides = SIDES[cart.value.kind];
  const { items, volunteers } = indexOf(catalogue);
  const name =
    cart.value.volunteerId === null
      ? undefined
      : volunteers.get(cart.value.volunteerId);

  /** Put the name check away, its space kept blank a moment if it was
   * shown, so what is below does not move up under the finger, and focus
   * the name list: without scrolling, from the check's own answers (Ali,
   * 2026-11-27, inventory-tng-m6dc); scrolled into view if it is not, from
   * Change, far below it, whose tap would otherwise show nothing, the
   * page's taps then ignored a moment as everything moved
   * (inventory-tng-r3jx). */
  function answerNameCheck({ scroll = false } = {}) {
    nameCheck.going();
    checkName.value = false;
    if (scroll) {
      focusInView(nameList.current);
      ignoreTaps(document, () => nameList.current);
    } else {
      focusInPlace(nameList.current);
    }
  }
  // Undo, while the cart is as removing left it.
  const undoable =
    removed.value?.revision === cart.value.revision ? removed.value : null;
  // Keyed by what each says, two saying the same apart.
  const problemKeys = keyedByWords(problems.value.map(wordsOf));
  // Two put back alike in words, as two items no longer in the catalogue
  // taken off, are both listed.
  const putBackKeys = keyedByWords(putBack.value ?? []);

  return (
    <form onSubmit={save}>
      <OlderList
        older={older}
        catalogue={catalogue}
        onPutBack={putInCart}
        after={cartHeading}
      />
      {checkName.value && name !== undefined ? (
        <div ref={nameCheck.shown} role="alert" class={CAUTION_BOX}>
          <div>{sayParts(ARE_YOU, { name: <strong>{name}</strong> })}</div>
          <div class="actions">
            <button
              type="button"
              onClick={() => {
                answerNameCheck();
                // Only the time changes, so "Sending" stays on show.
                kept(store.touch());
              }}
            >
              {say(THATS_ME)}
            </button>
            <button
              type="button"
              onClick={() => {
                answerNameCheck();
                set({ volunteerId: null });
              }}
            >
              {say(NOT_ME)}
            </button>
          </div>
        </div>
      ) : (
        nameCheck.space()
      )}
      <Field label={say(YOUR_NAME)}>
        {(id) => (
          <select
            id={id}
            ref={nameList}
            value={String(cart.value.volunteerId ?? "")}
            onChange={(event) =>
              set({ volunteerId: idOrNull(event.currentTarget.value) }, false)
            }
          >
            <option value="">{say(NO_NAME_PICKED)}</option>
            {catalogue.volunteers.map((volunteer) => (
              <option key={volunteer.id} value={volunteer.id}>
                {volunteer.name}
              </option>
            ))}
          </select>
        )}
      </Field>

      {/* Choices close together, each tall enough to tap. */}
      <fieldset class="gap-1">
        <legend>{say(WHAT_IS_HAPPENING)}</legend>
        {(Object.entries(KIND_WORDS) as [Kind, MessageDescriptor][]).map(
          ([kind, words]) => (
            <label key={kind} class="flex items-center gap-2 py-1 font-normal">
              <input
                type="radio"
                name="kind"
                value={kind}
                checked={cart.value.kind === kind}
                onChange={() => set({ kind })}
              />
              {say(words)}
            </label>
          ),
        )}
      </fieldset>

      {sides.from && (
        <Place
          label={say(cart.value.kind === "open" ? WHERE : FROM)}
          list={fromList}
          catalogue={catalogue}
          value={cart.value.fromId}
          onPick={(fromId) => set({ fromId }, false)}
        />
      )}
      {sides.to && (
        <Place
          label={say(TO)}
          list={toList}
          catalogue={catalogue}
          value={cart.value.toId}
          onPick={(toId) => set({ toId }, false)}
        />
      )}
      {cart.value.kind === "install" && (
        <Field label={say(INSTALL_NUMBER)}>
          {(id) => (
            <input
              id={id}
              inputMode="numeric"
              autoComplete="off"
              value={cart.value.installNumber}
              onInput={(event) =>
                set({ installNumber: event.currentTarget.value })
              }
            />
          )}
        </Field>
      )}

      {scanning.value ? (
        <Scanner
          cart={cart.value}
          onCode={(code) => {
            const read = readLabel(catalogue, code);
            const picked = actOn(read);
            return {
              ...picked,
              label: labelKind(read),
              stands: standsFor(picked),
            };
          }}
          onDone={() => {
            // The camera goes, and what is below it moves up a screen.
            scanning.value = false;
            settle(() => scanButton.current);
          }}
          onPicture={(video) => {
            picture.value = video;
            // Brought into view once, in answer to Scan with the camera;
            // never after a scan (Ali, 2026-11-24, inventory-tng-rj2g).
            if (video !== null) {
              scrollToPicture(video);
            }
          }}
          onScreen={pictureOnScreen}
          typing={typing}
          tooOld={tooOld}
          reload={reload}
          reloadKeepsCart={cartKept.value}
        />
      ) : (
        <button
          type="button"
          ref={scanButton}
          onClick={() => (scanning.value = true)}
        >
          {say(SCAN_WITH_CAMERA)}
        </button>
      )}
      {placeRead.value !== null ? (
        <PlaceSides
          question={placeQuestion.shown}
          place={placeRead.value}
          name={indexOf(catalogue).places.get(placeRead.value) ?? ""}
          sides={placeSides(cart.value)}
          onPick={answerPlace}
        />
      ) : (
        placeQuestion.space()
      )}

      <Finder
        catalogue={catalogue}
        cart={cart.value}
        onAdd={add}
        onLabel={actOn}
        tooOld={tooOld}
        after={cartHeading}
      />

      {/* Below the camera, as a scan may take it away (Ali, 2026-11-24,
          inventory-tng-oaao). */}
      <NewerBuild
        outbox={outbox}
        cartEmpty={cartEmpty}
        cartKept={cartKept}
        reload={reload}
      />
      <h2 ref={cartHeading} tabIndex={-1}>
        {say(CART)}
      </h2>
      {putBack.value !== null && (
        <div role="status">
          <p>{say(ADDED_BACK)}</p>
          {putBack.value.length > 0 && (
            <ul>
              {putBack.value.map((gap, at) => (
                <li key={putBackKeys[at]}>{gap}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {undoable !== null && (
        <p role="status">
          {sayParts(
            REMOVED_UNDO,
            {
              // Named even if the catalogue names it only as retired, as
              // the older builds' lists do (withRetired).
              item: (
                <strong>
                  {nameOf(
                    indexOf(withRetired(catalogue)).items.get(
                      undoable.line.itemId,
                    ),
                  )}
                </strong>
              ),
            },
            [
              (inside) => (
                <button type="button" ref={undoButton} onClick={undo}>
                  {inside}
                </button>
              ),
            ],
          )}
        </p>
      )}
      {cart.value.lines.length === 0 ? (
        <p>{say(NOTHING_IN_CART)}</p>
      ) : (
        <ul class={RULED_LIST}>
          {cart.value.lines.map((line) => {
            const item = items.get(line.itemId);
            return (
              <CartLine
                key={line.itemId}
                line={line}
                item={item}
                opens={
                  takesFromOpen(cart.value.kind)
                    ? offeredAt(opens, line.itemId, cart.value.fromId)
                    : []
                }
                moving={cart.value.kind === "move"}
                onChange={(next) =>
                  set(
                    withLineChanged(cart.value, line.itemId, () => ({
                      ...next,
                      typed: true,
                    })),
                  )
                }
                onRemove={() => remove(line.itemId)}
                removeRef={
                  line.itemId === restoredId.current ? restoredRemove : null
                }
              />
            );
          })}
        </ul>
      )}

      {problems.value.length > 0 && (
        <ul role="alert" class="list-none p-0">
          {problems.value.map((problem, at) => (
            <li key={problemKeys[at]}>
              <AlertWords note={{ bold: problem }} />
            </li>
          ))}
        </ul>
      )}
      {saved.value !== null && (
        <p role="status">
          {say(SENDING_WHAT, {
            what: batchWords(saved.value.draft, saved.value.summary),
          })}
        </p>
      )}
      {name !== undefined && (
        <p>
          {sayParts(RECORDING_AS, { name: <strong>{name}</strong> })}{" "}
          <button
            type="button"
            onClick={() => answerNameCheck({ scroll: true })}
          >
            {say(CHANGE)}
          </button>
        </p>
      )}
      <button
        type="submit"
        class="primary w-full text-xl"
        disabled={checking.value}
      >
        {say(RECORD)}
      </button>
      {!empty &&
        (discarding.value !== null ? (
          <ConfirmDiscard
            onDiscard={discard}
            onKeep={() => {
              discarding.value = null;
              settle(() => discardButton.current);
            }}
          />
        ) : (
          <button
            type="button"
            class="self-start"
            ref={discardButton}
            onClick={() => {
              discarding.value = cart.value.revision;
            }}
          >
            {say(DISCARD_CART)}
          </button>
        ))}
      {/* Last, as it shows at the bottom of the screen. */}
      {picture.value !== null && (
        <BackToCamera
          camera={picture.value}
          onScreen={pictureOnScreen}
          typing={typing}
        />
      )}
    </form>
  );
}

/** One went in the cart, but stays listed (row 22). */
export const NOT_REMOVED = msg`This item wasn't automatically removed. Record the cart and try to remove.`;
/** Discard cart, confirmed after another tab changed the cart. */
export const CHANGED_BEFORE_DISCARD = msg`The cart was changed in another tab. Check it, then discard it again.`;
/** From or To, for a place a label named: the side still empty first. */
function PlaceSides({
  question,
  place,
  name,
  sides,
  onPick,
}: {
  question: RefObject<HTMLFieldSetElement | null>;
  place: number;
  name: string;
  sides: Side[];
  onPick: (side: Side, place: number) => void;
}) {
  return (
    <fieldset ref={question}>
      <legend>{name}</legend>
      <div class="actions">
        {sides.map((side) => (
          <button key={side} type="button" onClick={() => onPick(side, place)}>
            {say(side === "from" ? FROM : TO)}
          </button>
        ))}
      </div>
    </fieldset>
  );
}

/** Whether `problem` is the warning that a reload would empty the cart. */
function isNotKeeping(problem: Said) {
  return wordsOf(problem) === say(NOT_KEEPING);
}

function idOrNull(value: string): number | null {
  return value === "" ? null : Number(value);
}

function Place({
  label,
  list,
  catalogue,
  value,
  onPick,
}: {
  label: string;
  list: RefObject<HTMLSelectElement | null>;
  catalogue: Catalogue;
  value: number | null;
  onPick: (locationId: number | null) => void;
}) {
  const places = indexOf(catalogue).placeList;
  return (
    <Field label={label}>
      {(id) => (
        <select
          id={id}
          ref={list}
          value={String(value ?? "")}
          onChange={(event) => onPick(idOrNull(event.currentTarget.value))}
        >
          <option value="">{say(PICK_A_PLACE)}</option>
          {places.map((place) => (
            <option key={place.id} value={place.id}>
              {place.name}
            </option>
          ))}
        </select>
      )}
    </Field>
  );
}
