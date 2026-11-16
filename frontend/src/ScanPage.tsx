import type { MessageDescriptor } from "@lingui/core";
import { msg } from "@lingui/core/macro";
import { type Signal, signal, useSignal } from "@preact/signals";
import { Fragment, type TargetedSubmitEvent } from "preact";
import { useEffect, useMemo, useRef } from "preact/hooks";
import { type Api, check, makeApi } from "./api/client";
import { Catalogue as CatalogueReply, DeviceRefusedReply } from "./api/replies";
import { MUST_UPGRADE, UPDATED, UPGRADE } from "./build";
import {
  addOne,
  askWho,
  type Batch,
  batchWords,
  STORAGE_KEY as CART_KEY,
  type Cart,
  CartStore,
  type Catalogue,
  freshCart,
  type Item,
  indexOf,
  type Kind,
  keysToAsk,
  type Line,
  loadCart,
  type Offered,
  offered,
  offeredAt,
  openingPackage,
  placed,
  quantityOf,
  RETIRED,
  resized,
  SIDES,
  type Side,
  sealedCounts,
  search,
  toBatch,
  unseen,
  withLine,
  withLineChanged,
} from "./cart";
import { Field } from "./Field";
import { say, sayParts } from "./i18n";
import { placeSides, readLabel, type Scanned } from "./labels";
import { NOTHING_TAKEN, OpenPackageCard } from "./OpenPackageCard";
import {
  asSave,
  type Gaps,
  linesSaid,
  nextWhole,
  type Older,
  OlderStore,
  type Outcome,
  olderId,
  toCart,
  withRetired,
} from "./older";
import {
  type Body,
  batchId,
  type Entry,
  MAKE_CHANGES,
  type Note,
  noteOf,
  STORAGE_KEY as OUTBOX_KEY,
  type Outbox,
  type Recorded,
  recordedHeading,
  type Said,
  sharedOutbox,
  wordsOf,
} from "./outbox";
import { decimal, thousandths } from "./quantity";
import {
  ADD_DIFFERENCE,
  changesShown,
  differenceOf,
  IN_CART,
  recordedLines,
} from "./recorded";
import { NOT_REGISTERED } from "./registration";
import { Scanner } from "./Scanner";
import { onStoredChange } from "./stored";
import {
  MOST_SECONDS_TO_SAVE,
  type Event as Reported,
  telemetry,
} from "./telemetry";

const API = makeApi();
/** How often an open page checks whether to ask whose name it is, and
 * whether its catalogue is stale. */
const CHECK_EVERY_MS = 60_000;
/** A page left open fetches the catalogue again once it is this old, so a
 * hub tablet sees new items, volunteers and settings. */
const CATALOGUE_FRESH_MS = 10 * 60_000;

type Loaded =
  | { state: "loading" }
  | { state: "ready"; catalogue: Catalogue }
  | { state: "unregistered"; reason: "unregistered" | "revoked" }
  | { state: "unavailable" };

/** The catalogue, asked which of the outbox's batches it shows
 * (cart.ts, unseen). */
async function loadCatalogue(api: Api, outbox: Outbox): Promise<Loaded> {
  const key = keysToAsk(outbox.entries.value);
  const reply = await check(
    api.GET("/api/catalogue", { params: { query: { key } } }),
    { 200: CatalogueReply, 403: DeviceRefusedReply },
  );
  switch (reply.status) {
    case 200:
      return { state: "ready", catalogue: reply.body };
    case 403:
      return { state: "unregistered", reason: reply.body.reason };
    default:
      return { state: "unavailable" };
  }
}

/** What a cart is filled for, in the words the page asks it in. */
export const KIND_WORDS: Record<Kind, MessageDescriptor> = {
  move: msg`Moving stock`,
  receipt: msg`Receiving stock`,
  install: msg`Taking it to an install`,
  open: msg`Opening packages`,
};

/** An older build's cart, filled for each kind (olderKind). */
const CART_KIND_WORDS: Record<Kind, MessageDescriptor> = {
  move: msg`A cart: moving stock`,
  receipt: msg`A cart: receiving stock`,
  install: msg`A cart: taking it to an install`,
  open: msg`A cart: opening packages`,
};

