import { describe, expect, test } from "vitest";
import { type Event, MOST_SECONDS, Telemetry } from "../shared/telemetry";
import { DecoderUnavailable } from "./camera";
import {
  CameraReport,
  labelKind,
  MOST_LABELS,
  MOST_OPEN_SECONDS,
  outcomeOf,
} from "./camera-report";

/** A report on a clock the test moves, from `start`, and what it sent. */
function reporting(start = 0) {
  const clock = { now: start };
  const sent: Event[] = [];
  const telemetry = new Telemetry();
  telemetry.record = (event) => sent.push(event);
  let flushes = 0;
  telemetry.flush = () => {
    flushes += 1;
  };
  const report = new CameraReport(telemetry, () => clock.now);
  return { report, clock, sent, flushes: () => flushes };
}

const NONE = { item: 0, package: 0, place: 0, unknown: 0 };

describe("what a session reports", () => {
  test("opened, labels read by kind, closed: one report, sent at once", () => {
    const { report, clock, sent, flushes } = reporting();
    clock.now = 1_500;
    report.opened("ponyfill");
    for (const kind of ["item", "item", "package", "place", "unknown"]) {
      report.read(kind as "item");
    }
    clock.now = 61_500;
    report.closed();
    report.closed();
    expect(sent).toEqual([
      {
        kind: "camera_session",
        outcome: "opened",
        decoder: "ponyfill",
        open_seconds: 1.5,
        seconds: 61.5,
        labels: { item: 2, package: 1, place: 1, unknown: 1 },
      },
    ]);
    expect(flushes()).toBe(1);
  });

  test("refused: reported at once, with no decoder and no open time", () => {
    const { report, clock, sent } = reporting();
    clock.now = 4_000;
    report.refused("not_allowed");
    report.closed();
    expect(sent).toEqual([
      {
        kind: "camera_session",
        outcome: "not_allowed",
        decoder: "none",
        seconds: 4,
        labels: NONE,
      },
    ]);
  });

  test("hidden with the camera open: reported, and shown again, resumed", () => {
    const { report, clock, sent } = reporting();
    report.opened("native");
    report.read("item");
    clock.now = 10_000;
    // pagehide follows visibilitychange: one report for both.
    report.hidden();
    report.hidden();
    // Hidden, nothing is counted.
    report.read("item");
    clock.now = 70_000;
    report.shown();
    report.read("place");
    clock.now = 100_000;
    report.closed();
    expect(sent).toEqual([
      expect.objectContaining({
        outcome: "opened",
        seconds: 10,
        labels: { ...NONE, item: 1 },
      }),
      {
        kind: "camera_session",
        outcome: "resumed",
        decoder: "native",
        seconds: 30,
        labels: { ...NONE, place: 1 },
      },
    ]);
  });

  test("hidden before the camera opened, or after it closed: nothing more", () => {
    const { report, sent } = reporting();
    report.hidden();
    report.shown();
    report.opened("native");
    report.closed();
    report.hidden();
    report.shown();
    report.closed();
    expect(sent.map((event) => "outcome" in event && event.outcome)).toEqual([
      "opened",
    ]);
  });

  test("closed while the camera was opening: cancelled, not refused", () => {
    const { report, clock, sent } = reporting();
    clock.now = 800;
    report.cancelled();
    report.closed();
    expect(sent).toEqual([
      {
        kind: "camera_session",
        outcome: "cancelled",
        decoder: "none",
        seconds: 0.8,
        labels: NONE,
      },
    ]);
  });

  test("a clock put back is sent as no time, never below zero", () => {
    const { report, clock, sent } = reporting(5_000);
    clock.now = 1_000;
    report.opened("native");
    report.closed();
    expect(sent).toEqual([
      expect.objectContaining({ open_seconds: 0, seconds: 0 }),
    ]);
  });

  test("numbers past what the backend takes are sent as its most", () => {
    const { report, clock, sent } = reporting();
    clock.now = (MOST_OPEN_SECONDS + 1) * 1000;
    report.opened("native");
    for (let i = 0; i <= MOST_LABELS; i++) {
      report.read("unknown");
    }
    clock.now = (MOST_SECONDS + MOST_OPEN_SECONDS + 1) * 1000;
    report.closed();
    expect(sent).toEqual([
      expect.objectContaining({
        open_seconds: MOST_OPEN_SECONDS,
        seconds: MOST_SECONDS,
        labels: { ...NONE, unknown: MOST_LABELS },
      }),
    ]);
  });
});

test("a package's label counts apart from its item's", () => {
  expect(labelKind({ kind: "item", itemId: 1 })).toBe("item");
  expect(labelKind({ kind: "item", itemId: 1, packageId: 2 })).toBe("package");
  expect(labelKind({ kind: "place", locationId: 3 })).toBe("place");
  expect(labelKind({ kind: "unknown", code: "B3ZT9W" })).toBe("unknown");
});

test.each([
  [new DecoderUnavailable("offline"), "decoder_failed"],
  [new DOMException("no", "NotAllowedError"), "not_allowed"],
  [new DOMException("no", "SecurityError"), "not_allowed"],
  [new DOMException("none", "NotFoundError"), "not_found"],
  [new DOMException("none", "OverconstrainedError"), "not_found"],
  [new DOMException("busy", "NotReadableError"), "in_use"],
  [new DOMException("busy", "AbortError"), "in_use"],
  [new DOMException("?", "TypeError"), "other"],
  [new Error("?"), "other"],
])("%s is said as %s", (error, outcome) => {
  expect(outcomeOf(error)).toBe(outcome);
});

test("off HTTPS and localhost, the camera is refused as insecure", () => {
  const secure = Object.getOwnPropertyDescriptor(globalThis, "isSecureContext");
  Object.defineProperty(globalThis, "isSecureContext", {
    value: false,
    configurable: true,
  });
  try {
    expect(outcomeOf(new TypeError("no mediaDevices"))).toBe("insecure");
  } finally {
    if (secure === undefined) {
      Reflect.deleteProperty(globalThis, "isSecureContext");
    } else {
      Object.defineProperty(globalThis, "isSecureContext", secure);
    }
  }
});
