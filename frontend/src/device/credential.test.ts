/**
 * What this browser stores, and the two things it must never do to a request.
 *
 * The load-bearing pair, both of which would be invisible in ordinary use and
 * expensive in a room:
 *
 * - **A cold start mints once, however many callers ask at once.** Without the
 *   guard that is the burst `DEVICE_ENROLMENT_RATE` refuses, produced by the
 *   app on an ordinary load rather than by anybody misbehaving.
 * - **Nothing waits for it.** A request made before this browser has a
 *   credential goes without one. A batch a volunteer is holding must not be
 *   delayed by something that decides nothing.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet } from "../api/client";
import { ENROL_PATH, enrol, forget, HEADER, held } from "./credential";

/** Where `storage.ts` puts it. Named here so a rename fails loudly. */
const STORAGE_KEY = "inventory.device";

/** Every fetch this test made, as [url, init] pairs. */
function calls(): [string, RequestInit][] {
  return (globalThis.fetch as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls;
}

function answering(token: string): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === ENROL_PATH
        ? new Response(JSON.stringify({ token, device: "abc" }), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          })
        : new Response(JSON.stringify({ results: [] }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
    ),
  );
}

beforeEach(() => {
  window.localStorage.clear();
  forget();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("what is stored", () => {
  it("is read afresh while there is nothing, so another tab's mint is picked up", () => {
    expect(held()).toBeNull();

    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: "minted-elsewhere" }));

    expect(held()).toBe("minted-elsewhere");
  });

  it("is not read again once there is something, because it cannot change", async () => {
    answering("signed-token");
    await enrol();

    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: "something-else" }));
    expect(held()).toBe("signed-token");

    // And forgetting clears the held answer as well as the stored one, so the
    // two cannot come apart.
    forget();
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ token: "something-else" }));
    expect(held()).toBe("something-else");
  });

  it("is nothing at all until something has been minted", () => {
    expect(held()).toBeNull();
  });

  it("is what the server handed over", async () => {
    answering("signed-token");

    await enrol();

    expect(held()).toBe("signed-token");
  });

  it("is thrown away when it is forgotten, so the next request mints again", async () => {
    answering("signed-token");
    await enrol();

    forget();

    expect(held()).toBeNull();
  });

  it("survives a body that is not the shape this version reads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ device: "abc" }), {
            status: 201,
            headers: { "Content-Type": "application/json" },
          }),
      ),
    );

    await enrol();

    expect(held()).toBeNull();
  });
});

describe("asking for one", () => {
  it("asks once, however many callers ask at once", async () => {
    answering("signed-token");

    await Promise.all([enrol(), enrol(), enrol()]);

    expect(calls().filter(([url]) => url === ENROL_PATH)).toHaveLength(1);
  });

  it("is the only request it makes, and it makes it through this app's own client", async () => {
    answering("signed-token");

    await enrol();
    await apiGet("/api/items");

    expect(calls().map(([url]) => url)).toEqual([ENROL_PATH, "/api/items"]);
  });

  it("asks for nothing more once this browser has one", async () => {
    answering("signed-token");
    await enrol();
    const asked = calls().length;

    await enrol();

    expect(calls()).toHaveLength(asked);
  });

  it("says nothing at all when the server refuses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("", { status: 429 })),
    );

    await expect(enrol()).resolves.toBeUndefined();
    expect(held()).toBeNull();
  });

  it("says nothing at all when there is no network", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))),
    );

    await expect(enrol()).resolves.toBeUndefined();
    expect(held()).toBeNull();
  });
});

describe("what a request carries", () => {
  it("carries nothing, and does not wait, before this browser has one", async () => {
    answering("signed-token");

    await apiGet("/api/items");

    const [, init] = calls().find(([url]) => url === "/api/items") ?? ["", {}];
    expect(new Headers(init.headers).get(HEADER)).toBeNull();
  });

  it("carries it on every request once there is one", async () => {
    answering("signed-token");
    await enrol();

    await apiGet("/api/items");

    const [, init] = calls().find(([url]) => url === "/api/items") ?? ["", {}];
    expect(new Headers(init.headers).get(HEADER)).toBe("signed-token");
  });

  it("keeps the headers the caller asked for", async () => {
    answering("signed-token");
    await enrol();

    await apiGet("/api/items");

    const [, init] = calls().find(([url]) => url === "/api/items") ?? ["", {}];
    expect(new Headers(init.headers).get("Accept")).toBe("application/json");
  });
});

// The reason `client.ts` imports `held` and nothing that mints, which
// `credential.ts` argues.
describe("sending a request", () => {
  it("mints nothing by itself", async () => {
    answering("signed-token");

    await apiGet("/api/items");

    expect(calls().filter(([url]) => url === ENROL_PATH)).toHaveLength(0);
    expect(held()).toBeNull();
  });
});
