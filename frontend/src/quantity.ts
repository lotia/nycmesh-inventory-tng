/**
 * Quantities as whole thousandths of a unit, the three decimal places the
 * ledger keeps, so adding packages and loose units never meets floating
 * point (0.1 + 0.2). Read from and written to the decimal strings the API
 * uses.
 */

const DECIMAL = /^(\d+)(?:\.(\d{1,3}))?$/;
const WHOLE = /^\d+$/;

/** "12.5" as 12500. Blank is nothing; anything else unreadable is null. */
export function thousandths(text: string): number | null {
  const match = DECIMAL.exec(text.trim() || "0");
  if (match === null) {
    return null;
  }
  const [, units = "0", fraction = ""] = match;
  return Number(units) * 1000 + Number(fraction.padEnd(3, "0"));
}

/** A count of whole things, such as packages. Blank is none. */
export function count(text: string): number | null {
  const trimmed = text.trim() || "0";
  return WHOLE.test(trimmed) ? Number(trimmed) : null;
}

/** 12500 as "12.5", and 3000 as "3". */
export function decimal(amount: number): string {
  const units = Math.trunc(amount / 1000);
  const fraction = String(amount % 1000)
    .padStart(3, "0")
    .replace(/0+$/, "");
  return fraction === "" ? String(units) : `${units}.${fraction}`;
}
