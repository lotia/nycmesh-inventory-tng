import { describe, expect, test, vi } from "vitest";
import { answering, json, saved, TOO_OLD } from "../testing/fetch";
import {
  draft,
  entry,
  named,
  replies,
  states,
  store,
  stored,
} from "../testing/outbox";
import { keep } from "../testing/saves";
import { type Entry, Outbox, STORAGE_KEY } from "./outbox";

/** Kept as another tab of this build keeps them: stored, and the storage
 * event a browser fires in this one, with what was stored before. */
function storeElsewhere(...entries: unknown[]) {
  storedElsewhere(...entries)();
}

/** Stores `entries` as another tab would; the event of it, to send later. */
function storedElsewhere(...entries: unknown[]): () => void {
  const oldValue = localStorage.getItem(STORAGE_KEY);
  store(...entries);
  const newValue = localStorage.getItem(STORAGE_KEY);
  return () =>
    window.dispatchEvent(
      new StorageEvent("storage", { key: STORAGE_KEY, oldValue, newValue }),
    );
}

describe("listening", () => {
  test("coming back online sends what waits", async () => {
    const { api, sent } = answering(
      replies(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    const outbox = new Outbox(api);
    const stop = outbox.listen();
    outbox.queue(draft(), named("a"));
    await vi.waitFor(() =>
      expect(outbox.entries.value[0]?.waitingFor).toBe("network"),
    );
    window.dispatchEvent(new Event("online"));
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    expect(sent).toHaveLength(2);
    stop();
  });

  test("the page shown again sends what waits; hidden, nothing", async () => {
    const { api, sent } = answering(() => saved());
    store(entry());
    const outbox = new Outbox(api);
    const stop = outbox.listen();
    const shown = (state: DocumentVisibilityState) => {
      Object.defineProperty(document, "visibilityState", {
        value: state,
        configurable: true,
      });
      document.dispatchEvent(new Event("visibilitychange"));
    };
    shown("hidden");
    expect(sent).toHaveLength(0);
    shown("visible");
    await vi.waitFor(() => expect(states(outbox)).toEqual(["saved"]));
    Reflect.deleteProperty(document, "visibilityState");
    stop();
  });

  test("a saved batch another tab forgets stays here, off the list, until this tab's catalogue shows it", () => {
    const old = entry({ state: "saved", transactionId: 1, queuedAt: 1 });
    const kept = entry({ state: "saved", transactionId: 2, queuedAt: 2 });
    const waiting = entry({ queuedAt: 3 });
    store(old, kept, waiting);
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    store(kept, waiting);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    expect(outbox.entries.value).toEqual([old, kept, waiting]);
    expect(outbox.shown()).toEqual([kept, waiting]);
    outbox.forgetShown([kept.body.key]);
    expect(outbox.entries.value).toEqual([old, kept, waiting]);
    outbox.forgetShown([old.body.key]);
    expect(outbox.entries.value).toEqual([kept, waiting]);
    stop();
  });

  /** A saved batch, and a refused one an older build's tab queued again
   * under its key (inventory-tng-0p8). */
  function underOneKey() {
    const saved = entry({ state: "saved", transactionId: 1, queuedAt: 1 });
    const refused = entry({
      body: { ...saved.body, lines: [{ item_id: 6, quantity: "1" }] },
      state: "refused",
      reason: { code: "refused", problems: [] },
      queuedAt: 2,
    });
    return { saved, refused };
  }

  test("a saved batch another tab forgets stays here, though another batch holds its key", () => {
    const { saved, refused } = underOneKey();
    store(saved, refused);
    const outbox = new Outbox(answering(() => json({}, 500)).api);
    const stop = outbox.listen();
    store(refused);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    // Still counted (scan/unseen-batches.ts, unseen), and its sibling still
    // listed.
    expect(outbox.entries.value).toEqual([saved, refused]);
    expect(outbox.shown()).toEqual([refused]);
    outbox.forgetShown([saved.body.key]);
    expect(outbox.entries.value).toEqual([refused]);
    expect(outbox.shown()).toEqual([refused]);
    stop();
  });

  test("a batch another tab keeps under the key of one it forgot shows here", () => {
    const { saved, refused } = underOneKey();
    store(saved);
    const outbox = new Outbox(answering(() => json({}, 500)).api);
    const stop = outbox.listen();
    store();
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    store(refused);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    expect(outbox.entries.value).toEqual([saved, refused]);
    expect(outbox.shown()).toEqual([refused]);
    stop();
  });

  test("a batch another tab forgot, then queued again, shows here", () => {
    const saved = entry({ state: "saved", transactionId: 1, queuedAt: 1 });
    const again = entry({ body: saved.body, queuedAt: 2 });
    store(saved);
    const outbox = new Outbox(answering(() => json({}, 500)).api);
    const stop = outbox.listen();
    store();
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    store(again);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    expect(outbox.entries.value).toEqual([again]);
    expect(outbox.shown()).toEqual([again]);
    stop();
  });

  test("another tab's change shows here", () => {
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    const other = entry({ state: "saved", transactionId: 3 });
    store(other);
    window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    expect(outbox.entries.value).toEqual([other]);
    stop();
  });

  test("another tab of this build held back as too old makes this one too old", () => {
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    const held = { reason: { code: "must_upgrade" as const } };
    // An older build's tab, sharing the key, is too old, not this one.
    storeElsewhere(entry({ ...held, build: "2026-11-12T09:00:00.000Z" }));
    expect(outbox.builds.newer.value).toBeNull();
    storeElsewhere(entry(held));
    expect(outbox.builds.newer.value).toBe("too_old");
    stop();
  });

  test("a batch held back by another tab makes this one too old, though this tab wrote before hearing", () => {
    const waiting = entry();
    const refused = entry({ state: "refused", reason: { code: "key_reused" } });
    store(waiting, refused);
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    const heard = storedElsewhere(
      { ...waiting, reason: { code: "must_upgrade" } },
      refused,
    );
    // This tab writes, reading the hold, before the storage event comes.
    outbox.dismiss(refused);
    heard();
    expect(outbox.builds.newer.value).toBe("too_old");
    stop();
  });

  test("a batch held back before this tab loaded does not make it too old", async () => {
    // A tab held back while this build was too old...
    const held = new Outbox(answering(() => json(TOO_OLD, 409)).api);
    held.queue(draft(), named("held"));
    await vi.waitFor(() => expect(held.builds.newer.value).toBe("too_old"));
    const [stale] = stored() as Entry[];
    expect(stale?.reason).toEqual({ code: "must_upgrade" });
    // ...then the build is current again (rolled back), and this tab is
    // loaded since.
    const { api, sent } = answering(() => saved());
    const outbox = new Outbox(api);
    const stop = outbox.listen();
    // Another tab writes, the stale one still kept.
    storeElsewhere(stale, entry());
    expect(outbox.builds.newer.value).toBeNull();
    await outbox.send();
    expect(sent).toHaveLength(2);
    expect(states(outbox)).toEqual(["saved", "saved"]);
    stop();
  });

  test("an older build's tab's batch does not show here", () => {
    const outbox = new Outbox(answering(() => saved()).api);
    const stop = outbox.listen();
    keep("inventory.outbox.v3", [entry()]);
    window.dispatchEvent(
      new StorageEvent("storage", { key: "inventory.outbox.v3" }),
    );
    expect(outbox.entries.value).toEqual([]);
    stop();
  });

  test("sending twice at once sends each batch once", async () => {
    const { api, sent } = answering(() => saved());
    store(entry());
    const outbox = new Outbox(api);
    await Promise.all([outbox.send(), outbox.send()]);
    expect(sent).toHaveLength(1);
  });
});
