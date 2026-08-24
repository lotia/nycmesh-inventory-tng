import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { Enrolment } from "./api/capabilities";
import { page } from "./api/testFixtures";
import { queueBatch } from "./batch/outbox";
import { batch, forgetWhatWasStubbed, nothingQueued } from "./batch/testFixtures";
import { callsTo, stubSession, VOLUNTEER } from "./testHarness";

/** Every path answering emptily, with `/api/me` answering this posture. */
function deployment(enrolment: Enrolment): void {
  stubSession(
    { ...VOLUNTEER, enrolment },
    (async () => new Response(JSON.stringify(page()), { status: 200 })) as unknown as typeof fetch,
  );
}

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the application heading", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify(page()), { status: 200 })),
    );
    render(<App />);
    expect(screen.getByRole("heading", { name: /nyc mesh inventory/i })).toBeInTheDocument();
  });

  it("opens on the catalogue, which is the path that needs no camera", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              count: 1,
              results: [
                {
                  id: 1,
                  name: "LiteBeam",
                  category: 1,
                  unit_of_measure: "each",
                  minimum_stock: "0.000",
                  reorder_quantity: "1.000",
                  active: true,
                  balances: [],
                  labels: [],
                },
              ],
            }),
            { status: 200 },
          ),
      ),
    );
    render(<App />);
    expect(await screen.findByRole("heading", { name: "LiteBeam" })).toBeInTheDocument();
  });
});

/**
 * PROVISIONAL, with the enrolment gate: inventory-tng-81f7.4 removes both.
 *
 * What is asserted here is what `App.tsx` decides and no component of its own
 * does -- which parts of the screen sit inside the gate and which sit outside
 * it. Both were wrong once, and neither is visible from either component.
 */
describe("behind the enrolment gate", () => {
  beforeEach(() => {
    nothingQueued();
  });

  afterEach(() => {
    forgetWhatWasStubbed();
    vi.unstubAllGlobals();
  });

  it("starts the label map without waiting to be let through the gate", async () => {
    // `Prefetched` in App.tsx says why it sits outside. The cost of the other
    // side of that trade was paid by every deployment, including every one
    // that sets no posture at all.
    deployment("self");

    render(<App />);
    await screen.findByRole("button", { name: /set this device up/i });

    expect(callsTo("/api/labels")).not.toHaveLength(0);
  });

  it("draws one heading, not the gate's and the app's", async () => {
    deployment("self");

    render(<App />);
    await screen.findByRole("button", { name: /set this device up/i });

    expect(screen.getAllByRole("heading", { name: /nyc mesh inventory/i })).toHaveLength(1);
  });

  it("still shows a batch this device is holding", async () => {
    // Outside the gate, deliberately; App.tsx is where that is argued.
    queueBatch(batch("key-1", "LiteBeam and Cat6 Outdoor"));
    deployment("self");

    render(<App />);

    expect(await screen.findByText(/LiteBeam and Cat6 Outdoor/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /set this device up/i })).toBeInTheDocument();
  });
});
