/**
 * What the cache promises: a scan that costs no request, a code it has never
 * heard of that still resolves, and a staleness rule that is actually applied.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

import { zipTies } from "../items/testFixtures";
import {
  CACHE_MAX_AGE_MS,
  cachedLabel,
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
    // A wrong item name in a cart line is worse than a slow scan.
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
