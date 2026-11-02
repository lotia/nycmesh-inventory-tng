/** Fakes for the API, shared by the tests. Never bundled. */
import { makeApi } from "./api/client";

/** A JSON reply with this status. */
export const json = (body: unknown, status: number): Response =>
  Response.json(body, { status });

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
