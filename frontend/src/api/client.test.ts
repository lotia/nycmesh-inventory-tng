import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiGet, apiPost } from "./client";

function answers(body: unknown, init: ResponseInit = {}): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200, ...init })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiGet", () => {
  it("returns the parsed body of an answer", async () => {
    answers({ count: 0, results: [] });
    await expect(apiGet("/api/items")).resolves.toEqual({ count: 0, results: [] });
  });

  it("asks for JSON, on the origin the app was loaded from", async () => {
    answers({});
    await apiGet("/api/items");
    expect(fetch).toHaveBeenCalledWith(
      "/api/items",
      expect.objectContaining({ headers: { Accept: "application/json" } }),
    );
  });

  it("prefers the sentence the API refused with over one of its own", async () => {
    answers({ detail: "This operation is reserved for administrators." }, { status: 403 });
    await expect(apiGet("/api/items")).rejects.toMatchObject({
      status: 403,
      message: "This operation is reserved for administrators.",
    });
  });

  it("still says something when a refusal carries no sentence", async () => {
    answers({ name: ["This field is required."] }, { status: 400 });
    await expect(apiGet("/api/items")).rejects.toMatchObject({ status: 400, message: /400/ });
  });

  it("carries the refused body, so a caller can render more than the sentence", async () => {
    answers({ detail: "no", errors: [{ field: "name" }] }, { status: 400 });
    await expect(apiGet("/api/items")).rejects.toMatchObject({
      body: { detail: "no", errors: [{ field: "name" }] },
    });
  });

  it("does not fall over on a failure that is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>502</html>", { status: 502 })),
    );
    await expect(apiGet("/api/items")).rejects.toMatchObject({ status: 502 });
  });

  it("reports a service it could not reach at all, rather than throwing a TypeError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const error = await apiGet("/api/items").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).offline).toBe(true);
  });

  it("lets an abort through as an abort, because the caller asked for it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new DOMException("aborted", "AbortError");
      }),
    );
    await expect(apiGet("/api/items")).rejects.toBeInstanceOf(DOMException);
  });
});

describe("apiPost", () => {
  it("sends JSON and hands back what was created", async () => {
    answers({ id: 8, display_name: "Olivia" }, { status: 201 });
    await expect(apiPost("/api/volunteers", { display_name: "Olivia" })).resolves.toEqual({
      id: 8,
      display_name: "Olivia",
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/volunteers",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ display_name: "Olivia" }) }),
    );
  });

  it("returns the CSRF token Django set, because a write without it is refused", async () => {
    // The cookie Django set on the API index, as the browser hands it back.
    // biome-ignore lint/suspicious/noDocumentCookie: writing it is how a test arranges one.
    document.cookie = "csrftoken=abc123";
    answers({}, { status: 201 });
    await apiPost("/api/volunteers", {});
    expect(fetch).toHaveBeenCalledWith(
      "/api/volunteers",
      expect.objectContaining({ headers: expect.objectContaining({ "X-CSRFToken": "abc123" }) }),
    );
  });

  it("carries a refused body, because a 409 is a thing to render", async () => {
    answers({ code: "volunteer_merged", volunteer: { id: 7 } }, { status: 409 });
    await expect(apiPost("/api/volunteers", {})).rejects.toMatchObject({
      status: 409,
      body: { code: "volunteer_merged" },
    });
  });

  it("reports a service it could not reach at all", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("Failed to fetch");
      }),
    );
    const error = await apiPost("/api/volunteers", {}).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).offline).toBe(true);
  });

  it("lets an abort through as an abort", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new DOMException("aborted", "AbortError");
      }),
    );
    await expect(apiPost("/api/volunteers", {})).rejects.toBeInstanceOf(DOMException);
  });
});
