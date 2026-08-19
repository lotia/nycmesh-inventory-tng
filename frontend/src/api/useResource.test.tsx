import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useResource } from "./useResource";

function Reader({ path }: { path: string }) {
  const { data, error, loading } = useResource<{ name: string }>(path);
  return (
    <>
      <output aria-label="state">{loading ? "loading" : "settled"}</output>
      <output aria-label="data">{data?.name ?? ""}</output>
      <output aria-label="error">{error?.message ?? ""}</output>
    </>
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useResource", () => {
  it("reports what it read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ name: "LiteBeam" }), { status: 200 })),
    );
    render(<Reader path="/api/items" />);
    await waitFor(() => expect(screen.getByLabelText("data")).toHaveTextContent("LiteBeam"));
    expect(screen.getByLabelText("state")).toHaveTextContent("settled");
  });

  it("reports a refusal as something the screen can draw", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "Nope." }), { status: 403 })),
    );
    render(<Reader path="/api/items" />);
    await waitFor(() => expect(screen.getByLabelText("error")).toHaveTextContent("Nope."));
  });

  it("keeps the answer it has while reading the next one", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (path: string) =>
          new Response(JSON.stringify({ name: path.includes("b") ? "B" : "A" }), { status: 200 }),
      ),
    );
    const { rerender } = render(<Reader path="/api/items?search=a" />);
    await waitFor(() => expect(screen.getByLabelText("data")).toHaveTextContent("A"));
    rerender(<Reader path="/api/items?search=b" />);
    await waitFor(() => expect(screen.getByLabelText("data")).toHaveTextContent("B"));
  });

  it("does not overwrite a newer read with an older one it abandoned", async () => {
    const controllers: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string, init: RequestInit) => {
        if (init.signal) {
          controllers.push(init.signal);
        }
        return new Response(JSON.stringify({ name: path }), { status: 200 });
      }),
    );
    const { rerender, unmount } = render(<Reader path="/api/items?search=a" />);
    rerender(<Reader path="/api/items?search=b" />);
    await waitFor(() => expect(screen.getByLabelText("data")).toHaveTextContent("search=b"));
    unmount();
    expect(controllers[0].aborted).toBe(true);
  });

  it("says nothing when a read it had already abandoned finally fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (path: string, init: RequestInit) =>
          new Promise<Response>((resolve, reject) => {
            if (path.includes("slow")) {
              init.signal?.addEventListener("abort", () =>
                reject(new DOMException("aborted", "AbortError")),
              );
              return;
            }
            resolve(new Response(JSON.stringify({ name: "quick" }), { status: 200 }));
          }),
      ),
    );
    const { rerender } = render(<Reader path="/api/items?search=slow" />);
    rerender(<Reader path="/api/items?search=quick" />);
    await waitFor(() => expect(screen.getByLabelText("data")).toHaveTextContent("quick"));
    expect(screen.getByLabelText("error")).toHaveTextContent("");
  });

  it("wraps a failure that is not the API's into one a screen can render", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => {
        throw new Error("boom");
      }),
    );
    render(<Reader path="/api/items" />);
    await waitFor(() => expect(screen.getByLabelText("error")).not.toHaveTextContent(""));
  });
});
