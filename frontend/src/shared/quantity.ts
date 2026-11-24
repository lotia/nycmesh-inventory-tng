/**
 * Quantities as whole thousandths of a unit, the three decimal places the
 * ledger keeps, so adding packages and loose units never meets floating
 * point (0.1 + 0.2). Read from and written to the decimal strings the API
 * uses.
 */

const DECIMAL = /^(\d+)(?:\.(\d{1,3}))?$/;
const WHOLE = /^\d+$/;

/** "12.5" as 12500, as the server or the page itself wrote it. Blank is
 * nothing; anything else unreadable is null. What is typed in a box is
 * read by `readBox`. */
export function thousandths(text: string): number | null {
  const match = DECIMAL.exec(text.trim() || "0");
  if (match === null) {
    return null;
  }
  const [, units = "0", fraction = ""] = match;
  return Number(units) * 1000 + Number(fraction.padEnd(3, "0"));
}

/** A decimal the server sent, in thousandths: it is always one, and may
 * be less than nothing, as the records may say (docs/decisions/0020). */
export function sent(text: string): number {
  if (text.startsWith("-")) {
    return -sent(text.slice(1));
  }
  const parsed = thousandths(text);
  if (parsed === null) {
    throw new Error(`not a decimal: ${text}`);
  }
  return parsed;
}

/** A box's decimal separator, as the server tells the page. */
export type Separator = "." | ",";

/** The decimal separator this page's boxes take: "." until its entry
 * point reads the page's own (takeSeparatorFrom). */
let pageSeparator: Separator = ".";

/** Take the decimal separator `root` says, in its data-decimal, as this
 * page's: the one the server chose from the browser's Accept-Language
 * (web/decimal_separator.py). Its boxes are read and written in it. */
export function takeSeparatorFrom(root: HTMLElement): void {
  pageSeparator = root.dataset.decimal === "," ? "," : ".";
}

/** The decimal separator this page's boxes take. */
export function boxSeparator(): Separator {
  return pageSeparator;
}

/** Why what was typed in a number box is no amount, as the server says
 * (web/forms.Unreadable): not a number; the other decimal separator than
 * the box's; that one followed by three digits after a whole part above
 * 0, as thousands are grouped; or more than three decimal places. Both
 * sides are tested against quantity-cases.json. */
export type Unreadable = "number" | "separator" | "thousands" | "places";

const DIGIT = /\p{Nd}/u;
const ASCII_DIGITS = /^[0-9]*$/;
// Read as the box's own separator, as digits of any script are read as
// 0-9 (Ali, 2026-11-23, inventory-tng-qnwz).
const ARABIC_DECIMAL = "\u066B";

/** A digit of any script as 0-9: Unicode keeps each script's in runs of
 * ten, from zero, so its place in its run of digits is its value. */
function asciiDigit(char: string): string {
  // One code point, as a string's iterator gives them.
  const point = char.codePointAt(0) as number;
  let zero = point;
  while (DIGIT.test(String.fromCodePoint(zero - 1))) {
    zero -= 1;
  }
  return String((point - zero) % 10);
}

/** `plain` as a sign, whole digits and decimals, `separator` between
 * them; null if it is not so. */
function split(plain: string, separator: string) {
  const negative = plain.startsWith("-");
  const [units = "", fraction = "", ...more] = (
    negative ? plain.slice(1) : plain
  ).split(separator);
  return more.length > 0 ||
    units + fraction === "" ||
    !ASCII_DIGITS.test(units) ||
    !ASCII_DIGITS.test(fraction)
    ? null
    : { negative, units, fraction };
}

/** A number typed in a box whose decimal separator is `separator`, in
 * thousandths, as web/forms.read_decimal reads one: digits, of any
 * script, with that separator and up to three decimals after it, and a
 * "-" before them if below zero. Neither grouping, an exponent, "+" nor
 * "_"; -0 reads as 0. */
export function amountTyped(
  text: string,
  separator: Separator = pageSeparator,
): number | Unreadable {
  let plain = "";
  for (const char of text.trim()) {
    plain += DIGIT.test(char)
      ? asciiDigit(char)
      : char === ARABIC_DECIMAL
        ? separator
        : char;
  }
  const parts = split(plain, separator);
  if (parts === null) {
    const mistaken = split(plain, separator === "." ? "," : ".");
    if (mistaken === null) {
      return "number";
    }
    // A whole part of 0, or none, is no thousands, so a decimal (Ali,
    // 2026-11-23, inventory-tng-mat2).
    return /[1-9]/.test(mistaken.units) && mistaken.fraction.length === 3
      ? "thousands"
      : "separator";
  }
  const { negative, units, fraction } = parts;
  if (fraction.length > 3) {
    return "places";
  }
  const amount = Number(units) * 1000 + Number(fraction.padEnd(3, "0"));
  // Plus 0, as -0 is 0.
  return (negative ? -amount : amount) + 0;
}

/** What is typed in a box, in thousandths (amountTyped): blank is
 * nothing; null if it is no amount of 0 or more. */
export function readBox(
  text: string,
  separator: Separator = pageSeparator,
): number | null {
  if (text.trim() === "") {
    return 0;
  }
  const amount = amountTyped(text, separator);
  return typeof amount === "number" && amount >= 0 ? amount : null;
}

/** 12500 as "12.5", or "12,5": `decimal` in a box whose decimal separator
 * is `separator`, without grouping, so it reads back unchanged (readBox). */
export function boxAmount(
  amount: number,
  separator: Separator = pageSeparator,
): string {
  return decimal(amount).replace(".", separator);
}

/** A box's refusal of the decimal separator its page does not use, as
 * the server counts them (web/forms.OtherSeparator): a point, a comma, or
 * either read as thousands (Unreadable). */
export type OtherSeparator = "point" | "comma" | "thousands";

/** Which decimal separator other than its box's, `separator`, what is
 * typed in it was refused for; null if that is not why, or it is no
 * refusal. */
export function otherSeparator(
  text: string,
  separator: Separator = pageSeparator,
): OtherSeparator | null {
  switch (amountTyped(text, separator)) {
    case "thousands":
      return "thousands";
    case "separator":
      return separator === "." ? "comma" : "point";
    default:
      return null;
  }
}

/** A count of whole things, such as packages. Blank is none. */
export function count(text: string): number | null {
  const trimmed = text.trim() || "0";
  return WHOLE.test(trimmed) ? Number(trimmed) : null;
}

/** 12500 as "12.5", and 3000 as "3": plain, as the API reads it. A box
 * takes `boxAmount`; words people read write it with i18n's `sayAmount`. */
export function decimal(amount: number): string {
  if (amount < 0) {
    return `-${decimal(-amount)}`;
  }
  const units = Math.trunc(amount / 1000);
  const fraction = String(amount % 1000)
    .padStart(3, "0")
    .replace(/0+$/, "");
  return fraction === "" ? String(units) : `${units}.${fraction}`;
}

/** How many whole packages of `size` thousandths `amount` makes, or null
 * if it is not a whole number of them, or the size is unknown or none. */
export function wholePackages(
  amount: number,
  size: number | null,
): number | null {
  return size !== null && size > 0 && amount % size === 0
    ? amount / size
    : null;
}
