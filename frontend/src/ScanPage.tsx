import { type Signal, useSignal } from "@preact/signals";
import type { TargetedSubmitEvent } from "preact";
import { useEffect, useMemo, useRef } from "preact/hooks";
import { type Api, check, makeApi } from "./api/client";
import { Catalogue as CatalogueReply, DeviceRefusedReply } from "./api/replies";
import {
  addOne,
  askWho,
  type Cart,
  CartStore,
  type Catalogue,
  freshCart,
  indexOf,
  keysAt,
  type Line,
  type Offered,
  offered,
  offeredAt,
  onlyPackage,
  quantityOf,
  RETIRED,
  SIDES,
  type Side,
  search,
  toBatch,
} from "./cart";
import { Field } from "./Field";
import { placeSides, readLabel, type Scanned } from "./labels";
import { type Outbox, sharedOutbox } from "./outbox";
import { decimal, thousandths } from "./quantity";
import { NOT_REGISTERED } from "./registration";
import { Scanner } from "./Scanner";
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

async function loadCatalogue(api: Api): Promise<Loaded> {
  const reply = await check(api.GET("/api/catalogue"), {
    200: CatalogueReply,
    403: DeviceRefusedReply,
  });
  switch (reply.status) {
    case 200:
      return { state: "ready", catalogue: reply.body };
    case 403:
      return { state: "unregistered", reason: reply.body.reason };
    default:
      return { state: "unavailable" };
  }
}

/** A list whose entries are ruled apart: the cart's lines, the outbox. */
const RULED_LIST = "list-none p-0 *:border-t *:border-line *:py-3";

/** The volunteer's page: fill a cart, then Save it to the outbox. */
export function ScanPage({
  api = API,
  outbox = sharedOutbox(),
}: {
  api?: Api;
  outbox?: Outbox;
}) {
  const loaded = useSignal<Loaded>({ state: "loading" });
  const loadedAt = useRef(0);

  function load() {
    void loadCatalogue(api).then((result) => {
      loaded.value = result;
      loadedAt.current = Date.now();
    });
  }

  useEffect(load, [api]);

  // A page left open refreshes a stale catalogue; a failed refresh keeps
  // the one it has.
  useEffect(() => {
    const refresh = () => {
      const stale = Date.now() - loadedAt.current > CATALOGUE_FRESH_MS;
      if (
        document.visibilityState !== "visible" ||
        !stale ||
        loaded.value.state !== "ready"
      ) {
        return;
      }
      void loadCatalogue(api).then((result) => {
        if (result.state === "ready") {
          loaded.value = result;
          loadedAt.current = Date.now();
        }
      });
    };
    const timer = setInterval(refresh, CHECK_EVERY_MS);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [api]);

  useEffect(() => {
    const stop = outbox.listen();
    void outbox.send();
    return stop;
  }, [outbox]);

  const current = loaded.value;
  return (
    <main class="volunteer page-width">
      <h1>Scan</h1>
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
        <CartForm catalogue={current.catalogue} outbox={outbox} />
      )}
      <OutboxList outbox={outbox} />
    </main>
  );
}

