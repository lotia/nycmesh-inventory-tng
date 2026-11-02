/**
 * Checked by `npm run typecheck`, never run or bundled. Each
 * `@ts-expect-error` must meet an error, or the check fails: so these prove
 * that the generated types reach TypeScript and refuse what the API would.
 */
import { makeApi, type Schemas } from "./client";

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
      | Schemas["UnavailableReply"]["reason"];
  }
  // @ts-expect-error not a kind the ledger has
  await api.POST("/api/transactions", { body: { ...batch, kind: "loan" } });
  await api.POST("/api/transactions", {
    // @ts-expect-error a batch needs its lines
    body: { key: "k", kind: "move", volunteer_id: 1 },
  });
}
