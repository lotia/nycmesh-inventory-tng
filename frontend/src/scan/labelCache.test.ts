/**
 * What the cache promises: a scan that costs no request, a code it has never
 * heard of that still resolves, and a staleness rule that is actually applied.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { zipTies } from "../items/testFixtures";
import {
  CACHE_MAX_AGE_MS,
  cachedLabel,
  forgetLabel,
  forgetLabelCache,
  refreshLabelCache,
  STORAGE_KEY,
} from "./labelCache";
import { mapped, PACKET, WALL } from "./testFixtures";

const apiGet = vi.hoisted(() => vi.fn());
vi.mock("../api/client", () => ({
  apiGet,
  asApiError: (error: unknown) => error,
  isAbort: () => false,
}));

/** The map, in the shape LabelMapSerializer sends it. */
function serving(rows: Record<string, unknown>[]): void {
  apiGet.mockImplementation((path: string) =>
    path === "/api/labels"
      ? Promise.resolve(rows)
      : Promise.reject(new Error(`nothing serves ${path}`)),
  );
}

beforeEach(() => {
  localStorage.clear();
  forgetLabelCache();
  apiGet.mockReset();
});

describe("the label cache", () => {
  it("answers a code it holds without asking the network", async () => {
    serving([mapped(PACKET)]);
    expect(await refreshLabelCache()).toBe(true);
    const during = apiGet.mock.calls.length;

    const hit = cachedLabel(PACKET.code);

    expect(hit?.item_name).toBe(zipTies.name);
    expect(apiGet.mock.calls).toHaveLength(during);
  });

  it("says nothing about a code minted since it was filled", async () => {
    serving([mapped(PACKET)]);
    await refreshLabelCache();

    expect(cachedLabel("NEVERSEEN0")).toBeNull();
  });

  it("survives a reload, because a basement is where this matters", async () => {
    serving([mapped(PACKET)]);
    await refreshLabelCache();

    // What a fresh page load looks like: memory gone, localStorage kept.
    forgetLabelCache();

    expect(cachedLabel(PACKET.code)?.item_name).toBe(zipTies.name);
    expect(localStorage.getItem(STORAGE_KEY)).not.toBeNull();
  });

  it("is used while it is younger than the limit", async () => {
    serving([mapped(PACKET)]);
    await refreshLabelCache(undefined, 1_000);

    expect(cachedLabel(PACKET.code, 1_000 + CACHE_MAX_AGE_MS)).not.toBeNull();
  });

  it("is ignored once it is older, rather than naming an item wrongly", async () => {
    serving([mapped(PACKET)]);
    await refreshLabelCache(undefined, 1_000);

    expect(cachedLabel(PACKET.code, 1_000 + CACHE_MAX_AGE_MS + 1)).toBeNull();
  });

  it("keeps a location label, which has no item to carry", async () => {
    serving([mapped(WALL, null)]);
    await refreshLabelCache();

    const hit = cachedLabel(WALL.code);

    expect(hit?.kind).toBe("location");
    expect(hit?.item_name).toBeNull();
  });

  it("carries no item for a label whose item the catalogue has lost", async () => {
    serving([mapped(PACKET, null)]);
    await refreshLabelCache();

    expect(cachedLabel(PACKET.code)?.item_name).toBeNull();
  });

  it("reports a wrong shape instead of rejecting into nothing", async () => {
    // This promised not to throw, and only the fetch was guarded -- so a
    // response of the wrong shape escaped as an unhandled rejection, which is
    // what CI caught while every test still passed.
    apiGet.mockResolvedValue({ not: "a list" });

    expect(await refreshLabelCache()).toBe(false);
  });

  it("reports a refresh that failed instead of throwing at the app", async () => {
    apiGet.mockRejectedValue(new Error("the basement"));

    expect(await refreshLabelCache()).toBe(false);
    expect(cachedLabel(PACKET.code)).toBeNull();
  });

  it("leaves the previous cache in place when a refresh fails", async () => {
    serving([mapped(PACKET)]);
    await refreshLabelCache();

    apiGet.mockRejectedValue(new Error("the basement"));
    expect(await refreshLabelCache()).toBe(false);

    expect(cachedLabel(PACKET.code)?.item_name).toBe(zipTies.name);
  });
});

describe("a code this device has just revoked", () => {
  it("stops being answered out of the cache, and stays out across a reload", async () => {
    // `forgetLabel` says what this closes: without it, a cache filled before
    // the revocation goes on calling that sticker fine on the very device
    // that retired it.
    serving([mapped(PACKET), mapped(WALL, null)]);
    await refreshLabelCache();

    forgetLabel(PACKET.code);

    expect(cachedLabel(PACKET.code)).toBeNull();
    // The wall code is untouched: one sticker was revoked, not the map.
    expect(cachedLabel(WALL.code)).not.toBeNull();
    // And written through, so the next load of the app does not bring it back.
    forgetLabelCache();
    expect(cachedLabel(PACKET.code)).toBeNull();
    expect(cachedLabel(WALL.code)).not.toBeNull();
  });

  it("hydrates from storage first, which is the failed-refresh case it is for", async () => {
    // A refresh that failed leaves nothing in memory, and the map on disk is
    // still young enough to be answered from -- so reading it is exactly when
    // the revocation has to reach it.
    serving([mapped(PACKET)]);
    await refreshLabelCache();
    forgetLabelCache();

    forgetLabel(PACKET.code);

    forgetLabelCache();
    expect(cachedLabel(PACKET.code)).toBeNull();
  });

  it("does nothing at all when there is no cache to take it out of", () => {
    // The ordinary case on a cold start: the app revokes before the map has
    // been filled, and this must not mint one.
    forgetLabelCache();
    window.localStorage.clear();

    forgetLabel(PACKET.code);

    expect(window.localStorage.getItem(STORAGE_KEY)).toBeNull();
  });
});
