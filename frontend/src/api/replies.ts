/**
 * What the API's replies must look like before the pages use them, checked
 * with Valibot (decision 0010). The generated types say what the server
 * promises; these check what arrived. types.typecheck.ts proves that each
 * matches its generated type, so the two cannot drift apart.
 */
import * as v from "valibot";

const id = v.pipe(v.number(), v.integer());

export const Registered = v.object({ device_id: id });

export const CodeRefusedReply = v.object({
  reason: v.picklist(["unknown", "expired", "used_up", "revoked"]),
});

export const RateLimitedReply = v.object({ reason: v.literal("rate_limited") });

export const DeviceRefusedReply = v.object({
  reason: v.picklist(["unregistered", "revoked"]),
});

export const KeyReusedReply = v.object({ reason: v.literal("key_reused") });

export const Saved = v.object({ transaction_id: id });

/** FastAPI's 422, and the ledger's, which has the same shape: only the parts
 * the pages read are checked. */
export const ValidationFailed = v.object({
  detail: v.array(
    v.looseObject({
      loc: v.array(v.union([v.string(), v.number()])),
      type: v.string(),
      msg: v.optional(v.string()),
    }),
  ),
});

export const VolunteerEntry = v.object({ id, name: v.string() });

export const LocationEntry = v.object({
  id,
  name: v.string(),
  kind: v.string(),
  parent_id: v.nullable(id),
});

export const PackageEntry = v.object({
  id,
  name: v.string(),
  size: v.string(),
});

export const ItemEntry = v.object({
  id,
  name: v.string(),
  unit: v.string(),
  category: v.string(),
  manufacturer: v.nullable(v.string()),
  part_number: v.nullable(v.string()),
  old_code: v.nullable(v.string()),
  packages: v.array(PackageEntry),
});

export const LabelEntry = v.object({
  code: v.string(),
  item_id: v.nullable(id),
  package_id: v.nullable(id),
  location_id: v.nullable(id),
});

export const Catalogue = v.object({
  volunteers: v.array(VolunteerEntry),
  locations: v.array(LocationEntry),
  items: v.array(ItemEntry),
  labels: v.array(LabelEntry),
});
