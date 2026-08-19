/**
 * Arriving by label, which is how a volunteer meets this app for the first
 * time: they point their phone's own camera at a sticker on a shelf.
 */
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { STORAGE_KEY } from "../cart/cartStorage";
import { zipTies } from "../items/testFixtures";
import { callsTo, renderScreen } from "../testHarness";
import { DeepLink } from "./DeepLink";

const PACKET = {
  code: "7QK3M2XV9A",
  kind: "item" as const,
  quantity: "100.000",
  revoked_at: null,
  item: 1,
  location: null,
};

const WALL = {
  code: "4NP8R7T2WQ",
  kind: "location" as const,
  quantity: "1.000",
  revoked_at: null,
  item: null,
  location: 3,
};

/** The two reads a code costs: the label, then the item it points at. */
function serving(label: unknown, status = 200): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      if (path.startsWith("/api/labels/")) {
        return new Response(JSON.stringify(label), { status });
      }
      return new Response(JSON.stringify(zipTies), { status: 200 });
    }),
  );
}

function arriveAt(path: string) {
  window.history.replaceState(null, "", path);
  return renderScreen(<DeepLink />);
}

/** What the cart holds, read back out of the store the provider writes to. */
function stored(): { lines: { name: string; quantity: number }[]; locationId: number | null } {
  return JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}");
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("arriving by label", () => {
  it("puts the item in the batch, in the amount the sticker stands for", async () => {
    serving(PACKET);
    arriveAt("/S/7QK3M2XV9A");
    expect(await screen.findByRole("alert")).toHaveTextContent("Added 100 × Zip Ties Reusable");
    await waitFor(() => expect(stored().lines).toHaveLength(1));
    expect(stored().lines[0]).toMatchObject({ name: "Zip Ties Reusable", quantity: 100 });
  });

  it("sets the batch's location from the code on the wall", async () => {
    serving(WALL);
    arriveAt("/S/4NP8R7T2WQ");
    expect(await screen.findByRole("alert")).toHaveTextContent(/location set/i);
    await waitFor(() => expect(stored().locationId).toBe(3));
  });

  it("puts the address bar back, so a reload does not scan again", async () => {
    serving(PACKET);
    arriveAt("/S/7QK3M2XV9A");
    await screen.findByRole("alert");
    await waitFor(() => expect(window.location.pathname).toBe("/"));
  });

  it("still accepts a superseded sticker, and says it is one", async () => {
    serving({ ...PACKET, revoked_at: "2026-01-01T00:00:00Z" });
    arriveAt("/S/7QK3M2XV9A");
    const said = await screen.findByRole("alert");
    expect(said).toHaveTextContent("Added 100 × Zip Ties Reusable");
    expect(said).toHaveTextContent(/reprinted/i);
    await waitFor(() => expect(stored().lines).toHaveLength(1));
  });

  it("sends an unknown code to the search rather than to an error page", async () => {
    serving({ detail: "No Label matches the given query." }, 404);
    arriveAt("/S/ZZZZZZZZZZ");
    expect(await screen.findByRole("alert")).toHaveTextContent(/search for the item instead/i);
  });

  it("says what went wrong when the service could not answer", async () => {
    serving({ detail: "Nope." }, 500);
    arriveAt("/S/7QK3M2XV9A");
    expect(await screen.findByRole("alert")).toHaveTextContent("Nope.");
  });

  it("does nothing at all when the app was opened normally", () => {
    serving(PACKET);
    arriveAt("/");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // The session read is the app asking who is signed in; what must not
    // have happened is a label being resolved.
    expect(callsTo("/api/labels/")).toHaveLength(0);
  });
});
