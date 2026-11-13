import { type Signal, signal, useSignal } from "@preact/signals";
import { Fragment, type TargetedSubmitEvent } from "preact";
import { useEffect, useMemo, useRef } from "preact/hooks";
import { type Api, check, makeApi } from "./api/client";
import { Catalogue as CatalogueReply, DeviceRefusedReply } from "./api/replies";
import { MUST_UPGRADE, UPDATED, UPGRADE } from "./build";
import {
  addOne,
  askWho,
  STORAGE_KEY as CART_KEY,
  type Cart,
  CartStore,
  type Catalogue,
  freshCart,
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
import { placeSides, readLabel, type Scanned } from "./labels";
import { NOTHING_TAKEN, OpenPackageCard } from "./OpenPackageCard";
import {
  type Gaps,
  type Note,
  nextWhole,
  type Older,
  OlderStore,
  olderId,
  toCart,
  withRetired,
} from "./older";
import {
  type Entry,
  STORAGE_KEY as OUTBOX_KEY,
  type Outbox,
  sharedOutbox,
} from "./outbox";
import { decimal, thousandths } from "./quantity";
import { NOT_REGISTERED } from "./registration";
import { Scanner } from "./Scanner";
import { onStoredChange } from "./stored";
import { MOST_SECONDS_TO_SAVE, telemetry } from "./telemetry";

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
const KIND_WORDS: Record<Kind, string> = {
  move: "Moving stock",
  receipt: "Receiving stock",
  install: "Taking it to an install",
  open: "Opening packages",
};

/** A list whose entries are ruled apart: the cart's lines, the outbox. */
const RULED_LIST = "list-none p-0 *:border-t *:border-line *:py-3";

/** The volunteer's page: fill a cart, then Save it to the outbox. */
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
      <h1>Scan</h1>
      <NewerBuild outbox={outbox} cartEmpty={cartEmpty.value} reload={reload} />
      {current.state === "loading" && <p>Loading the catalogue…</p>}
      {current.state === "unregistered" && (
        <p role="alert">
          {NOT_REGISTERED[current.reason]} <a href="/register">Register it</a>,
          then come back.
        </p>
      )}
      {current.state === "unavailable" && (
        <p role="alert">
          Couldn't load the catalogue.{" "}
          <button type="button" onClick={load}>
            Try again
          </button>
        </p>
      )}
      {current.state === "ready" && (
        <CartForm
          catalogue={current.catalogue}
          pending={pending}
          outbox={outbox}
          cartEmpty={cartEmpty}
          older={older}
        />
      )}
      <OutboxList outbox={outbox} />
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
  if (learned === null) {
    return null;
  }
  const waiting = outbox.entries.value.filter(
    (entry) =>
      entry.state === "waiting" &&
      (learned !== "too_old" || !outbox.builds.isNewer(entry.build)),
  );
  const holds = waiting.length > 0 || !cartEmpty;
  if (learned !== "too_old" && holds) {
    return null;
  }
  return (
    <div
      role="alert"
      class="flex flex-col items-start gap-3 border-2 border-caution p-3"
    >
      <div>{holds ? MUST_UPGRADE : UPDATED}</div>
      <button type="button" class="primary" onClick={reload}>
        {UPGRADE}
      </button>
    </div>
  );
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
  older,
}: {
  catalogue: Catalogue;
  /** The outbox's batches it does not show (cart.ts, unseen). */
  pending: readonly Entry[];
  outbox: Outbox;
  /** Kept as the cart changes, here or in another tab (NewerBuild). */
  cartEmpty: Signal<boolean>;
  /** What older builds left, to put back in the cart or discard. */
  older: OlderStore;
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
  const problems = useSignal<string[]>([]);
  const saved = useSignal<string | null>(null);
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
  useEffect(() => {
    cartEmpty.value = empty;
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
    problems.value = stored
      ? []
      : [
          "This browser isn't keeping the cart, so a reload will empty it. Free some space or leave private browsing.",
        ];
  }

  function change(next: Cart) {
    saved.value = null;
    putBack.value = null;
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
        return `${item.name} comes in no package to open.`;
      }
      change(withLine(cart.value, itemId));
      return `${item?.name}: type how many of which package to open.`;
    }
    const next = addOne(cart.value, itemId, pack?.id);
    // A package's own label is the volunteer's choice (Line.typed).
    change(
      packageId === undefined
        ? next
        : withLineChanged(next, itemId, (line) => ({ ...line, typed: true })),
    );
    return `Added ${pack === undefined ? "one" : `a ${pack.name}`}: ${item?.name}.`;
  }

  // The camera, while open, and a place it read, asking From or To.
  const scanning = useSignal(false);
  const placeRead = useSignal<number | null>(null);

  /** Acts on a label, read by the camera or typed by a scanner gun. */
  function actOn(read: Scanned): string {
    if (read.kind === "unknown") {
      // A stray code leaves a place's question to answer.
      return `That label isn't in the inventory (${read.code}).`;
    }
    placeRead.value = null;
    const { places } = indexOf(catalogue);
    if (read.kind === "place") {
      const place = places.get(read.locationId);
      const [only, other] = placeSides(cart.value);
      query.value = "";
      if (only !== undefined && other === undefined) {
        placeOn(only, read.locationId);
        const here = cart.value.kind === "open" ? "here" : "from here";
        return `${place}: ${only === "from" ? here : "to here"}.`;
      }
      placeRead.value = read.locationId;
      return `${place}: from here, or to here?`;
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
      problems.value = [
        "The cart was changed in another tab. Check it, then save again.",
      ];
      return;
    }
    const batch = toBatch(current, catalogue, opens);
    if ("problems" in batch) {
      problems.value = batch.problems;
      return;
    }
    // A save an older build left, put back: sent under its key.
    if (!outbox.queue(batch.draft, batch.what, current.batchKey)) {
      // Nothing was sent, and the cart stays to try again.
      problems.value = [
        "This browser wouldn't keep the batch, so it isn't saved. Free some space or leave private browsing, then try again.",
      ];
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
    saved.value = batch.what;
  }

  /** Put an older build's save, or cart, in this cart, which must be
   * empty, and forget it there: moved, never copied, so it is saved once.
   * Why not, if it was not. */
  function putInCart(each: Older, automatically = false): string | null {
    if (cart.value.lines.length > 0) {
      return "Save previous cart before adding additional items";
    }
    const { cart: filled, gaps } = toCart(each, catalogue, cart.value);
    // Read again first: an older tab may have sent it meanwhile.
    if (!older.isListed(each)) {
      older.refresh();
      return GONE;
    }
    const before = cart.value;
    if (!store.change(filled)) {
      // Not kept, a reload would empty it while the older one stays
      // listed, to be put back and saved twice: the cart stays as it was.
      cart.value = before;
      return "Sorry. Can't save the cart. Clear local data, reload the page and try again.";
    }
    kept(true);
    if (!automatically) {
      // Put in after a save, without a tap, it leaves that save said.
      saved.value = null;
    }
    if (!older.remove(each)) {
      problems.value = [
        "Sorry. This item wasn't automatically removed. Save the cart and try to remove.",
      ];
    }
    telemetry.record({
      kind: "older_storage",
      held: heldIn(each),
      action: automatically ? "put_in_cart_automatically" : "put_in_cart",
    });
    putBack.value = gaps;
    return null;
  }

  // An empty cart takes what an older build left that goes back whole,
  // without a tap: the oldest first, one at a time. Each is tried once a
  // load, so one the browser would not keep is not tried for ever.
  const triedAutomatically = useRef(new Set<string>());
  const listed = older.listed.value;
  useEffect(() => {
    if (!empty) {
      return;
    }
    const next = nextWhole(listed, catalogue, triedAutomatically.current);
    if (next === undefined) {
      return;
    }
    triedAutomatically.current.add(olderId(next));
    const why = putInCart(next, true);
    if (why !== null && why !== GONE) {
      problems.value = [why];
    }
  }, [empty, listed, catalogue]);

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

  return (
    <form onSubmit={save}>
      <OlderList older={older} catalogue={catalogue} onPutBack={putInCart} />
      {checkName.value && name !== undefined && (
        <div
          role="alert"
          class="flex flex-col items-start gap-3 border-2 border-caution p-3"
        >
          <div>
            Are you <strong>{name}</strong>? This phone hasn't been used for a
            while.
          </div>
          <div class="actions">
            <button
              type="button"
              onClick={() => {
                checkName.value = false;
                // Only the time changes, so "Saved" stays on show.
                kept(store.touch());
              }}
            >
              That's me
            </button>
            <button
              type="button"
              onClick={() => {
                set({ volunteerId: null });
                changeName();
              }}
            >
              Not me
            </button>
          </div>
        </div>
      )}
      <Field label="Your name">
        {(id) => (
          <select
            id={id}
            ref={nameList}
            value={String(cart.value.volunteerId ?? "")}
            onChange={(event) =>
              set({ volunteerId: idOrNull(event.currentTarget.value) })
            }
          >
            <option value="">Pick your name</option>
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
        <legend>What is happening</legend>
        {(Object.entries(KIND_WORDS) as [Kind, string][]).map(
          ([kind, words]) => (
            <label key={kind} class="flex items-center gap-2 py-1 font-normal">
              <input
                type="radio"
                name="kind"
                value={kind}
                checked={cart.value.kind === kind}
                onChange={() => set({ kind })}
              />
              {words}
            </label>
          ),
        )}
      </fieldset>

      {sides.from && (
        <Place
          label={cart.value.kind === "open" ? "Where" : "From"}
          catalogue={catalogue}
          value={cart.value.fromId}
          onPick={(fromId) => set({ fromId })}
        />
      )}
      {sides.to && (
        <Place
          label="To"
          catalogue={catalogue}
          value={cart.value.toId}
          onPick={(toId) => set({ toId })}
        />
      )}
      {cart.value.kind === "install" && (
        <Field label="MeshDB install number (if you know it)">
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

      <h2>Cart</h2>
      {putBack.value !== null && (
        <div role="status">
          <p>Added back to cart. Review before Save.</p>
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
        <p>Nothing in the cart yet.</p>
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
          Scan with the camera
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
          {problems.value.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {saved.value !== null && (
        <p role="status">Saved: {saved.value}. A new cart is ready.</p>
      )}
      {name !== undefined && (
        <p>
          Saving as <strong>{name}</strong>.{" "}
          <button type="button" onClick={changeName}>
            Change
          </button>
        </p>
      )}
      <button type="submit" class="primary w-full text-xl">
        Save
      </button>
    </form>
  );
}

/**
 * What older builds left and the page did not put back in the cart itself,
 * each in words, to add to the cart or discard (older.ts). Discarding asks
 * first, here on the page. A line that cannot go back says why under it,
 * and can be discarded on its own, without asking. One holding an item
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
  onPutBack: (each: Older) => string | null;
}) {
  // The one whose discarding is being confirmed, and what one tap said.
  const discarding = useSignal<string | null>(null);
  const said = useSignal<{ on: string; words: string } | null>(null);
  const listed = older.listed.value;
  // Each in words once, not on every change to the cart beside it.
  const shown = useMemo(() => {
    const offered = indexOf(catalogue).items;
    return listed.map((each) => {
      const { cart, lost, parts } = toCart(each, withRetired(catalogue));
      const lines = parts.map((part) => ({
        ...part,
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
        fills: cart.lines.length > 0,
        retired: lines.some((line) => line.retired),
      };
    });
  }, [listed, catalogue]);
  if (shown.length === 0) {
    return null;
  }
  /** Tell the volunteer, under `each`, that it, or a line of it, was not
   * discarded, unless it changed meanwhile (`still`), which listing it
   * again shows. */
  const notDiscarded = (still: boolean, on: string) => {
    if (still) {
      said.value = {
        on,
        words: "Sorry. Unable to discard. Clear your site data to remove.",
      };
    }
  };
  return (
    <section>
      <h2>Checked out items. Add to cart or discard before sending</h2>
      <ul class={RULED_LIST}>
        {shown.map(({ each, lost, lines, fills, retired }, n) => {
          const on = olderId(each);
          const title = `older-${n}`;
          return (
            <li key={on} class="flex flex-col gap-2">
              <div id={title}>
                <strong>{olderKind(each)}</strong>
                {olderWhere(each, catalogue)}
              </div>
              <small>{olderWhen(each)}</small>
              {lost !== null && <NoteSaid note={lost} />}
              {lines.length > 0 && (
                <ul class="list-none p-0">
                  {lines.map((line, at) => {
                    const named = `${title}-${at}`;
                    return (
                      <li key={at} class="flex flex-col items-start gap-1">
                        <div id={named}>
                          {line.retired && (
                            <>
                              <strong>Item retired</strong>
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
                            onClick={() => {
                              said.value = null;
                              if (older.dropPart(each, line.drop, catalogue)) {
                                telemetry.record({
                                  kind: "older_storage",
                                  held: heldIn(each),
                                  action: "line_discarded",
                                });
                              } else {
                                notDiscarded(older.isUnchanged(each), on);
                              }
                            }}
                          >
                            Discard
                          </button>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
              {said.value?.on === on && <p role="alert">{said.value.words}</p>}
              {discarding.value === on ? (
                <div class="flex flex-col items-start gap-3">
                  <div role="alert">Confirm discard</div>
                  <div class="actions">
                    <button
                      type="button"
                      class="danger"
                      aria-describedby={title}
                      onClick={() => {
                        discarding.value = null;
                        if (older.remove(each)) {
                          telemetry.record({
                            kind: "older_storage",
                            held: heldIn(each),
                            action: "discarded",
                          });
                        } else {
                          notDiscarded(older.isListed(each), on);
                        }
                      }}
                    >
                      Discard
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        discarding.value = null;
                      }}
                    >
                      Keep it
                    </button>
                  </div>
                </div>
              ) : (
                <div class="actions">
                  {fills && (
                    <button
                      type="button"
                      aria-describedby={title}
                      disabled={retired}
                      onClick={() => {
                        const why = onPutBack(each);
                        said.value = why === null ? null : { on, words: why };
                      }}
                    >
                      {retired ? "Cannot Add to Cart" : "Add to cart"}
                    </button>
                  )}
                  <button
                    type="button"
                    aria-describedby={title}
                    onClick={() => {
                      said.value = null;
                      discarding.value = on;
                    }}
                  >
                    Discard
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
 * Why something cannot go back, in the alert's style, its opening words in
 * bold. Not role="alert": it is there as the list is shown, and several at
 * once, read out together, would drown each other and the page.
 */
function NoteSaid({ note: [opening, rest] }: { note: Note }) {
  return (
    <p class="alert">
      <strong>{opening}</strong>
      {rest}
    </p>
  );
}

/** Said when one has gone meanwhile. */
const GONE = "That one has gone: it was sent, or dealt with in another tab.";

/** Whether it is saves or a cart, as telemetry tells them. */
function heldIn(each: Older): "saves" | "cart" {
  return each.batch === null ? "cart" : "saves";
}

/** What it was for, in the words the page asks it in. */
function olderKind(each: Older): string {
  if (each.kind === "report") {
    return "Saying what is on open packages";
  }
  const words = each.kind === null ? "Something" : KIND_WORDS[each.kind];
  return each.batch === null ? `A cart: ${words.toLowerCase()}` : words;
}

/** Where, for which install, and by whom, as the catalogue names them. */
function olderWhere(each: Older, catalogue: Catalogue): string {
  const { places, volunteers } = indexOf(catalogue);
  const place = (placeId: number) =>
    places.get(placeId) ?? "a place no longer in the catalogue";
  const words: string[] = [];
  const at = each.kind === "open" || each.kind === "report";
  if (each.fromId !== null) {
    words.push(`${at ? "at" : "from"} ${place(each.fromId)}`);
  }
  if (each.toId !== null && !at) {
    words.push(`to ${place(each.toId)}`);
  }
  if (each.installNumber !== "") {
    words.push(`for install ${each.installNumber}`);
  }
  const who =
    each.volunteerId === null ? undefined : volunteers.get(each.volunteerId);
  const where = words.length > 0 ? ` ${words.join(" ")}` : "";
  return who === undefined ? where : `${where}, by ${who}`;
}

/** When it was saved, or the cart last changed, and what became of it: a
 * refusal says only why and what to do, as the rest tells it apart. */
function olderWhen(each: Older): string {
  if (each.refused !== null) {
    return `${each.refused} Add it to the cart, fix that, then save.`.trim();
  }
  const at =
    each.at === null
      ? ""
      : ` ${new Date(each.at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
  if (each.batch === null) {
    return at === "" ? "Added but not sent" : `Added on${at} but not sent`;
  }
  return `Saved on this device${at}, not sent.`;
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
            {side === "from" ? "From" : "To"}
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
          <option value="">Pick a place</option>
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
  item: Catalogue["items"][number] | undefined;
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
        No longer in the catalogue.{" "}
        <button type="button" onClick={onRemove}>
          Remove
        </button>
      </li>
    );
  }
  const quantity = quantityOf(line, item);
  return (
    <li class="flex flex-col gap-2">
      <div>
        <strong>{item.name}</strong>
        {item.retired && <small> {RETIRED}</small>}
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
                <strong>Sorry. The package size has changed to {size}</strong>{" "}
                since this item was added. Enter the count again.
              </p>
            )}
            <Field
              quiet
              label={`${pack.name} (${size} ${item.unit})${pack.retired ? `, ${RETIRED}` : ""}`}
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
          item.packages.length > 0
            ? `Loose ${item.unit === "each" ? "units" : item.unit}`
            : item.unit === "each"
              ? "How many"
              : `How many ${item.unit}`
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
          ? quantity.problem
          : `${decimal(quantity.amount)} ${item.unit} in all`}
      </div>
      <button type="button" class="self-start" onClick={onRemove}>
        Remove
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
      <Field label="Find an item, or type or scan its code">
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
        <p>Nothing matches “{query.value.trim()}”.</p>
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
                  item.retired ? RETIRED : null,
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

function OutboxList({ outbox }: { outbox: Outbox }) {
  const entries = outbox.shown().reverse();
  if (entries.length === 0) {
    return null;
  }
  const needsRegistering = entries.some((e) => e.waitingFor === "registration");
  return (
    <section>
      <h2>Saved on this browser</h2>
      {needsRegistering && (
        <p>
          <a href="/register">Register this browser</a> to send what is waiting.
        </p>
      )}
      <ul class={RULED_LIST}>
        {entries.map((entry) => (
          <li key={entry.body.key}>
            {entry.what}
            <br />
            <small>
              {entry.state === "saved"
                ? `In the inventory (transaction ${entry.transactionId}).`
                : (entry.note ?? "Sending…")}
            </small>
            {entry.state !== "waiting" && (
              <>
                {" "}
                <button
                  type="button"
                  onClick={() => outbox.dismiss(entry.body.key)}
                >
                  Dismiss
                </button>
              </>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
