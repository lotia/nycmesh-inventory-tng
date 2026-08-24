/**
 * The two things a browser tells you about a failure nobody caught.
 *
 * `window.onerror` for a throw that reached the top, and `unhandledrejection`
 * for a promise nobody handled -- which in this app is most of them, because
 * every API call is one. Together they are the whole of what the frontend
 * could ever have said about its own failures, and until now it said none of
 * it: the single `console.error` in the decode loop runs on a volunteer's
 * phone and is read by nobody.
 *
 * RECORDED AS A SPAN, not a log line. There is no log pipeline in the browser
 * -- decision 0021's "logs go to standard output" has no meaning on a phone --
 * so a span carrying an exception event is the shape that reaches a collector,
 * and it lands in the same trace as whatever the volunteer was doing.
 *
 * WHAT IS NOT SENT is anything either handler hands over beyond the message
 * and the stack: no URL, no user agent, no element. `redaction` on the backend
 * is deny-by-default for the same reason and this is the browser's half of it,
 * though nothing here can enforce what somebody puts in an error message.
 */

import { SpanStatusCode, trace } from "@opentelemetry/api";

import { failed, type Kind, thrown } from "./report";

/** The scope these spans arrive under. */
export const TRACER = "inventory.frontend.errors";

/**
 * Record one failure as a span of its own.
 *
 * Its own span rather than an event on whatever is current, because at the
 * moment `window.onerror` fires there may be nothing current at all -- the
 * stack that threw has already unwound.
 */
export function report(reason: unknown, kind: Kind): void {
  const failure = thrown(reason);
  const span = trace.getTracer(TRACER).startSpan(kind);
  span.recordException(failure);
  span.setStatus({ code: SpanStatusCode.ERROR, message: failure.message });
  span.end();
}

/**
 * Both halves, for a failure that reached the top of the stack.
 *
 * The span is for a session somebody asked about; the report is for every
 * session, and `telemetry/report.ts` argues that asymmetry. Here they are the
 * same event, so it is one call rather than two at each listener.
 */
export function caught(reason: unknown, kind: Kind): void {
  report(reason, kind);
  failed(reason, "app", kind);
}

/**
 * Listen for both, and hand back the way to stop.
 *
 * Neither handler returns anything and neither swallows: the browser still
 * logs to its own console, which is what a developer with the phone in their
 * hand is reading.
 */
export function watch(target: Window = window): () => void {
  const onError = (event: ErrorEvent): void =>
    caught(event.error ?? event.message, "window.onerror");
  const onRejection = (event: PromiseRejectionEvent): void =>
    caught(event.reason, "unhandledrejection");
  target.addEventListener("error", onError);
  target.addEventListener("unhandledrejection", onRejection);
  return () => {
    target.removeEventListener("error", onError);
    target.removeEventListener("unhandledrejection", onRejection);
  };
}