export const SCAN = msg`Scan`;
export const LOADING = msg`Loading the catalogue…`;
/** After why this browser cannot record (registration.NOT_REGISTERED). */
export const REGISTER_IT = msg`<0>Register it</0>, then come back.`;
export const COULD_NOT_LOAD = msg`Couldn't load the catalogue.`;
export const TRY_AGAIN = msg`Try again`;
export const NOT_KEEPING = msg`This browser isn't keeping the cart, so a reload will empty it. Free some space or leave private browsing.`;
/** What a label or a tap did, said under the camera. */
export const NO_PACKAGE_TO_OPEN = msg`{item} comes in no package to open.`;
export const WHICH_PACKAGE = msg`{item}: type how many of which package to open.`;
export const ADDED_ONE = msg`Added one: {item}.`;
export const ADDED_PACKAGE = msg`Added a {package}: {item}.`;
export const UNKNOWN_LABEL = msg`That label isn't in the inventory ({code}).`;
export const PLACE_HERE = msg`{place}: here.`;
export const PLACE_FROM_HERE = msg`{place}: from here.`;
export const PLACE_TO_HERE = msg`{place}: to here.`;
export const PLACE_WHICH = msg`{place}: from here, or to here?`;
export const CHANGED_BEFORE_RECORD = msg`The cart was changed in another tab. Check it, then record it again.`;
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
export const NOTHING_IN_CART = msg`Nothing in the cart yet.`;
export const SCAN_WITH_CAMERA = msg`Scan with the camera`;
export const SENDING_WHAT = msg`Sending: {what}. A new cart is ready.`;
export const RECORDING_AS = msg`Recording as {name}.`;
export const CHANGE = msg`Change`;
export const RECORD = msg`Record`;
export const DISCARD_CART = msg`Discard cart`;
export const CHECKED_OUT = msg`Checked out items. Add to cart or discard before sending`;
export const RETIRED_ITEM = msg`Item retired`;
export const DISCARD = msg`Discard`;
export const CANNOT_ADD = msg`Cannot Add to Cart`;
export const ADD_TO_CART = msg`Add to cart`;
export const CONFIRM_DISCARD = msg`Confirm discard`;
export const KEEP_IT = msg`Keep it`;
export const SAYING_WHAT_IS_ON = msg`Saying what is on open packages`;
export const SOMETHING = msg`Something`;
export const A_CART_SOMETHING = msg`A cart: something`;
/** Where an older build's save was for, clause by clause (olderWhere):
 * each clause is whole, so a translator words it alone, and they follow
 * in this order, joined by spaces, rather than as a message for each of
 * the 20 ways they combine. */
