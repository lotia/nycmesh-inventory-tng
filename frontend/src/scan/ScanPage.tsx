import { msg } from "@lingui/core/macro";
import { signal, useSignal } from "@preact/signals";
import { useEffect, useMemo, useRef } from "preact/hooks";
import { type Api, makeApi } from "../api/client";
import { MUST_UPGRADE, UPDATED, UPGRADE } from "../shared/build";
import { say, sayParts } from "../shared/i18n";
import {
  STORAGE_KEY as OUTBOX_KEY,
  type Outbox,
  sharedOutbox,
} from "../shared/outbox";
import { NOT_REGISTERED } from "../shared/registration";
import { onStoredChange } from "../shared/stored";
import { telemetry } from "../shared/telemetry";
import { CartForm, CHECK_EVERY_MS } from "./CartForm";
import { STORAGE_KEY as CART_KEY, loadCart } from "./cart";
import { type Loaded, loadCatalogue } from "./catalogue";
import { CAUTION_BOX } from "./Notes";
import { heldIn } from "./OlderList";
import { type AddDifference, OutboxList } from "./OutboxList";
import { unseen } from "./offered";
import { OlderStore } from "./older";

const API = makeApi();
/** A page left open fetches the catalogue again once it is this old, so a
 * hub tablet sees new items, volunteers and settings. */
const CATALOGUE_FRESH_MS = 10 * 60_000;

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

  // The outbox's batches the catalogue does not show (offered.ts, unseen).
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

/** Whether the stored cart holds no lines. */
function storedCartEmpty(): boolean {
  return loadCart().lines.length === 0;
}
