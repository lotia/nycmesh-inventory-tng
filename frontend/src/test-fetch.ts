/** Fakes for the API, shared by the tests. Never bundled. */
import { makeApi } from "./api/client";

/** A JSON reply with this status. */
export const json = (body: unknown, status: number): Response =>
  Response.json(body, { status });

/** The batch endpoint saving a batch as this transaction. */
export const saved = (transactionId = 77): Response =>
  json({ transaction_id: transactionId }, 201);

/** The batch endpoint's answer to a batch from a build too old. */
export const TOO_OLD = {
  reason: "build_too_old",
  build: "2026-11-12T09:00:00.000Z",
  oldest: "2026-11-12T10:00:00.000Z",
  message: "Load the page again.",
};

/** An api whose fetch answers with `reply`, and keeps what it was sent. */
export function answering(
  reply: (request: Request) => Response | Promise<Response>,
) {
  const sent: Request[] = [];
  const api = makeApi(async (request) => {
    sent.push(request);
    return reply(request);
  });
  return { api, sent };
}

/** A reply the test sends when it chooses, to see what happens meanwhile. */
export function deferred() {
  let resolve: (response: Response) => void = () => {};
  const promise = new Promise<Response>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