export const AT_PLACE = msg({
  comment:
    "A clause of where a save was for; clauses that apply follow in this order, joined by spaces: at or from, to, for install.",
  message: "at {place}",
});
export const FROM_PLACE = msg({
  comment:
    "A clause of where a save was for; clauses that apply follow in this order, joined by spaces: at or from, to, for install.",
  message: "from {place}",
});
export const TO_PLACE = msg({
  comment:
    "A clause of where a save was for; clauses that apply follow in this order, joined by spaces: at or from, to, for install.",
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
export const PICK_A_PLACE = msg`Pick a place`;
export const LINE_GONE = msg`No longer in the catalogue.`;
export const REMOVE = msg`Remove`;
export const RESIZED_TO = msg`The package size has changed to {size} since this item was added. Enter the count again.`;
export const PACKAGE_COUNT = msg`{package} ({size} {unit})`;
export const PACKAGE_COUNT_RETIRED = msg`{package} ({size} {unit}), retired: taking only`;
export const LOOSE_UNITS = msg`Loose units`;
export const LOOSE_IN = msg`Loose {unit}`;
export const HOW_MANY = msg`How many`;
export const HOW_MANY_IN = msg`How many {unit}`;
export const IN_ALL = msg`{amount} {unit} in all`;
export const FIND = msg`Find an item, or type or scan its code`;
export const NOTHING_MATCHES = msg`Nothing matches “{typed}”.`;
export const CARTS_FROM_DEVICE = msg`Carts from this device`;
export const REGISTER_TO_SEND_WAITING = msg`<0>Register this browser</0> to send what is waiting.`;
export const RECORDED_IN = msg`Recorded (transaction {transaction}).`;
export const SENDING = msg`Sending…`;
/** A save waiting while the page is too old to send it (Ali, 2026-11-16,
 * inventory-tng-n7yv): the banner says what to do. */
export const HELD = msg`Held until you upgrade.`;
export const DISMISS = msg`Dismiss`;

/** A list whose entries are ruled apart: the cart's lines, the outbox. */
const RULED_LIST = "list-none p-0 *:border-t *:border-line *:py-3";

/** A box that asks the volunteer to act, as the upgrade and the name check
 * do, or says a save is held for the upgrade. */
const CAUTION_BOX =
  "flex flex-col items-start gap-3 border-2 border-caution p-3";

/** The volunteer's page: fill a cart, then Record it, through the outbox. */
export function ScanPage({
  api = API,
  outbox = sharedOutbox(),
  reload,
}: {
  api?: Api;
  outbox?: Outbox;
  /** Refresh this page, to load a newer build (scan.tsx). */
  reload: () => void;
}) {
  const loaded = useSignal<Loaded>({ state: "loading" });
  // Whether the cart holds no lines: until it does, a newer build is not
  // loaded, as it would not read this build's cart (build.ts).
  const cartEmpty = useMemo(() => signal(storedCartEmpty()), []);
  // The cart's batch key, while the cart is shown (CartForm): a save's
  // difference put in it is said to be there until it is recorded.
  const cartKey = useMemo(() => signal<string | undefined>(undefined), []);
  // What older builds left, listed to put back in the cart or discard.
  const older = useMemo(() => new OlderStore(), []);
  useEffect(() => {
    // Once for saves, once for a cart: the saves are listed first.
    for (const held of new Set(older.listed.value.map(heldIn))) {
      telemetry.record({ kind: "older_storage", held, action: "found" });
    }
    return older.listen();
  }, []);
  const loadedAt = useRef(0);
  // What puts a save's difference in the cart, while the cart is shown
  // (CartForm), for the outbox's list below it.
  const adder = useRef<AddDifference | null>(null);
  // One refresh at a time; one asked for meanwhile follows it.
  const asking = useRef<"no" | "yes" | "again">("no");

  // The newest load asked for: an older one's answer, which may not show
  // a batch a newer one let the outbox forget, is never shown after it.
  const newest = useRef(0);

  /** Load it; again, keeping the one it has if that fails. */
  async function fetch(again: boolean) {
    const order = ++newest.current;
    const result = await loadCatalogue(api, outbox);
    if (order !== newest.current) {
      return;
    }
    if (!again || result.state === "ready") {
      loaded.value = result;
      loadedAt.current = Date.now();
    }
    if (result.state === "ready") {
      // Shown now: what it shows need not be kept (outbox.ts).
      outbox.forgetShown(result.catalogue.saved);
    }
  }

  function load() {
    void fetch(false);
  }

  useEffect(load, [api]);

  /** Load it again, one at a time. */
  function refresh() {
    if (asking.current !== "no") {
      asking.current = "again";
      return;
    }
    asking.current = "yes";
    void fetch(true).then(() => {
      const again = asking.current === "again";
      asking.current = "no";
      if (again) {
        refresh();
      }
    });
  }

  // A page left open refreshes a stale catalogue.
  useEffect(() => {
    const refreshStale = () => {
      const stale = Date.now() - loadedAt.current > CATALOGUE_FRESH_MS;
      if (
        document.visibilityState !== "visible" ||
        !stale ||
        loaded.value.state !== "ready"
      ) {
        return;
      }
      refresh();
    };
    const timer = setInterval(refreshStale, CHECK_EVERY_MS);
    document.addEventListener("visibilitychange", refreshStale);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshStale);
    };
  }, [api]);

  // The outbox's batches the catalogue does not show (cart.ts, unseen).
  const shown = loaded.value.state === "ready" ? loaded.value.catalogue : null;
  const entries = outbox.entries.value;
  const pending = useMemo(
    () => (shown === null ? [] : unseen(entries, shown)),
    [shown, entries],
  );
  // A save the server held back as from an older build too old, here or
  // in another tab, is listed with what older builds left (outbox.ts).
  const tooOld = entries
    .filter((each) => each.state === "too_old")
    .map((each) => each.body.key)
    .join();
  useEffect(() => older.refresh(), [tooOld]);
  // One saved changed stock: ask again, so what the page fills and offers
  // follows.
  const savedSince = pending
    .filter((each) => each.state === "saved")
    .map((each) => each.body.key)
    .join();
  useEffect(() => {
    if (savedSince !== "") {
      refresh();
    }
  }, [savedSince]);
  // One put back and refused as another tab recorded it first, here or in
  // another tab: ask again at once, so the stock shows what that tab
  // recorded (Ali, inventory-tng-g0e, d). Once for each; the first load
  // shows those refused before it.
  const recordedFirst = entries
    .filter((each) => each.reason?.code === "recorded_elsewhere")
    .map((each) => each.body.key);
  const refreshedFor = useRef(new Set(recordedFirst)).current;
  const ready = shown !== null;
  useEffect(() => {
    // Not while loading: a refresh would drop that load's answer.
    const fresh = recordedFirst.filter((key) => !refreshedFor.has(key));
    if (!ready || fresh.length === 0) {
      return;
    }
    for (const key of fresh) {
      refreshedFor.add(key);
    }
    refresh();
  }, [recordedFirst.join(), ready]);

  useEffect(() => {
    const stop = outbox.listen();
    const stopBuilds = outbox.builds.listen([OUTBOX_KEY, CART_KEY]);
    // Another tab's cart, while no CartForm keeps cartEmpty.
    const stopCart = onStoredChange([CART_KEY], () => {
      if (loaded.value.state !== "ready") {
        cartEmpty.value = storedCartEmpty();
      }
    });
    void outbox.send();
    return () => {
      stop();
      stopBuilds();
      stopCart();
    };
  }, [outbox]);

  const current = loaded.value;
  return (
    <main class="volunteer page-width">
      <h1>{say(SCAN)}</h1>
      <NewerBuild outbox={outbox} cartEmpty={cartEmpty.value} reload={reload} />
      {current.state === "loading" && <p>{say(LOADING)}</p>}
      {current.state === "unregistered" && (
        <p role="alert">
          <strong>
            {say(NOT_REGISTERED[current.reason])}{" "}
            {sayParts(REGISTER_IT, {}, [
              (inside) => <a href="/register">{inside}</a>,
            ])}
          </strong>
        </p>
      )}
      {current.state === "unavailable" && (
        <p role="alert">
          <strong>{say(COULD_NOT_LOAD)}</strong>{" "}
          <button type="button" onClick={load}>
            {say(TRY_AGAIN)}
          </button>
        </p>
      )}
      {current.state === "ready" && (
        <CartForm
          catalogue={current.catalogue}
          pending={pending}
          outbox={outbox}
          cartEmpty={cartEmpty}
          cartKey={cartKey}
          older={older}
          adder={adder}
        />
      )}
      <OutboxList
        outbox={outbox}
        catalogue={shown}
        cartKey={cartKey.value}
        onAddDifference={(entry, difference) =>
          adder.current?.(entry, difference) ?? null
        }
      />
    </main>
  );
}

