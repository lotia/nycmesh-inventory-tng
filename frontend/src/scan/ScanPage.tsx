import { msg } from "@lingui/core/macro";
import { signal, useSignal } from "@preact/signals";
import { useEffect, useMemo, useRef } from "preact/hooks";
import { type Api, makeApi } from "../api/client";
import { say, sayParts } from "../shared/i18n";
import { onStoredChange } from "../shared/local-storage";
import {
  STORAGE_KEY as OUTBOX_KEY,
  type Outbox,
  sharedOutbox,
} from "../shared/outbox";
import { NOT_REGISTERED } from "../shared/registration";
import { focusInPlace, ignoreTaps } from "../shared/settling";
import { telemetry } from "../shared/telemetry";
import { CartForm } from "./CartForm";
import { STORAGE_KEY as CART_KEY, type Catalogue, loadCart } from "./cart";
import { type Loaded, loadCatalogue } from "./catalogue";
import { useEveryMinuteAndWhenShown } from "./every-minute-and-when-shown";
import { NewerBuild } from "./NewerBuild";
import { heldIn } from "./OlderList";
import { type AddDifference, type AddRest, OutboxList } from "./OutboxList";
import { OlderStore } from "./older-builds";
import { unseen } from "./unseen-batches";

const API = makeApi();
/** A page left open fetches the catalogue again once it is this old, so a
 * hub tablet sees new items, volunteers and settings. */
const CATALOGUE_FRESH_MS = 10 * 60_000;
/**
 * How long Record waits for a fresh catalogue to check the cart against
 * before it queues the cart anyway (Ali, 2026-11-23, inventory-tng-h7ua).
 * Run out, the cart is queued as checked against the catalogue shown, and
 * the server still refuses a line in a unit or package size changed since;
 * the catalogue, come later, is shown all the same. Long enough for a weak
 * signal to bring the catalogue, short enough that a phone with none is
 * not kept waiting at Record.
 */
const RECORD_CATALOGUE_WAIT_MS = 3_000;

export const SCAN = msg`Scan`;
export const LOADING = msg`Loading the catalogue…`;
/** After why this browser cannot record (registration.NOT_REGISTERED). */
export const REGISTER_IT = msg`<0>Register it</0>, then come back.`;
export const COULD_NOT_LOAD = msg`Couldn't load the catalogue.`;
export const TRY_AGAIN = msg`Try again`;

/** The volunteer's page: fill a cart, then Record it, through the outbox. */
export function ScanPage({
  api = API,
  outbox = sharedOutbox(),
  reload,
}: {
  api?: Api;
  outbox?: Outbox;
  /** Refresh this page, to load a newer build or retry the camera
   * (scan.tsx). */
  reload: () => void;
}) {
  const loaded = useSignal<Loaded>({ state: "loading" });
  // Whether the cart holds no lines: until it does, a newer build is not
  // loaded, as it would not read this build's cart (shared/build.ts).
  const cartEmpty = useMemo(() => signal(storedCartEmpty()), []);
  // Whether a reload brings back the cart (CartForm, cartKept).
  const cartKept = useMemo(() => signal(true), []);
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
  // And what puts a refused batch's other lines in it (add-the-rest.ts).
  const rester = useRef<AddRest | null>(null);
  // The Cart heading (CartForm), where the outbox's list sends focus once
  // nothing is listed.
  const cartHeading = useRef<HTMLHeadingElement>(null);
  const pageHeading = useRef<HTMLHeadingElement>(null);
  // One refresh at a time; one asked for meanwhile follows it.
  const asking = useRef<"no" | "yes" | "again">("no");

  // The newest load shown: an older one's answer, which may not show a
  // batch a newer one let the outbox forget, is never shown after it. One
  // whose newer loads have not answered, or failed, is shown: Record's own
  // load (latest), failing, never drops a refresh that came.
  const newest = useRef(0);
  const asked = useRef(0);

  /** Load it; again, keeping the one it has if that fails. The one
   * loaded and shown; or null. */
  async function fetch(again: boolean): Promise<Catalogue | null> {
    const order = ++asked.current;
    const result = await loadCatalogue(api, outbox);
    if (order < newest.current || (again && result.state !== "ready")) {
      return null;
    }
    newest.current = order;
    loaded.value = result;
    loadedAt.current = Date.now();
    if (result.state !== "ready") {
      return null;
    }
    // Shown now: what it shows need not be kept (shared/outbox.ts).
    outbox.forgetShown(result.catalogue.saved);
    return result.catalogue;
  }

  /** The catalogue loaded again and shown, for Record to check the cart
   * against; if it does not come within RECORD_CATALOGUE_WAIT_MS, the one
   * shown then, which a refresh may have changed meanwhile. Come later, it
   * is shown all the same. */
  async function latest(): Promise<Catalogue> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const late = new Promise<null>((resolve) => {
      timer = setTimeout(() => resolve(null), RECORD_CATALOGUE_WAIT_MS);
    });
    const fresh = await Promise.race([fetch(true), late]).finally(() =>
      clearTimeout(timer),
    );
    // Record is offered only while one is shown, and a reload that fails
    // keeps it.
    return (
      fresh ?? (loaded.value as Extract<Loaded, { state: "ready" }>).catalogue
    );
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
  useEveryMinuteAndWhenShown(() => {
    const stale = Date.now() - loadedAt.current > CATALOGUE_FRESH_MS;
    if (stale && loaded.value.state === "ready") {
      refresh();
    }
  }, [api]);

  // The outbox's batches the catalogue does not show (unseen-batches.ts,
  // unseen).
  const shown = loaded.value.state === "ready" ? loaded.value.catalogue : null;
  const entries = outbox.entries.value;
  const pending = useMemo(
    () => (shown === null ? [] : unseen(entries, shown)),
    [shown, entries],
  );
  // A save the server held back as from an older build too old, here or
  // in another tab, is listed with what older builds left (shared/outbox.ts).
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
      <h1 ref={pageHeading} tabIndex={-1}>
        {say(SCAN)}
      </h1>
      {/* With the cart, below the camera and the item search (CartForm). */}
      {current.state !== "ready" && (
        <NewerBuild
          outbox={outbox}
          cartEmpty={cartEmpty}
          cartKept={cartKept}
          reload={reload}
        />
      )}
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
          <button
            type="button"
            onClick={() => {
              // The alert gives way to Loading, and the list below moves
              // up (Ali, 2026-11-27, inventory-tng-r3jx).
              load();
              focusInPlace(pageHeading.current);
              ignoreTaps(document, () => pageHeading.current);
            }}
          >
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
          cartKept={cartKept}
          cartKey={cartKey}
          older={older}
          adder={adder}
          rester={rester}
          reload={reload}
          latest={latest}
          cartHeading={cartHeading}
        />
      )}
      <OutboxList
        outbox={outbox}
        catalogue={shown}
        cartKey={cartKey.value}
        onAddDifference={(entry, difference) =>
          adder.current?.(entry, difference) ?? null
        }
        onAddRest={(entry, rest) => rester.current?.(entry, rest) ?? null}
        after={cartHeading}
      />
    </main>
  );
}

/** Whether the stored cart holds no lines. */
function storedCartEmpty(): boolean {
  return loadCart().lines.length === 0;
}
