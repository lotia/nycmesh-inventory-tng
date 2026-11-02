import { describe, expect, test } from "vitest";
import { REFUSALS, register } from "./registration";
import { answering, json } from "./test-fetch";

describe("register", () => {
  test("posts the phrase as JSON to /api/devices", async () => {
    const { api, sent } = answering(() => json({ device_id: 7 }, 201));
    expect(await register(api, "a-b-c-d-e")).toEqual({
      kind: "registered",
      deviceId: 7,
    });
    const [request] = sent;
    expect(request?.method).toBe("POST");
    expect(new URL(request?.url ?? "").pathname).toBe("/api/devices");
    expect(request?.headers.get("Content-Type")).toBe("application/json");
    expect(await request?.json()).toEqual({ code: "a-b-c-d-e" });
  });

  test.each(Object.keys(REFUSALS))(
    "a refusal for %s says why",
    async (reason) => {
      const status = reason === "rate_limited" ? 429 : 400;
      const { api } = answering(() => json({ reason }, status));
      expect(await register(api, "x")).toEqual({ kind: "refused", reason });
    },
  );

  test("a 503 from the database is worth trying again", async () => {
    const { api } = answering(() =>
      json({ reason: "database_unavailable" }, 503),
    );
    expect(await register(api, "x")).toEqual({ kind: "unavailable" });
  });

  test("a 503 from a proxy is too", async () => {
    const { api } = answering(
      () => new Response("<html>Service Unavailable</html>", { status: 503 }),
    );
    expect(await register(api, "x")).toEqual({ kind: "unavailable" });
  });

  test.each([
    [
      "a proxy's error page",
      () => new Response("<html>502</html>", { status: 502 }),
    ],
    [
      "a reason this build does not know",
      () => json({ reason: "frozen" }, 400),
    ],
    ["a refusal with no reason", () => json({}, 400)],
    ["a rate limit as a 400", () => json({ reason: "rate_limited" }, 400)],
    ["a bad phrase as a 429", () => json({ reason: "unknown" }, 429)],
    ["a validation error", () => json({ detail: [{ type: "missing" }] }, 422)],
    [
      "a success that is not JSON",
      () => new Response("<html>ok</html>", { status: 201 }),
    ],
    ["a success with no body", () => new Response(null, { status: 201 })],
    ["a success without a device", () => json({ device_id: "7" }, 201)],
    ["a status nobody declared", () => json({ device_id: 7 }, 418)],
  ])("%s is a failure, not a crash", async (_name, reply) => {
    const { api } = answering(reply);
    expect(await register(api, "x")).toEqual({ kind: "failed" });
  });

  test.each(["", "   ", "\n\t"])(
    "a blank phrase (%j) is not sent",
    async (typed) => {
      const { api, sent } = answering(() => json({ device_id: 1 }, 201));
      expect(await register(api, typed)).toEqual({ kind: "blank" });
      expect(sent).toEqual([]);
    },
  );

  test("no answer at all is a failure", async () => {
    const { api } = answering(() => {
      throw new TypeError("Failed to fetch");
    });
    expect(await register(api, "x")).toEqual({ kind: "failed" });
  });
});