/**
 * Asks for a reload once a newer build is out (build.ts). While this tab
 * can still save, only once the cart is saved or emptied and nothing waits
 * to send, as the newer build reads neither; until then it says nothing.
 * Too old, at once, saying what this tab holds is kept: the newer build
 * reads this build's cart, or lists it, and lists its saves waiting
 * (older.ts). A newer build's saves waiting here are not counted: that
 * build sends them.
 */
function NewerBuild({
  outbox,
  cartEmpty,
  reload,
}: {
  outbox: Outbox;
  cartEmpty: boolean;
  reload: () => void;
}) {
  const learned = outbox.builds.newer.value;
  // Whether this tab holds what a newer build reads not as this build's:
  // a cart, or a batch waiting; too old, a newer build's own not counted.
  const holds =
    !cartEmpty ||
    outbox.entries.value.some(
      (entry) =>
        entry.state === "waiting" &&
        (learned !== "too_old" || !outbox.builds.isNewer(entry.build)),
    );
  if (learned === null || (learned !== "too_old" && holds)) {
    return null;
  }
  return (
    <div role="alert" class={CAUTION_BOX}>
      <div>{say(holds ? MUST_UPGRADE : UPDATED)}</div>
      <button type="button" class="primary" onClick={reload}>
        {say(UPGRADE)}
      </button>
    </div>
  );
}

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

/** Whether the stored cart holds no lines. */
function storedCartEmpty(): boolean {
  return loadCart().lines.length === 0;
}

