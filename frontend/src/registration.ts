/** Registering this browser with a phrase (docs/decisions/0009). */
import { type Api, reasonOf, type Schemas } from "./api/client";

type BadCode = Schemas["CodeRefusedReply"]["reason"];
type Rejection = BadCode | Schemas["RateLimitedReply"]["reason"];

export type Outcome =
  | { kind: "registered"; deviceId: number }
  // Nothing to send: a blank phrase would only count against the limit.
  | { kind: "blank" }
  | { kind: "refused"; reason: Rejection }
  // The database cannot answer just now: worth trying again soon.
  | { kind: "unavailable" }
  // No answer, or one this build does not understand.
  | { kind: "failed" };

/** What to tell a volunteer. Typed by the schema, so a new reason on the
 * server fails the type check until it has a message here. */
export const REFUSALS: Record<Rejection, string> = {
  unknown:
    "That phrase isn't one we know. Check it word by word against the one you were given.",
  expired: "That phrase has expired. Ask for a new one.",
  used_up:
    "That phrase has registered all the devices it may. Ask for a new one.",
  revoked: "That phrase has been withdrawn. Ask for a new one.",
  rate_limited: "Too many tries from here. Wait a minute, then try again.",
};

function isBadCode(reason: string | undefined): reason is BadCode {
  return (
    reason !== undefined &&
    reason !== "rate_limited" &&
    Object.hasOwn(REFUSALS, reason)
  );
}

/** Send the phrase, as typed or as the link carried it. */
export async function register(api: Api, typed: string): Promise<Outcome> {
  const code = typed.trim();
  if (code === "") {
    return { kind: "blank" };
  }
  // Throws when offline, or for a success whose body is not JSON.
  const reply = await api
    .POST("/api/devices", { body: { code } })
    .catch(() => null);
  if (reply === null) {
    return { kind: "failed" };
  }
  const { data, error, response } = reply;
  if (data !== undefined) {
    // Only 201 has a body here; check it rather than trust the types.
    return typeof data.device_id === "number"
      ? { kind: "registered", deviceId: data.device_id }
      : { kind: "failed" };
  }
  if (response.status === 503) {
    return { kind: "unavailable" };
  }
  const reason = reasonOf(error);
  if (response.status === 429 && reason === "rate_limited") {
    return { kind: "refused", reason };
  }
  if (response.status === 400 && isBadCode(reason)) {
    return { kind: "refused", reason };
  }
  return { kind: "failed" };
}
