/**
 * What the pages tell the backend about themselves (docs/decisions/0006):
 * errors, three Web Vitals, the outbox's retries, and how long a batch took
 * from its first line to saving. No OpenTelemetry here: events go to
 * POST /api/telemetry on this origin, whose schema is the list of what may
 * be sent, and the backend turns them into metrics. Nothing names a
 * volunteer, an item or a place, and an error sends only that one happened.
 *
 * Events wait in memory and go together: when ten are waiting, and when the
 * page is hidden or left, by sendBeacon, which outlives the page. A page
 * sends at most MOST_PER_PAGE, so a loop of errors cannot flood the server.
 * Nothing is sent until start(), which only the pages' entry points call.
 */
import { onCLS, onINP, onLCP } from "web-vitals";
import type { Schemas } from "./api/client";

export const PATH = "/api/telemetry";
export const FLUSH_AT = 10;
export const MOST_PER_PAGE = 50;
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
  private recorded = 0;

  constructor(private readonly send: Send = beacon) {}

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
    if (this.page === null || this.recorded >= MOST_PER_PAGE) {
      return;
    }
    this.recorded += 1;
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