function CartForm({
  catalogue,
  pending,
  outbox,
  cartEmpty,
  cartKey,
  older,
  adder,
}: {
  catalogue: Catalogue;
  /** The outbox's batches it does not show (cart.ts, unseen). */
  pending: readonly Entry[];
  outbox: Outbox;
  /** Kept as the cart changes, here or in another tab (NewerBuild). */
  cartEmpty: Signal<boolean>;
  /** Kept as the cart's batch key changes. */
  cartKey: Signal<string | undefined>;
  /** What older builds left, to put back in the cart or discard. */
  older: OlderStore;
  /** Given what puts a save's difference in this cart (addDifference). */
  adder: { current: AddDifference | null };
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
  const query = useSignal("");
  const problems = useSignal<Said[]>([]);
  // What was last sent to be recorded, worded as it is shown.
  const saved = useSignal<Batch | null>(null);
  // While Discard cart waits to be confirmed, the cart's revision then.
  const discarding = useSignal<string | null>(null);
  // An older save just put back in the cart, and what it left to do again.
  const putBack = useSignal<Gaps | null>(null);
  // After a while unused, the phone may have changed hands: offer to
  // change the name, without stopping anyone saving.
  const checkName = useSignal(askWho(cart.value, catalogue));
  const nameList = useRef<HTMLSelectElement>(null);

  // Tabs share one cart; a refreshed catalogue clears what it no longer has.
  useEffect(() => store.listen(), [store]);
  useEffect(() => store.useCatalogue(catalogue), [catalogue]);
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
  useEffect(() => {
    if (catalogue.settings.name_check_minutes === 0) {
      return;
    }
    const maybeAsk = () => {
      if (
        document.visibilityState === "visible" &&
        askWho(cart.value, catalogue)
      ) {
        checkName.value = true;
      }
    };
    const timer = setInterval(maybeAsk, CHECK_EVERY_MS);
    document.addEventListener("visibilitychange", maybeAsk);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", maybeAsk);
    };
  }, [catalogue]);

  /** Say so if the browser would not keep the cart. */
  function kept(stored: boolean) {
    problems.value = stored ? [] : [[say(NOT_KEEPING)]];
  }

  function change(next: Cart) {
    saved.value = null;
    putBack.value = null;
    discarding.value = null;
    kept(store.change(next));
  }

  function set(fields: Partial<Cart>) {
    change(placed(cart.value, { ...cart.value, ...fields }, catalogue, sealed));
  }

  /** Add one, and say what: opening, an item is the package it opens
   * (openingPackage), as only whole packages are opened; with no such
   * package, its line, for the volunteer to fill. A package's own label
   * is the volunteer's choice, kept as typed. */
  function add(itemId: number, packageId?: number): string {
    const item = indexOf(catalogue).items.get(itemId);
    const opening = packageId === undefined && cart.value.kind === "open";
    const pack = opening
      ? openingPackage(item, cart.value.fromId, sealed)
      : item?.packages.find((each) => each.id === packageId);
    query.value = "";
    if (opening && pack === undefined) {
      if (item?.packages.length === 0) {
        return say(NO_PACKAGE_TO_OPEN, { item: item.name });
      }
      change(withLine(cart.value, itemId));
      return say(WHICH_PACKAGE, { item: item?.name });
    }
    const next = addOne(cart.value, itemId, pack?.id);
    // A package's own label is the volunteer's choice (Line.typed).
    change(
      packageId === undefined
        ? next
        : withLineChanged(next, itemId, (line) => ({ ...line, typed: true })),
    );
    return pack === undefined
      ? say(ADDED_ONE, { item: item?.name })
      : say(ADDED_PACKAGE, { package: pack.name, item: item?.name });
  }

  // The camera, while open, and a place it read, asking From or To.
  const scanning = useSignal(false);
  const placeRead = useSignal<number | null>(null);

  /** Acts on a label, read by the camera or typed by a scanner gun. */
  function actOn(read: Scanned): string {
    if (read.kind === "unknown") {
      // A stray code leaves a place's question to answer.
      return say(UNKNOWN_LABEL, { code: read.code });
    }
    placeRead.value = null;
    const { places } = indexOf(catalogue);
    if (read.kind === "place") {
      const place = places.get(read.locationId);
      const [only, other] = placeSides(cart.value);
      query.value = "";
      if (only !== undefined && other === undefined) {
        placeOn(only, read.locationId);
        const here = cart.value.kind === "open" ? PLACE_HERE : PLACE_FROM_HERE;
        return say(only === "from" ? here : PLACE_TO_HERE, { place });
      }
      placeRead.value = read.locationId;
      return say(PLACE_WHICH, { place });
    }
    const added = add(read.itemId, read.packageId);
    // A buzz where the phone has one (not iOS Safari).
    navigator.vibrate?.(60);
    return added;
  }

  function placeOn(side: Side, locationId: number) {
    set(side === "from" ? { fromId: locationId } : { toId: locationId });
    placeRead.value = null;
  }

  function save(event: TargetedSubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    // Another tab may have changed or saved this cart: saving what this tab
    // shows could record it twice, so it shows that cart instead.
    const current = store.forSaving();
    if (current === null) {
      problems.value = [[say(CHANGED_BEFORE_RECORD)]];
      return;
    }
    const batch = toBatch(current, catalogue, opens);
    if ("problems" in batch) {
      problems.value = batch.problems;
      return;
    }
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
      return;
    }
    if (current.startedAt > 0) {
      // A cart kept overnight counts as a day, and a clock put back as none.
      const seconds = Math.min(
        Math.max((Date.now() - current.startedAt) / 1000, 0),
        MOST_SECONDS_TO_SAVE,
      );
      telemetry.record({ kind: "scan_to_save", seconds });
    }
    change(freshCart(current));
    saved.value = batch;
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
      return;
    }
    change(freshCart(current));
    telemetry.record({ kind: "cart_discarded" });
  }

  /** The cart as stored, to fill if empty: another tab may have filled
   * it, unheard yet, which filling it would write over. */
  function storedCart(): Cart {
    return store.forSaving() ?? cart.value;
  }

  /** Fill this cart, as stored, which must be empty, with `save`, once
   * `ready` says it still may (else why not), and `forget` it where it
   * was (false if that could not be): putInCart's and addDifference's
   * steps. True once filled, else why not. */
  function fill(
    save: Older,
    automatically: boolean,
    forget: () => boolean,
    ready: () => NotPut | null | true = () => true,
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
    putBack.value = gaps;
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

  /** Put what a save recorded elsewhere adds (recorded.ts, differenceOf)
   * in this cart, which must be empty, under the difference's own key,
   * and mark the save so, offering it no more: by a tap, or `automatically`
   * into an empty cart. Why not, if it was not. */
  function addDifference(
    entry: Entry,
    difference: Body,
    automatically = false,
  ): NotPut | null {
    const why = fill(asSave(difference), automatically, () =>
      outbox.differenceAdded(entry),
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

  function changeName() {
    checkName.value = false;
    nameList.current?.focus();
  }
  // Keyed by what each says, two saying the same apart.
  const problemKeys = keyedByWords(problems.value.map(wordsOf));

  return (
    <form onSubmit={save}>
      <OlderList older={older} catalogue={catalogue} onPutBack={putInCart} />
      {checkName.value && name !== undefined && (
        <div role="alert" class={CAUTION_BOX}>
          <div>{sayParts(ARE_YOU, { name: <strong>{name}</strong> })}</div>
          <div class="actions">
            <button
              type="button"
              onClick={() => {
                checkName.value = false;
                // Only the time changes, so "Sending" stays on show.
                kept(store.touch());
              }}
            >
              {say(THATS_ME)}
            </button>
            <button
              type="button"
              onClick={() => {
                set({ volunteerId: null });
                changeName();
              }}
            >
              {say(NOT_ME)}
            </button>
          </div>
        </div>
      )}
      <Field label={say(YOUR_NAME)}>
        {(id) => (
          <select
            id={id}
            ref={nameList}
            value={String(cart.value.volunteerId ?? "")}
            onChange={(event) =>
              set({ volunteerId: idOrNull(event.currentTarget.value) })
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
          catalogue={catalogue}
          value={cart.value.fromId}
          onPick={(fromId) => set({ fromId })}
        />
      )}
      {sides.to && (
        <Place
          label={say(TO)}
          catalogue={catalogue}
          value={cart.value.toId}
          onPick={(toId) => set({ toId })}
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

      <h2>{say(CART)}</h2>
      {putBack.value !== null && (
        <div role="status">
          <p>{say(ADDED_BACK)}</p>
          {putBack.value.length > 0 && (
            <ul>
              {putBack.value.map((gap) => (
                <li key={gap}>{gap}</li>
              ))}
            </ul>
          )}
        </div>
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
                  sides.from && cart.value.kind !== "open"
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
                onRemove={() =>
                  set({
                    lines: cart.value.lines.filter(
                      (each) => each.itemId !== line.itemId,
                    ),
                  })
                }
              />
            );
          })}
        </ul>
      )}

      {scanning.value ? (
        <Scanner
          onCode={(code) => actOn(readLabel(catalogue, code))}
          onDone={() => {
            scanning.value = false;
          }}
        />
      ) : (
        <button type="button" onClick={() => (scanning.value = true)}>
          {say(SCAN_WITH_CAMERA)}
        </button>
      )}
      {placeRead.value !== null && (
        <PlaceSides
          place={placeRead.value}
          name={indexOf(catalogue).places.get(placeRead.value) ?? ""}
          sides={placeSides(cart.value)}
          onPick={placeOn}
        />
      )}

      <Finder catalogue={catalogue} query={query} onAdd={add} onLabel={actOn} />

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
          <button type="button" onClick={changeName}>
            {say(CHANGE)}
          </button>
        </p>
      )}
      <button type="submit" class="primary w-full text-xl">
        {say(RECORD)}
      </button>
      {!empty &&
        (discarding.value !== null ? (
          <ConfirmDiscard
            onDiscard={discard}
            onKeep={() => {
              discarding.value = null;
            }}
          />
        ) : (
          <button
            type="button"
            class="self-start"
            onClick={() => {
              discarding.value = cart.value.revision;
            }}
          >
            {say(DISCARD_CART)}
          </button>
        ))}
    </form>
  );
}

