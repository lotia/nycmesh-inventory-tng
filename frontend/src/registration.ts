/** Registering this browser with a phrase (docs/decisions/0009). */
import { type Api, check, type Schemas } from "./api/client";
import { CodeRefusedReply, RateLimitedReply, Registered } from "./api/replies";

type Rejection =
  | Schemas["CodeRefusedReply"]["reason"]
  | Schemas["RateLimitedReply"]["reason"];

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

/** Send the phrase, as typed or as the link carried it. */
export async function register(api: Api, typed: string): Promise<Outcome> {
  const code = typed.trim();
  if (code === "") {
    return { kind: "blank" };
  }
  const reply = await check(api.POST("/api/devices", { body: { code } }), {
    201: Registered,
    400: CodeRefusedReply,
    429: RateLimitedReply,
  });
  switch (reply.status) {
    case 201:
      return { kind: "registered", deviceId: reply.body.device_id };
    case 400:
    case 429:
      return { kind: "refused", reason: reply.body.reason };
    case "unavailable":
      return { kind: "unavailable" };
    default:
      return { kind: "failed" };
  }
}
