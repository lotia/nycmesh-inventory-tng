import { afterEach, describe, expect, test, vi } from "vitest";

// Each callback the page gives web-vitals, by the vital it reports.
const vitals = vi.hoisted(() => new Map<string, (metric: object) => void>());
vi.mock("web-vitals", () => ({
  onLCP: (report: (metric: object) => void) => vitals.set("LCP", report),
  onINP: (report: (metric: object) => void) => vitals.set("INP", report),
  onCLS: (report: (metric: object) => void) => vitals.set("CLS", report),
}));

import { type Event, FLUSH_AT, PATH, PER_HOUR, Telemetry } from "./telemetry";

const RETRY: Event = { kind: "outbox_retry", reason: "no_answer" };

/** What stops each test's Telemetry listening, so no other test's events
 * reach it. */
const stops: (() => void)[] = [];
afterEach(() => {
  for (const stop of stops.splice(0)) {
    stop();
  }
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function started(telemetry: Telemetry, page: "scan" | "register" = "scan") {
  stops.push(telemetry.start(page));
  return telemetry;
}

/** A Telemetry whose reports are kept here, as the events in each. */
function kept(): { telemetry: Telemetry; reports: Promise<Event[]>[] } {
  const reports: Promise<Event[]>[] = [];
  const telemetry = new Telemetry((body) =>
    reports.push(body.text().then((text) => JSON.parse(text).events)),
  );
  return { telemetry, reports };
}

describe("reporting", () => {
  test("nothing is kept or sent before the page starts it", () => {
    const { telemetry, reports } = kept();
    telemetry.record(RETRY);
    telemetry.flush();
    expect(reports).toEqual([]);
  });

  test("events wait, and go together when enough are waiting", async () => {
    const { telemetry, reports } = kept();
    started(telemetry, "scan");
    for (let i = 1; i < FLUSH_AT; i++) {
      telemetry.record(RETRY);
    }
    expect(reports).toEqual([]);
    telemetry.record(RETRY);
    expect(reports).toHaveLength(1);
    expect(await reports[0]).toHaveLength(FLUSH_AT);
  });

  test("what waits goes when the page is hidden or left", async () => {
    const { telemetry, reports } = kept();
    started(telemetry, "register");
    telemetry.record(RETRY);
    // Shown again: nothing goes.
    document.dispatchEvent(new Event("visibilitychange"));
    expect(reports).toEqual([]);
    Object.defineProperty(document, "visibilityState", {
      value: "hidden",
      configurable: true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
    // Back to the document's own.
    Reflect.deleteProperty(document, "visibilityState");
    telemetry.record(RETRY);
    window.dispatchEvent(new Event("pagehide"));
    expect(await Promise.all(reports)).toEqual([[RETRY], [RETRY]]);
  });

  test("a page's errors are reported, saying only that one happened", async () => {
    const { telemetry, reports } = kept();
    started(telemetry, "scan");
    window.dispatchEvent(new Event("error"));
    window.dispatchEvent(new Event("unhandledrejection"));
    telemetry.flush();
    expect(await reports[0]).toEqual([
      { kind: "error", page: "scan", source: "error" },
      { kind: "error", page: "scan", source: "unhandledrejection" },
    ]);
  });

  test("each kind has its own share an hour, so none crowds out the rest", async () => {
    let now = 0;
    const reports: Promise<Event[]>[] = [];
    const telemetry = new Telemetry(
      (body) =>
        reports.push(body.text().then((text) => JSON.parse(text).events)),
      () => now,
    );
    started(telemetry);
    const error: Event = { kind: "error", page: "scan", source: "error" };
    // Offline all hour: retries use up their share, and only theirs.
    for (let i = 0; i < PER_HOUR.outbox_retry * 3; i++) {
      telemetry.record(RETRY);
    }
    telemetry.record(error);
    telemetry.flush();
    const kinds = async () =>
      (await Promise.all(reports)).flat().map((e) => e.kind);
    expect(
      (await kinds()).filter((kind) => kind === "outbox_retry"),
    ).toHaveLength(PER_HOUR.outbox_retry);
    expect(await kinds()).toContain("error");
    // An hour on, each kind has its share again.
    now += 3_600_000;
    telemetry.record(RETRY);
    telemetry.flush();
    expect(
      (await kinds()).filter((kind) => kind === "outbox_retry"),
    ).toHaveLength(PER_HOUR.outbox_retry + 1);
  });
});

test("each Web Vital is reported with its page and rating", async () => {
  const { telemetry, reports } = kept();
  started(telemetry, "register");
  vitals.get("LCP")?.({ name: "LCP", value: 900, rating: "good", id: "x" });
  vitals.get("INP")?.({ name: "INP", value: 300, rating: "needs-improvement" });
  vitals.get("CLS")?.({ name: "CLS", value: 0.3, rating: "poor" });
  telemetry.flush();
  expect(await reports[0]).toEqual([
    {
      kind: "web_vital",
      page: "register",
      name: "LCP",
      value: 900,
      rating: "good",
    },
    {
      kind: "web_vital",
      page: "register",
      name: "INP",
      value: 300,
      rating: "needs-improvement",
    },
    {
      kind: "web_vital",
      page: "register",
      name: "CLS",
      value: 0.3,
      rating: "poor",
    },
  ]);
});

describe("sending", () => {
  test("by sendBeacon, to this origin", () => {
    const beacon = vi.fn(() => true);
    vi.stubGlobal("navigator", { sendBeacon: beacon });
    const telemetry = new Telemetry();
    started(telemetry, "scan");
    telemetry.record(RETRY);
    telemetry.flush();
    expect(beacon).toHaveBeenCalledWith(PATH, expect.any(Blob));
  });

  test("by a fetch that outlives the page where there is no beacon", () => {
    const sent = vi.fn(() =>
      Promise.resolve(new Response(null, { status: 204 })),
    );
    vi.stubGlobal("navigator", {});
    vi.stubGlobal("fetch", sent);
    const telemetry = new Telemetry();
    started(telemetry, "scan");
    telemetry.record(RETRY);
    telemetry.flush();
    expect(sent).toHaveBeenCalledWith(
      PATH,
      expect.objectContaining({ method: "POST", keepalive: true }),
    );
  });
});
