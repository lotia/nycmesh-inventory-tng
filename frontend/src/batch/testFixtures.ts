/**
 * The scene the queue's tests share: a device with nothing on it, and a server
 * that answers however the test says.
 *
 * Three files drive the same queue -- the module, the panel that draws it and
 * the bar that fills it -- and each was carrying its own copy of the setup.
 * Two copies of "a fresh device" that drift are two files disagreeing about
 * what a test starts from. Declared once for the reason cart/testFixtures.ts
 * is.
 *
 * Responses are still built where they are used, one call at a time: what
 * a test is arranging is what the server said, and that is worth reading
 * literally.
 */
import { vi } from "vitest";
import { forgetOutbox, type QueuedBatch } from "./outbox";

/** The `fetch` a test drives. Installed by `stubFetch`. */
export const fetching = vi.fn();

/** A batch as the submit bar hands one over. */
export function batch(
  key = "key-1",
  what = "LiteBeam",
): Pick<QueuedBatch, "key" | "body" | "what"> {
  return {
    key,
    body: {
      idempotency_key: key,
      kind: "checkout",
      actor: 7,
      movements: [{ item: 1, quantity: 2 }],
    },
    what,
  };
}

/**
 * The server recording it, with whatever it wants to warn about.
 *
 * A fresh one per call, never a shared instance: a `Response` body can be read
 * once, so handing the same object to two attempts makes the second look like
 * an answer that is not JSON.
 */
export function recorded(warnings: { detail: string }[] = []): Response {
  return new Response(JSON.stringify({ id: 12, warnings }), { status: 201 });
}

/** The server answering, badly. */
export function answered(status: number, detail = "No."): Response {
  return new Response(JSON.stringify({ detail }), { status });
}

/** Nothing at the other end: what `fetch` does with no network. */
export function nothing(): Promise<never> {
  return Promise.reject(new TypeError("Failed to fetch"));
}

/** A device holding no batches, and a module remembering none. */
export function nothingQueued(): void {
  window.localStorage.clear();
  forgetOutbox();
}

/** `fetching` in place of the browser's own, answering nothing yet. */
export function stubFetch(): void {
  fetching.mockReset();
  vi.stubGlobal("fetch", fetching);
}

/** Undo whatever the test stubbed or spied on, however it ended. */
export function forgetWhatWasStubbed(): void {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
}
