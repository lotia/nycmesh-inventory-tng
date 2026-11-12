import { afterEach, describe, expect, test, vi } from "vitest";
import { BUILD, BUILD_KEY, Builds, stampOf } from "./build";
import { telemetry } from "./telemetry";

const MINE = "2026-11-12T10:00:00.000Z";
const NEWER = "2026-11-12T11:00:00.000Z";
const OLDER = "2026-11-12T09:00:00.000Z";

const stamp = () => JSON.parse(localStorage.getItem(BUILD_KEY) ?? "null");

/** Another tab writing `value` under `key`. */
function written(key: string, value: string | null) {
  window.dispatchEvent(new StorageEvent("storage", { key, newValue: value }));
}

afterEach(() => vi.restoreAllMocks());

test("this build's id is when it was built, as the backend reads one", () => {
  expect(BUILD).toMatch(/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/);
});

test.each([
  [NEWER, true],
  [MINE, false],
  [OLDER, false],
  ["2026-11-12", false],
  ["zzzz", false],
  [undefined, false],
  [20261112, false],
])("%j is newer: %s", (other, newer) => {
  expect(new Builds(MINE).isNewer(other)).toBe(newer);
});

test.each([
  [{ build: MINE }, MINE],
  [{ lines: [] }, undefined],
  [[], undefined],
  [null, undefined],
])("the stamp of %j is %j", (stored, build) => {
  expect(stampOf(stored)).toBe(build);
});

describe("stamping", () => {
  test("a tab stamps its build where none is", () => {
    const stop = new Builds(MINE).listen([]);
    expect(stamp()).toEqual({ build: MINE });
    stop();
  });

  test("a tab stamps over an older build's", () => {
    localStorage.setItem(BUILD_KEY, JSON.stringify({ build: OLDER }));
    const builds = new Builds(MINE);
    const stop = builds.listen([]);
    expect(stamp()).toEqual({ build: MINE });
    expect(builds.newer.value).toBeNull();
    stop();
  });

  test("a newer build's stamp found at the start says nothing, as it may be left by a build rolled back", () => {
    localStorage.setItem(BUILD_KEY, JSON.stringify({ build: NEWER }));
    const builds = new Builds(MINE);
    const stop = builds.listen([]);
    expect(stamp()).toEqual({ build: MINE });
    expect(builds.newer.value).toBeNull();
    stop();
  });
});

describe("other tabs", () => {
  test.each([BUILD_KEY, "inventory.cart.v2"])(
    "a newer build's stamp written to %s says one is out",
    (key) => {
      const builds = new Builds(MINE);
      const stop = builds.listen(["inventory.cart.v2"]);
      written(key, JSON.stringify({ build: NEWER, lines: [] }));
      expect(builds.newer.value).toBe("other_tab");
      stop();
    },
  );

  test.each([
    ["this build's", JSON.stringify({ build: MINE })],
    ["an older build's", JSON.stringify({ build: OLDER })],
    ["no stamp", JSON.stringify([])],
    ["what is not JSON", "{"],
    ["a key removed", null],
  ])("%s says nothing", (_name, value) => {
    const builds = new Builds(MINE);
    const stop = builds.listen(["inventory.cart.v2"]);
    written("inventory.cart.v2", value);
    expect(builds.newer.value).toBeNull();
    stop();
  });

  test("a key it does not follow says nothing", () => {
    const builds = new Builds(MINE);
    const stop = builds.listen([]);
    written("inventory.cart.v2", JSON.stringify({ build: NEWER }));
    expect(builds.newer.value).toBeNull();
    stop();
    written(BUILD_KEY, JSON.stringify({ build: NEWER }));
    expect(builds.newer.value).toBeNull();
  });
});

describe("learning", () => {
  test("the first way is kept, and reported once", () => {
    const record = vi.spyOn(telemetry, "record");
    const builds = new Builds(MINE);
    builds.learn("other_tab");
    builds.learn("server");
    builds.learn("other_tab");
    expect(builds.newer.value).toBe("other_tab");
    expect(record.mock.calls).toEqual([
      [{ kind: "out_of_date", learned: "other_tab" }],
    ]);
  });

  test("too old, learned later, says more, and stays", () => {
    const record = vi.spyOn(telemetry, "record");
    const builds = new Builds(MINE);
    builds.learn("server");
    builds.learn("too_old");
    builds.learn("too_old");
    builds.learn("other_tab");
    expect(builds.newer.value).toBe("too_old");
    expect(record.mock.calls).toEqual([
      [{ kind: "out_of_date", learned: "server" }],
      [{ kind: "out_of_date", learned: "too_old" }],
    ]);
  });
});
