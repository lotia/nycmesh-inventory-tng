import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the application heading", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ count: 0, results: [] }), { status: 200 })),
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
