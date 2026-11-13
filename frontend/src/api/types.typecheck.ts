/**
 * Checked by `npm run typecheck`, never run or bundled. Each
 * `@ts-expect-error` must meet an error, or the check fails: so these prove
 * that the generated types reach TypeScript and refuse what the API would.
 */
import type * as v from "valibot";
import type { BatchBody } from "../outbox";
import { makeApi, type Schemas } from "./client";
import type * as replies_ from "./replies";

const api = makeApi();

export async function registering(): Promise<void> {
  const { data, error } = await api.POST("/api/devices", {
    body: { code: "a-b-c-d-e" },
  });
  // @ts-expect-error data is undefined until the reply is known to be a 201
  void data.device_id;
  if (data !== undefined) {
    data.device_id satisfies number;
  }
  if (error !== undefined && "reason" in error) {
    error.reason satisfies
      | Schemas["CodeRefusedReply"]["reason"]
      | Schemas["RateLimitedReply"]["reason"]
      | Schemas["UnavailableReply"]["reason"];
    // @ts-expect-error a reason the server never sends
    "frozen" satisfies typeof error.reason;
  }
  // @ts-expect-error the body needs a code
  await api.POST("/api/devices", { body: {} });
  // @ts-expect-error the code is a string
  await api.POST("/api/devices", { body: { code: 12345 } });
  // @ts-expect-error devices takes POST only
  await api.GET("/api/devices");
  // @ts-expect-error a path the API does not have
  await api.POST("/api/nowhere", { body: {} });
}

export async function saving(): Promise<void> {
  const line = { item_id: 1, quantity: 2 };
  const batch = { key: "k", volunteer_id: 1, lines: [line] };
  const { data, error } = await api.POST("/api/transactions", {
    body: { ...batch, kind: "move", from_location_id: 1, to_location_id: 2 },
  });
  if (data !== undefined) {
    data.transaction_id satisfies number;
  }
  if (error !== undefined && "reason" in error) {
    error.reason satisfies
      | Schemas["DeviceRefusedReply"]["reason"]
      | Schemas["KeyReusedReply"]["reason"]
      | Schemas["BuildTooOldReply"]["reason"]
      | Schemas["UnavailableReply"]["reason"];
  }
  // @ts-expect-error not a kind the ledger has
  await api.POST("/api/transactions", { body: { ...batch, kind: "loan" } });
  await api.POST("/api/transactions", {
    // @ts-expect-error a batch needs its lines
    body: { key: "k", kind: "move", volunteer_id: 1 },
  });
}

/** True only when A and B are the same type, in both directions. */
type Same<A, B> =
  (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2
    ? true
    : false;
type Checked<S extends v.GenericSchema> = v.InferOutput<S>;

// Each reply schema says exactly what the generated type says: a field
// added, dropped or retyped on the server fails here until replies.ts
// follows. Except BuildTooOldReply, read by pages older than the server:
// only its reason is checked, and must stay the same (replies.ts).
export const replies: [
  Same<Checked<typeof replies_.Registered>, Schemas["Registered"]>,
  Same<Checked<typeof replies_.CodeRefusedReply>, Schemas["CodeRefusedReply"]>,
  Same<Checked<typeof replies_.RateLimitedReply>, Schemas["RateLimitedReply"]>,
  Same<
    Checked<typeof replies_.DeviceRefusedReply>,
    Schemas["DeviceRefusedReply"]
  >,
  Same<Checked<typeof replies_.KeyReusedReply>, Schemas["KeyReusedReply"]>,
  Same<
    Checked<typeof replies_.BuildTooOldReply>["reason"],
    Schemas["BuildTooOldReply"]["reason"]
  >,
  Same<Checked<typeof replies_.Saved>, Schemas["Saved"]>,
  Same<Checked<typeof replies_.Catalogue>, Schemas["Catalogue"]>,
  // What the outbox keeps is exactly what the endpoint takes.
  Same<Checked<typeof BatchBody>, Schemas["Batch"]>,
] = [true, true, true, true, true, true, true, true, true];
// @ts-expect-error a schema missing a field is caught
export const caught: Same<{ id: number }, Schemas["VolunteerEntry"]> = true;
