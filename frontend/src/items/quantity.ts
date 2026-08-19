/**
 * Saying a quantity out loud, in the item's own unit.
 *
 * A cart line's quantity is never left as a bare number. The complaint this
 * project starts from is a form where "1" could mean one zip tie or one packet
 * of a hundred, and the fix is that the resulting quantity is always spelled
 * out and always editable -- docs/decisions/0011-qr-batch-scanning.md
 * section 6, "Nothing is ever added silently".
 */

/** The units `Item.unit_of_measure` can hold, and how to say them. */
const UNITS: Record<string, { one: string; many: string }> = {
  each: { one: "each", many: "each" },
  metre: { one: "metre", many: "metres" },
  foot: { one: "foot", many: "feet" },
};

/** A decimal from the API, as a number, without trailing zeros in the display. */
export function toNumber(decimal: string): number {
  const value = Number(decimal);
  return Number.isFinite(value) ? value : 0;
}

/** A quantity as a person writes it: 12, not 12.000, and 1.5 kept. */
export function formatQuantity(quantity: number): string {
  return String(Math.round(quantity * 1000) / 1000);
}

function unitFor(quantity: number, unitOfMeasure: string): string {
  // An unknown unit is shown as it arrived rather than swallowed: a new one on
  // the backend should read oddly here, not vanish.
  const unit = UNITS[unitOfMeasure] ?? { one: unitOfMeasure, many: unitOfMeasure };
  return quantity === 1 ? unit.one : unit.many;
}

/**
 * The largest packet this quantity is a whole number of.
 *
 * Null when there is none, which is every hand-counted quantity and every item
 * with no packaging label. Only sizes above one count: a label meaning one of
 * something is not packaging, it is the thing.
 */
function packetIn(quantity: number, packetSizes: number[]): number | null {
  const whole = packetSizes.filter((size) => size > 1 && quantity % size === 0);
  return whole.length > 0 ? Math.max(...whole) : null;
}

/**
 * How a cart line reads: "100 each (1 packet of 100)".
 *
 * The parenthetical is what closes the packet ambiguity. Without it a
 * volunteer who tapped the +100 chip and one who typed 100 see the same line,
 * and only one of them meant a packet.
 */
export function describeQuantity(
  quantity: number,
  unitOfMeasure: string,
  packetSizes: number[],
): string {
  const spelled = `${formatQuantity(quantity)} ${unitFor(quantity, unitOfMeasure)}`;
  const packet = packetIn(quantity, packetSizes);
  if (packet === null) {
    return spelled;
  }
  const packets = quantity / packet;
  return `${spelled} (${formatQuantity(packets)} ${packets === 1 ? "packet" : "packets"} of ${formatQuantity(packet)})`;
}
