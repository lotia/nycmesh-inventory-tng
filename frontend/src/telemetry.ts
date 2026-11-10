/**
 * What the pages tell the backend about themselves (docs/decisions/0006):
 * errors, three Web Vitals, the outbox's retries, and how long a batch took
 * from its first line to saving. No OpenTelemetry here: events go to
 * POST /api/telemetry on this origin, whose schema is the list of what may
 * be sent, and the backend turns them into metrics. Nothing names a
 * volunteer, an item or a place, and an error sends only that one happened.
 *
 * Events wait in memory and go together: when ten are waiting, and when the
 * page is hidden or left, by sendBeacon, which outlives the page. Each kind
 * has its own budget an hour (PER_HOUR), so a loop of errors cannot flood
 * the server, retries while offline cannot crowd out the rest, and a scan
 * page left open all day still reports.
 * Nothing is sent until start(), which only the pages' entry points call.
 */
import { onCLS, onINP, onLCP } from "web-vitals";
import type { Schemas } from "./api/client";

export const PATH = "/api/telemetry";
export const FLUSH_AT = 10;
/** How many of each kind a page sends an hour: 50 in all, under the
 * server's limit (page_telemetry.REPORTS). */
export const PER_HOUR: Record<Event["kind"], number> = {
  web_vital: 10,
  error: 10,
  outbox_retry: 10,
  scan_to_save: 20,
};
const HOUR_MS = 3_600_000;
/** The longest scan_to_save the backend takes (page_telemetry.ScanToSave):
 * an event outside its bounds would have the whole report refused. */
export const MOST_SECONDS_TO_SAVE = 86_400;

/** As POST /api/telemetry takes them: its schema is the list. */
export type Event = Schemas["Report"]["events"][number];
type Page = Schemas["PageError"]["page"];
type Send = (body: Blob) => void;

/** By sendBeacon, or by a fetch that outlives the page where there is none;
 * a report that cannot go is dropped. */
function beacon(body: Blob): void {
  if (navigator.sendBeacon?.(PATH, body)) {
    return;
  }
  void fetch(PATH, { method: "POST", body, keepalive: true }).catch(() => {});
}

export class Telemetry {
  private page: Page | null = null;
  private waiting: Event[] = [];
  /** This hour's events of each kind, and when the hour began. */
  private recorded = new Map<Event["kind"], number>();
  private hourFrom = 0;

  constructor(
    private readonly send: Send = beacon,
    private readonly now: () => number = Date.now,
  ) {}

  /** Report from now on, as `page`; what was recorded before is dropped.
   * Returns what stops listening to the page (the Web Vitals aside, which
   * report once each). */
  start(page: Page, target: Window = window): () => void {
    this.page = page;
    // Each of LCPMetric, INPMetric and CLSMetric is one of these.
    const vital = ({
      name,
      value,
      rating,
    }: Omit<Schemas["WebVital"], "kind" | "page">) =>
      this.record({ kind: "web_vital", page, name, value, rating });
    onLCP(vital);
    onINP(vital);
    onCLS(vital);
    const listening = new AbortController();
    const { signal } = listening;
    for (const source of ["error", "unhandledrejection"] as const) {
      target.addEventListener(
        source,
        () => this.record({ kind: "error", page, source }),
        { signal },
      );
    }
    target.document.addEventListener(
      "visibilitychange",
      () => {
        if (target.document.visibilityState === "hidden") {
          this.flush();
        }
      },
      { signal },
    );
    target.addEventListener("pagehide", () => this.flush(), { signal });
    return () => listening.abort();
  }

  record(event: Event): void {
    if (this.page === null) {
      return;
    }
    const now = this.now();
    if (now - this.hourFrom >= HOUR_MS) {
      this.recorded.clear();
      this.hourFrom = now;
    }
    const recorded = this.recorded.get(event.kind) ?? 0;
    if (recorded >= PER_HOUR[event.kind]) {
      return;
    }
    this.recorded.set(event.kind, recorded + 1);
    this.waiting.push(event);
    if (this.waiting.length >= FLUSH_AT) {
      this.flush();
    }
  }

  flush(): void {
    if (this.waiting.length === 0) {
      return;
    }
    const events = this.waiting;
    this.waiting = [];
    this.send(
      new Blob([JSON.stringify({ events })], { type: "application/json" }),
    );
  }
}

/** The one every page on this browser reports through. */
export const telemetry = new Telemetry();
