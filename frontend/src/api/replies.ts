/**
 * What the API's replies must look like before the pages use them, checked
 * with Valibot (decision 0010). The generated types say what the server
 * promises; these check what arrived. types.typecheck.ts proves that each
 * matches its generated type, so the two cannot drift apart.
 */
import * as v from "valibot";

/** A row's id. */
export const id = v.pipe(v.number(), v.integer());

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
  // Opening one makes an open package, followed, rather than loose units.
  track_opened: v.boolean(),
  // An open one with less left is a short end; null, none is.
  short_below: v.nullable(v.string()),
});

/** A package opened and followed, not reported used up (0020). */
export const OpenPackageEntry = v.object({
  id,
  key: v.pipe(v.string(), v.uuid()),
  item_id: id,
  package_id: id,
  location_id: id,
  opened_at: v.string(),
  // What is left on it, in the item's unit.
  left: v.string(),
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
  // Retired, with stock left: it may be taken, never received.
  retired: v.boolean(),
});

export const LabelEntry = v.object({
  code: v.string(),
  item_id: v.nullable(id),
  package_id: v.nullable(id),
  location_id: v.nullable(id),
});

export const Catalogue = v.object({
  settings: v.object({ name_check_minutes: v.pipe(id, v.minValue(0)) }),
  volunteers: v.array(VolunteerEntry),
  locations: v.array(LocationEntry),
  items: v.array(ItemEntry),
  labels: v.array(LabelEntry),
  open_packages: v.array(OpenPackageEntry),
});
