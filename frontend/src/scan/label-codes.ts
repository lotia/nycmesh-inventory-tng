/**
 * What a label scanned with the camera names (docs/decisions/0015): its QR
 * code holds the label's code alone, as a scanner gun types it.
 */
import type { Catalogue } from "./cart";
import { indexOf, type Named } from "./search";

export type Scanned = Named | { kind: "unknown"; code: string };

/** The item, package or place a label's code names, if the catalogue has it. */
export function readLabel(catalogue: Catalogue, text: string): Scanned {
  const code = text.trim().toUpperCase();
  return indexOf(catalogue).codes.get(code) ?? { kind: "unknown", code };
}