function CartForm({
  catalogue,
  outbox,
}: {
  catalogue: Catalogue;
  outbox: Outbox;
}) {
  const store = useMemo(() => new CartStore(catalogue), []);
  const cart = store.cart;
  const query = useSignal("");
  const problems = useSignal<string[]>([]);
  const saved = useSignal<string | null>(null);
  // After a while unused, the phone may have changed hands: offer to
  // change the name, without stopping anyone saving.
  const checkName = useSignal(askWho(cart.value, catalogue));
  const nameList = useRef<HTMLSelectElement>(null);

  // Tabs share one cart; a refreshed catalogue clears what it no longer has.
  useEffect(() => store.listen(), [store]);
  useEffect(() => store.useCatalogue(catalogue), [catalogue]);

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
    kept(store.change(next));
  }

  function set(fields: Partial<Cart>) {
    const next = { ...cart.value, ...fields };
    // What was taken from open packages was where it came from, as it was:
    // another place, or another kind, leaves none of it.
    const moved =
      next.kind !== cart.value.kind || next.fromId !== cart.value.fromId;
    change(
      moved
        ? { ...next, lines: next.lines.map(({ open: _, ...line }) => line) }
        : next,
    );
  }

  /** Add one, and say what: opening, an item of one package is one of
   * those, as only whole packages are opened. */
  function add(itemId: number, packageId?: number): string {
    const item = indexOf(catalogue).items.get(itemId);
    const pack =
      packageId === undefined && cart.value.kind === "open"
        ? onlyPackage(item)
        : item?.packages.find((each) => each.id === packageId);
    change(addOne(cart.value, itemId, pack?.id));
    query.value = "";
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
    // Only what the page offers to take from, where it comes from.
    const offeredKeys = keysAt(
      offered(catalogue, outbox.entries.value),
      current.fromId,
    );
    const batch = toBatch(current, catalogue, offeredKeys);
    if ("problems" in batch) {
      problems.value = batch.problems;
      return;
    }
    if (!outbox.queue(batch.draft, batch.what)) {
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

  const sides = SIDES[cart.value.kind];
  const opens = offered(catalogue, outbox.entries.value);
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
        {(
          [
            ["move", "Moving stock"],
            ["receipt", "Receiving stock"],
            ["install", "Taking it to an install"],
            ["open", "Opening packages"],
          ] as const
        ).map(([kind, words]) => (
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
        ))}
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
                  set({
                    lines: cart.value.lines.map((each) =>
                      each.itemId === line.itemId ? next : each,
                    ),
                  })
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
      {item.packages.map((pack) => (
        <Field
          key={pack.id}
          quiet
          label={`${pack.name} (${decimal(thousandths(pack.size) ?? 0)} ${item.unit})${pack.retired ? `, ${RETIRED}` : ""}`}
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
      ))}
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
      {opens.map((open) => {
        const taken: NonNullable<Line["open"]>[string] = line.open?.[
          open.key
        ] ?? {
          amount: "",
          whole: false,
        };
        const take = (next: typeof taken) =>
          onChange({ ...line, open: { ...line.open, [open.key]: next } });
        const left = decimal(open.left);
        const pack = item.packages.find((each) => each.id === open.package_id);
        const since = `, opened ${open.opened_at.slice(0, 10)}`;
        return (
          <div key={open.key} class="flex flex-col gap-1">
            <Field
              quiet
              label={`From the open ${pack?.name ?? "package"}: ${left} ${item.unit} left${since}`}
            >
              {(id) => (
                <input
                  id={id}
                  inputMode="decimal"
                  value={taken.amount}
                  disabled={taken.whole}
                  onInput={(event) =>
                    take({
                      ...taken,
                      amount: event.currentTarget.value,
                      whole: false,
                    })
                  }
                />
              )}
            </Field>
            {moving && (
              <label class="flex items-center gap-2 font-normal">
                <input
                  type="checkbox"
                  checked={taken.whole}
                  onChange={(event) =>
                    take(
                      event.currentTarget.checked
                        ? { amount: left, whole: true }
                        : { amount: "", whole: false },
                    )
                  }
                />
                All of it, moved still open
              </label>
            )}
            {!taken.whole && (
              <label class="flex items-center gap-2 font-normal">
                <input
                  type="checkbox"
                  checked={taken.usedUp === true}
                  onChange={(event) =>
                    take({ ...taken, usedUp: event.currentTarget.checked })
                  }
                />
                {(thousandths(taken.amount) ?? 0) >= open.left
                  ? "That takes all it shows: is it used up?"
                  : open.short
                    ? "A short end: is it used up?"
                    : "Used up: what is left is no use"}
              </label>
            )}
          </div>
        );
      })}
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
  const entries = [...outbox.entries.value].reverse();
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
