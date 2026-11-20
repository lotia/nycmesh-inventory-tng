/**
 * What one session of the scan page's camera reports (inventory-tng-8i4.63,
 * docs/decisions/0006): how it ended, the decoder that read, how long the
 * camera took to open, how long it stayed open, and the labels it read by
 * kind. Nothing names a label, an item or a place.
 *
 * One report a session, sent at once as the camera closes or is refused,
 * and as the page is hidden or left with it open, since a page may not come
 * back. Shown again with the camera open, what follows is a session of its
 * own, `resumed`.
 */
import type { Schemas } from "../api/client";
import { type Telemetry, telemetry } from "../shared/telemetry";
import { DecoderUnavailable } from "./camera";
import type { DecoderKind } from "./decoder";
import type { Scanned } from "./label-codes";

type CameraSession = Schemas["CameraSession"];
export type Outcome = CameraSession["outcome"];
export type LabelKind = keyof Schemas["LabelsRead"];

/** The most of each the backend takes (page_telemetry_api.CameraSession):
 * a number past one would have the whole report refused. */
export const MOST_OPEN_SECONDS = 120;
export const MOST_SECONDS = 86_400;
export const MOST_LABELS = 10_000;

/** What a label read names, as counted: a package's own label apart from
 * its item's. */
export function labelKind(read: Scanned): LabelKind {
  if (read.kind === "item") {
    return read.packageId === undefined ? "item" : "package";
  }
  return read.kind;
}

/** Why the camera could not be used, as the report says it. */
export function outcomeOf(error: unknown): Outcome {
  if (error instanceof DecoderUnavailable) {
    return "decoder_failed";
  }
  // No camera at all off HTTPS and localhost: mediaDevices is missing.
  if (globalThis.isSecureContext === false) {
    return "insecure";
  }
  if (!(error instanceof DOMException)) {
    return "other";
  }
  switch (error.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "not_allowed";
    case "NotFoundError":
    case "OverconstrainedError":
      return "not_found";
    case "NotReadableError":
    case "AbortError":
      return "in_use";
    default:
      return "other";
  }
}

type Unsent = Omit<CameraSession, "kind" | "decoder" | "seconds"> & {
  from: number;
};

const none = (): Schemas["LabelsRead"] => ({
  item: 0,
  package: 0,
  place: 0,
  unknown: 0,
});

/** One camera session's report, from asking for the camera on. */
export class CameraReport {
  private readonly asked: number;
  private decoder: CameraSession["decoder"] = "none";
  /** What is not sent yet: nothing before the camera opens or is refused,
   * nor once sent. */
  private unsent: Unsent | null = null;
  /** Sent as the page was hidden with the camera open. */
  private hiddenOpen = false;

  constructor(
    private readonly to: Telemetry = telemetry,
    // Monotonic, so a clock put back never makes a time below zero, which
    // would have the whole report refused.
    private readonly now: () => number = () => performance.now(),
  ) {
    this.asked = now();
  }

  opened(decoder: DecoderKind): void {
    this.decoder = decoder;
    this.unsent = {
      outcome: "opened",
      from: this.asked,
      open_seconds: Math.min(this.since(this.asked), MOST_OPEN_SECONDS),
      labels: none(),
    };
  }

  refused(error: unknown): void {
    this.ended(outcomeOf(error));
  }

  /** Closed while the camera was opening, and so not opened: the browser
   * then says why it stopped, which is no refusal. */
  cancelled(): void {
    this.ended("cancelled");
  }

  read(kind: LabelKind): void {
    if (this.unsent !== null) {
      const { labels } = this.unsent;
      labels[kind] = Math.min(labels[kind] + 1, MOST_LABELS);
    }
  }

  /** The page hidden or left: sent now, as it may not come back. */
  hidden(): void {
    this.hiddenOpen ||= this.unsent !== null;
    this.send();
  }

  /** The page shown again: a session of its own, if the camera was open. */
  shown(): void {
    if (!this.hiddenOpen) {
      return;
    }
    this.hiddenOpen = false;
    this.unsent = { outcome: "resumed", from: this.now(), labels: none() };
  }

  closed(): void {
    this.hiddenOpen = false;
    this.send();
  }

  private ended(outcome: Outcome): void {
    this.unsent = { outcome, from: this.asked, labels: none() };
    this.send();
  }

  private send(): void {
    if (this.unsent === null) {
      return;
    }
    const { from, ...rest } = this.unsent;
    this.unsent = null;
    this.to.record({
      kind: "camera_session",
      ...rest,
      decoder: this.decoder,
      seconds: Math.min(this.since(from), MOST_SECONDS),
    });
    this.to.flush();
  }

  private since(from: number): number {
    return Math.max(this.now() - from, 0) / 1000;
  }
}
