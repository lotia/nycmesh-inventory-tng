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

/** The scope these spans arrive under. */
export const TRACER = "inventory.frontend.errors";

/** What a thrown value comes to when it is not an `Error`. */
function thrown(reason: unknown): Error {
  return reason instanceof Error ? reason : new Error(String(reason));
}

/**
 * Record one failure as a span of its own.
 *
 * Its own span rather than an event on whatever is current, because at the
 * moment `window.onerror` fires there may be nothing current at all -- the
 * stack that threw has already unwound.
 */
export function report(reason: unknown, kind: string): void {
  const failure = thrown(reason);
  const span = trace.getTracer(TRACER).startSpan(kind);
  span.recordException(failure);
  span.setStatus({ code: SpanStatusCode.ERROR, message: failure.message });
  span.end();
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
    report(event.error ?? event.message, "window.onerror");
  const onRejection = (event: PromiseRejectionEvent): void =>
    report(event.reason, "unhandledrejection");
  target.addEventListener("error", onError);
  target.addEventListener("unhandledrejection", onRejection);
  return () => {
    target.removeEventListener("error", onError);
    target.removeEventListener("unhandledrejection", onRejection);
  };
}
