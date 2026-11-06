/**
 * What a label scanned with the camera names (docs/decisions/0015): its QR
 * code holds the label's code alone, as a scanner gun types it.
 */
import {
  type Cart,
  type Catalogue,
  indexOf,
  type Named,
  SIDES,
  type Side,
} from "./cart";

export type Scanned = Named | { kind: "unknown"; code: string };

/** The item, package or place a label's code names, if the catalogue has it. */
export function readLabel(catalogue: Catalogue, text: string): Scanned {
  const code = text.trim().toUpperCase();
  return indexOf(catalogue).codes.get(code) ?? { kind: "unknown", code };
}

/**
 * The sides a place may fill for the cart's kind, the one still empty first:
 * a volunteer is asked From or To, offered that one.
 */
export function placeSides(cart: Cart): Side[] {
  const sides = SIDES[cart.kind];
  const offered = (["from", "to"] as const).filter((side) => sides[side]);
  return offered.sort(
    (a, b) => Number(filled(cart, a)) - Number(filled(cart, b)),
  );
}

function filled(cart: Cart, side: Side): boolean {
  return (side === "from" ? cart.fromId : cart.toId) !== null;
}

/**
 * Tells a label shown again from the same label still in view: the camera
 * reads a code many times a second, so one is taken once in `quietMs`.
 */
export class Repeats {
  private readonly last = new Map<string, number>();

  constructor(private readonly quietMs = 2000) {}

  /** Whether `code`, read at `now` (in milliseconds), is a new scan. */
  fresh(code: string, now: number): boolean {
    const before = this.last.get(code);
    this.last.set(code, now);
    return before === undefined || now - before >= this.quietMs;
  }
}
