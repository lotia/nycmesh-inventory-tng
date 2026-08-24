/**
 * What this app says about a failure, and where that goes.
 *
 * THE ASYMMETRY IS THE POINT, and it is written here because it is exactly the
 * kind of thing a later change removes without noticing. Browser telemetry
 * costs a volunteer's battery and their data, on a phone in a basement. So:
 *
 * - **Spans are for a session somebody asked about.** They are the detail --
 *   which step, in what order, how long -- and they are worth what they cost
 *   only when somebody is looking. With no debug token there is no SDK at all,
 *   so `trace()` below records nothing.
 *
 * - **Failures are always reported.** They are rare, they are small, and they
 *   are the only account anybody will ever get of what happens on a
 *   volunteer's phone. Where they go, and why it is not the collector, is
 *   docs/observability.md; decision 0012 is why that endpoint may exist.
 *
 * Anything added here should be able to say which of those two it is.
 */

import { trace as otel, type Span, SpanStatusCode } from "@opentelemetry/api";

import { csrfToken } from "../api/csrf";
import { asked, HEADER } from "./flag";

/** The debug header, when this device is being recorded, and nothing when not. */
function recording(): Record<string, string> {
  const token = asked();
  return token === null ? {} : { [HEADER]: token };
}

/** Where a failure is reported. Rate limited, and it stores nothing. */
export const FAILURES = "/api/client-failures";

/** The scope these spans arrive under. */
export const TRACER = "inventory.frontend";

/**
 * What the app was doing, in its own words. A metric groups by these.
 *
 * BOTH THIS AND `Kind` ARE CLOSED SETS, and the backend holds the same two --
 * `ClientFailureSerializer.DOING` and `.KINDS`. Not belt and braces: a union
 * is erased at runtime, so it cannot be what stops a caller minting a time
 * series on a credential-free endpoint, and a `ChoiceField` cannot be what
 * stops this application posting a value it will refuse. A backend test holds
 * the two lists against each other, because they went out of step once: three
 * of the four call sites here sent a `kind` the serializer did not admit, and
 * every one of those reports was answered 400 and dropped in silence.
 */
export type Doing = "scan" | "outbox" | "print" | "api" | "app";

/**
 * Why a failure is being reported: one of the browser's two handlers, or one
 * of the three this application notices itself.
 */
export type Kind =
  | "window.onerror"
  | "unhandledrejection"
  | "decode-loop"
  | "refused"
  | "server-error";

/**
 * What a thrown value comes to when it is not an `Error`.
 *
 * Here rather than in `errors.ts`, which is where it started: this module has
 * the two span wrappers below and `errors.ts` already imports from it, so the
 * dependency runs the right way and the coercion is written once instead of
 * three times.
 */
export function thrown(reason: unknown): Error {
  return reason instanceof Error ? reason : new Error(String(reason));
}

/**
 * Mark this span as the failure it met, and let the failure carry on.
 *
 * Shared by both wrappers below: what a failed span records is one decision,
 * and it was written out twice. Neither handles the failure -- they watch a
 * step, so whatever was thrown is re-thrown.
 */
function failing(span: Span, reason: unknown): never {
  span.recordException(thrown(reason));
  span.setStatus({ code: SpanStatusCode.ERROR });
  throw reason;
}

/** As much of a failure as is worth sending: the message, and a short stack. */
export function described(reason: unknown): string {
  const said = reason instanceof Error ? (reason.stack ?? reason.message) : String(reason);
  return said.slice(0, 2000);
}

/**
 * Report a failure, whatever this session is or is not recording.
 *
 * Never throws and never awaits into the caller: a report that failed must not
 * become the failure. `keepalive` so it still goes when the page is closing,
 * which is when a scanner crash tends to be noticed.
 */
export function failed(reason: unknown, where: Doing, kind: Kind = "unhandledrejection"): void {
  try {
    void fetch(FAILURES, {
      method: "POST",
      // THE SAME TWO HEADERS EVERY OTHER WRITE CARRIES, and leaving them off
      // made this endpoint unreachable from the browsers most likely to need
      // it. `fetch` sends the session cookie by default, and DRF's
      // `SessionAuthentication` enforces CSRF the moment it finds a user --
      // regardless of `AllowAny` -- so a report from a signed-in
      // administrator was refused 403 and thrown away without a trace. The
      // debug token is the other: without it a report from a device somebody
      // is actively recording cannot be tied to the trace that explains it.
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": csrfToken(),
        ...recording(),
      },
      body: JSON.stringify({ kind, where, detail: described(reason) }),
      keepalive: true,
    }).catch(() => {});
  } catch {
    // A browser that refused the request outright. There is nowhere else to
    // say so, and saying it in the console is what this replaced.
  }
}

/**
 * Record one step as a span, when this session is being recorded.
 *
 * Cheap when it is not: the API's own tracer hands back a non-recording span,
 * which is the browser's equivalent of the guard `inventory/tracing.py`
 * measured on the backend. An exception is recorded on the span and re-raised
 * -- this watches a step, it does not handle it.
 */
export async function traced<T>(name: string, step: () => Promise<T>): Promise<T> {
  return otel.getTracer(TRACER).startActiveSpan(name, async (span) => {
    try {
      return await step();
    } catch (reason) {
      failing(span, reason);
    } finally {
      span.end();
    }
  });
}

/** The same, for a step that is not a promise. */
export function trace<T>(name: string, step: () => T): T {
  return otel.getTracer(TRACER).startActiveSpan(name, (span) => {
    try {
      return step();
    } catch (reason) {
      failing(span, reason);
    } finally {
      span.end();
    }
  });
}
