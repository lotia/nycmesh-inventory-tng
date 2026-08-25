import { afterEach, describe, expect, it, vi } from "vitest";
import { HEADER, remember } from "../telemetry/flag";
import { ApiError, apiGet, apiPost } from "./client";

function answers(body: unknown, init: ResponseInit = {}): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(body), { status: 200, ...init })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  // HERE RATHER THAN AT THE END OF THE TEST THAT SETS IT. A failing assertion
  // aborts before the last statement, so a `forget()` written there would
  // leave the debug token set for every later test -- and `apiGet`'s first
  // case deep-equals the headers, so one real failure would cascade into
  // unrelated red ones that hid it. `telemetry.test.tsx` makes the same
  // argument about the same mistake with `vi.unstubAllGlobals` above.
  window.localStorage.clear();
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

  it("reads a field-keyed refusal, which is what a rejected write actually sends", async () => {
    // The commonest refusal there is: `Item.name` is unique, and DRF answers a
    // field's own validator with a map and no `detail` at all. This used to
    // come out as the number 400 and nothing else.
    answers({ name: ["item with this name already exists."] }, { status: 400 });
    await expect(apiGet("/api/items")).rejects.toMatchObject({
      status: 400,
      message: "Name: item with this name already exists.",
    });
  });

  it("names the field the way somebody has to go back to it", async () => {
    answers(
      { minimum_stock: ["A minimum stock level is how little may be left."] },
      { status: 400 },
    );
    await expect(apiGet("/api/items")).rejects.toMatchObject({
      message: "Minimum stock: A minimum stock level is how little may be left.",
    });
  });

  it("says the complaint about the whole submission before the fields", async () => {
    // `validate()` raising a bare string is `non_field_errors`, and it is the
    // reason the write was refused; a field line is what to do about it.
    answers(
      { name: ["This field is required."], non_field_errors: ["Sean still holds a location."] },
      { status: 400 },
    );
    await expect(apiGet("/api/items")).rejects.toMatchObject({
      message: "Sean still holds a location. Name: This field is required.",
    });
  });

  it("keeps every message a field carries", async () => {
    answers({ code: ["Not that.", "Nor that."] }, { status: 400 });
    await expect(apiGet("/api/items")).rejects.toMatchObject({
      message: "Code: Not that. Nor that.",
    });
  });

  it("reads a bare list, which is what a top-level refusal sends", async () => {
    answers(["Nothing doing."], { status: 400 });
    await expect(apiGet("/api/items")).rejects.toMatchObject({ message: "Nothing doing." });
  });

  it("leaves the batch endpoint's own shape to the batch endpoint", async () => {
    // `detail` wins, so the per-line map under `movements` is never flattened
    // into the sentence -- SubmitBar renders those lines itself.
    answers(
      { detail: "Nothing was saved.", errors: [{ index: 0, field: "item", detail: "no" }] },
      { status: 400 },
    );
    await expect(apiGet("/api/stock/transactions")).rejects.toMatchObject({
      message: "Nothing was saved.",
    });
  });

  it("still says something when a refusal carries nothing this can read", async () => {
    answers({ movements: { 0: { item: "gone" } } }, { status: 400 });
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

  it("carries the debug token while this device is being recorded, and not otherwise", async () => {
    // The propagation half of the debug flag: a volunteer an administrator
    // handed a link to has their requests recorded in full, and the backend
    // reads that off this header. Asserted here as well as on the failure
    // report, because they are two call sites of one helper and only one of
    // them was covered -- so `flag.debugHeaders` could have returned nothing
    // and this path would not have noticed.
    answers({}, { status: 200 });
    await apiGet("/api/items");
    expect(fetch).toHaveBeenCalledWith(
      "/api/items",
      expect.not.objectContaining({
        headers: expect.objectContaining({ [HEADER]: expect.anything() }),
      }),
    );

    remember("a-signed-token");
    answers({}, { status: 200 });
    await apiGet("/api/items");
    // Read through `Headers` rather than as a plain object: the recording path
    // normalises, which is what makes a caller's own header shape safe.
    const [, init] = vi.mocked(fetch).mock.lastCall as unknown as [string, RequestInit];
    expect(new Headers(init.headers).get(HEADER)).toBe("a-signed-token");
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
