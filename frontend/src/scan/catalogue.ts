/** Loading the scan page's catalogue, and what came of it (Loaded). */
import { type Api, check } from "../api/client";
import {
  Catalogue as CatalogueReply,
  DeviceRefusedReply,
} from "../api/replies";
import type { Outbox } from "../shared/outbox";
import type { Catalogue } from "./cart";
import { keysToAsk } from "./unseen-batches";

export type Loaded =
  | { state: "loading" }
  | { state: "ready"; catalogue: Catalogue }
  | { state: "unregistered"; reason: "unregistered" | "revoked" }
  | { state: "unavailable" };

/** The catalogue, asked which of the outbox's batches it shows
 * (unseen-batches.ts, unseen). */
export async function loadCatalogue(api: Api, outbox: Outbox): Promise<Loaded> {
  const key = keysToAsk(outbox.entries.value);
  const reply = await check(
    api.GET("/api/catalogue", { params: { query: { key } } }),
    { 200: CatalogueReply, 403: DeviceRefusedReply },
  );
  switch (reply.status) {
    case 200:
      return { state: "ready", catalogue: reply.body };
    case 403:
      return { state: "unregistered", reason: reply.body.reason };
    default:
      return { state: "unavailable" };
  }
}