/**
 * What older builds left and the page did not put back in the cart itself,
 * each in words, to add to the cart or discard (older.ts). Discarding asks
 * first, here on the page. A line that cannot go back says why under it,
 * and can be discarded on its own, without asking: focus then goes to its
 * entry's heading, and taps on the entry's buttons wait a moment
 * (SETTLE_MS). One holding an item
 * retired since, and out of the catalogue, names it from the catalogue's
 * retired items, and goes in the cart only once that line is discarded.
 */
function OlderList({
  older,
  catalogue,
  onPutBack,
}: {
  older: OlderStore;
  catalogue: Catalogue;
  /** Put one back in the cart: why not, if it was not. */
  onPutBack: (each: Older) => NotPut | null;
}) {
  // The one whose discarding is being confirmed, and what one tap said.
  const discarding = useSignal<string | null>(null);
  const said = useSignal<{ on: string; why: OlderWhy } | null>(null);
  // When a line of each was last discarded, and the one whose heading is
  // to take focus once it is listed again.
  const lineGoneAt = useRef(new Map<string, number>());
  const refocus = useRef<string | null>(null);
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
  // After a line's Discard, focus goes to its entry's heading, not to the
  // page; if the entry went with it, where it goes after an entry's Discard.
  useEffect(() => {
    const on = refocus.current;
    refocus.current = null;
    const n = shown.findIndex(({ each }) => olderId(each) === on);
    if (n >= 0) {
      document.getElementById(`older-${n}`)?.focus();
    }
  }, [shown]);
  if (shown.length === 0) {
    return null;
  }
  /** A tap on one of `on`'s buttons, ignored just after a line of it was
   * discarded: the second tap of a double tap would land on what moved up
   * in its place (Ali, inventory-tng-0su). */
  const unlessSettling = (on: string, tap: () => void) => () => {
    if (
      performance.now() - (lineGoneAt.current.get(on) ?? -Infinity) >=
      SETTLE_MS
    ) {
      tap();
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
    <section>
      <h2>{say(CHECKED_OUT)}</h2>
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
                                refocus.current = on;
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

/**
 * Why something cannot go back, in the alert's style. Not role="alert": it
 * is there as the list is shown, and several at once, read out together,
 * would drown each other and the page.
 */
function NoteSaid({
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
function ConfirmDiscard({
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
function AlertWords({ note: { bold, plain = [] } }: { note: Note }) {
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
function Counted({ said }: { said: Said }) {
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
function keyedByWords(says: readonly string[]): string[] {
  const seen = new Map<string, number>();
  return says.map((words) => {
    const nth = (seen.get(words) ?? 0) + 1;
    seen.set(words, nth);
    return `${words} ${nth}`;
  });
}

/** Why one cannot go in the cart: it is not empty (row 17). */
export const CART_NOT_EMPTY = msg`Record or discard the current cart before adding more items`;
/** The cart one went in was not kept (row 21). */
export const NOT_KEPT = msg`This browser can't keep the cart. Clear local data, reload the page and try again.`;
/** One went in the cart, but stays listed (row 22). */
export const NOT_REMOVED = msg`This item wasn't automatically removed. Record the cart and try to remove.`;
/** Discard cart, confirmed after another tab changed the cart. */
export const CHANGED_BEFORE_DISCARD = msg`The cart was changed in another tab. Check it, then discard it again.`;
/** The browser would not keep a discard. */
export const NOT_DISCARDED = msg`Unable to discard. Clear your site data to remove.`;

/** How long after a line's Discard taps on its entry's buttons are
 * ignored, in milliseconds. */
const SETTLE_MS = 500;

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
type NotPut = "cart_not_empty" | "changed" | "not_kept";

/** Puts what a save recorded elsewhere adds in the cart (CartForm,
 * addDifference): why not, if it was not. */
type AddDifference = (entry: Entry, difference: Body) => NotPut | null;

/** What the older list says under one: why it was not put in the cart, or
 * not discarded (changed meanwhile, or not kept). */
type OlderWhy = NotPut | "discard_changed" | "discard_not_kept";

/** `why` in words; one changed, as it is now, that `offersAdd` (Add to
 * cart, not Cannot Add to Cart) or, if not, only to check it (Ali,
 * inventory-tng-8gj). */
function olderSaid(why: OlderWhy, offersAdd: boolean): string {
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
function heldIn(each: Older): "saves" | "cart" {
  return each.batch === null ? "cart" : "saves";
}

/** Tells telemetry what the volunteer did with `each`. */
function recordOlder(
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

/** From or To, for a place a label named: the side still empty first. */
function PlaceSides({
  place,
  name,
  sides,
  onPick,
}: {
  place: number;
  name: string;
  sides: Side[];
  onPick: (side: Side, place: number) => void;
}) {
  return (
    <fieldset>
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

function idOrNull(value: string): number | null {
  return value === "" ? null : Number(value);
}

function Place({
  label,
  catalogue,
  value,
  onPick,
}: {
  label: string;
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

function CartLine({
  line,
  item,
  opens,
  moving,
  onChange,
  onRemove,
}: {
  line: Line;
  item: Item | undefined;
  /** Its open packages where it comes from, to take from. */
  opens: Offered[];
  /** Whether an open package can move whole. */
  moving: boolean;
  onChange: (line: Line) => void;
  onRemove: () => void;
}) {
  if (item === undefined) {
    return (
      <li>
        {say(LINE_GONE)}{" "}
        <button type="button" onClick={onRemove}>
          {say(REMOVE)}
        </button>
      </li>
    );
  }
  const quantity = quantityOf(line, item);
  return (
    <li class="flex flex-col gap-2">
      <div>
        <strong>{item.name}</strong>
        {item.retired && <small> {say(RETIRED)}</small>}
      </div>
      {opens.map((open) => (
        <OpenPackageCard
          key={open.key}
          open={open}
          item={item}
          moving={moving}
          taken={line.open?.[open.key] ?? NOTHING_TAKEN}
          onTake={(next) =>
            onChange({ ...line, open: { ...line.open, [open.key]: next } })
          }
        />
      ))}
      {item.packages.map((pack) => {
        // The catalogue gives every size as a decimal, so never null.
        /* v8 ignore next */
        const size = decimal(thousandths(pack.size) ?? 0);
        return (
          <Fragment key={pack.id}>
            {resized(line, pack) && (
              <p role="alert">
                <AlertWords
                  note={{
                    bold: [say(RESIZED_TO, { size })],
                  }}
                />
              </p>
            )}
            <Field
              quiet
              label={say(pack.retired ? PACKAGE_COUNT_RETIRED : PACKAGE_COUNT, {
                package: pack.name,
                size,
                unit: item.unit,
              })}
            >
              {(id) => (
                <input
                  id={id}
                  inputMode="numeric"
                  value={line.packages[pack.id] ?? ""}
                  onInput={(event) =>
                    onChange({
                      ...line,
                      packages: {
                        ...line.packages,
                        [pack.id]: event.currentTarget.value,
                      },
                    })
                  }
                />
              )}
            </Field>
          </Fragment>
        );
      })}
      <Field
        quiet
        label={
          item.unit === "each"
            ? say(item.packages.length > 0 ? LOOSE_UNITS : HOW_MANY)
            : say(item.packages.length > 0 ? LOOSE_IN : HOW_MANY_IN, {
                unit: item.unit,
              })
        }
      >
        {(id) => (
          <input
            id={id}
            inputMode={item.unit === "each" ? "numeric" : "decimal"}
            value={line.loose}
            onInput={(event) =>
              onChange({ ...line, loose: event.currentTarget.value })
            }
          />
        )}
      </Field>
      <div>
        {"problem" in quantity
          ? wordsOf(quantity.problem)
          : say(IN_ALL, { amount: decimal(quantity.amount), unit: item.unit })}
      </div>
      <button type="button" class="self-start" onClick={onRemove}>
        {say(REMOVE)}
      </button>
    </li>
  );
}

function Finder({
  catalogue,
  query,
  onAdd,
  onLabel,
}: {
  catalogue: Catalogue;
  query: Signal<string>;
  onAdd: (itemId: number, packageId?: number) => void;
  /** A label's code typed, as a scanner gun types one. */
  onLabel: (read: Scanned) => void;
}) {
  const matches = search(catalogue, query.value);
  return (
    <>
      <Field label={say(FIND)}>
        {(id) => (
          <input
            id={id}
            type="search"
            autoComplete="off"
            autoCapitalize="none"
            spellcheck={false}
            value={query.value}
            onInput={(event) => {
              query.value = event.currentTarget.value;
            }}
            onKeyDown={(event) => {
              // A scanner gun types the code, then Enter.
              if (event.key !== "Enter") {
                return;
              }
              event.preventDefault();
              const read = readLabel(catalogue, query.value);
              if (read.kind !== "unknown") {
                onLabel(read);
              } else if (matches.length === 1 && matches[0] !== undefined) {
                onAdd(matches[0].id);
              }
            }}
          />
        )}
      </Field>
      {query.value.trim() !== "" && matches.length === 0 && (
        <p>{say(NOTHING_MATCHES, { typed: query.value.trim() })}</p>
      )}
      {matches.length > 0 && (
        <ul class="list-none p-0 *:py-1">
          {matches.map((item) => (
            <li key={item.id}>
              <button type="button" onClick={() => onAdd(item.id)}>
                {item.name}
              </button>{" "}
              <small>
                {[
                  item.retired ? say(RETIRED) : null,
                  item.category,
                  item.manufacturer,
                  item.part_number,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </small>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/**
 * What this browser sent, or is sending, and what became of each. A
 * refusal or failure says so in the alert's style, and a refused batch
 * lists its lines, to compare with what was recorded. One sent under its
 * cart's key and refused as another tab recorded it first shows what that
 * tab recorded and what differs (RecordedElsewhere), or, from a server
 * that does not say, what is left to do (SCAN_AGAIN_CHANGED), as the older
 * list does, while the page refreshes the stock (Ali, inventory-tng-g0e,
 * d; inventory-tng-0gg).
 */
function OutboxList({
  outbox,
  catalogue,
  cartKey,
  onAddDifference,
}: {
  outbox: Outbox;
  /** To name the lines of one refused; null until it is loaded. */
  catalogue: Catalogue | null;
  /** The cart's batch key, if it has one. */
  cartKey: string | undefined;
  onAddDifference: AddDifference;
}) {
  const online = useOnline();
  // Under which save (batchId), why its difference did not go in the cart.
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
              {notAdded.value?.on === id && (
                <p role="alert">
                  <AlertWords
                    note={{ bold: [olderSaid(notAdded.value.why, true)] }}
                  />
                </p>
              )}
              {entry.state !== "waiting" && (
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
      {difference !== null && added !== true && (
        <button type="button" onClick={() => onAdd(difference)}>
          {say(ADD_DIFFERENCE)}
        </button>
      )}
      {difference !== null && added === true && cartKey === difference.key && (
        <p>{say(IN_CART)}</p>
      )}
    </>
  );
}
