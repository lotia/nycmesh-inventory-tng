/**
 * The scan page's words, through Lingui (docs/decisions/0022): each phrase
 * is a descriptor, named and exported where it is used, made with Lingui's
 * `msg` macro, its English the message's id; `say` reads one in the active
 * locale. Vite's plugin compiles each catalogue as it is imported, so the
 * build holds compiled messages and nothing is compiled in the browser.
 */
import {
  i18n,
  type MessageDescriptor,
  type MessageOptions,
} from "@lingui/core";
import { signal } from "@preact/signals";
import { messages } from "../locales/en.po";
import { boxSeparator } from "./quantity";

/** Counts each change of locale or catalogue: a component that says
 * anything reads it, through `say`, so it renders again on each. */
const changes = signal(0);

i18n.on("change", () => {
  changes.value++;
});

/** Make `name`, with its compiled catalogue, the locale every phrase is
 * said in. */
export function speak(name: string, catalogue: typeof messages): void {
  i18n.loadAndActivate({ locale: name, messages: catalogue });
}

speak("en", messages);

/** A number writer for each locale and decimal separator, made the first
 * time it is needed. */
const writers = new Map<string, Intl.NumberFormat>();

/**
 * `value` written as the active locale writes a number, grouped and with
 * its decimal sign (1,234.5, or 1.234,5 in German), keeping every
 * thousandth the ledger keeps, as the server's web/formatting.amount
 * does; on a page whose boxes take a comma (shared/quantity's
 * `boxSeparator`), ungrouped and with that comma, 1234,5 (Ali,
 * 2026-11-23, inventory-tng-4mc3). For words only: a box holds
 * shared/quantity's `boxAmount`, which it reads back.
 */
export function sayNumber(value: number): string {
  // Read so a component rendering this renders again on a change.
  void changes.value;
  // As Lingui writes a plural's #.
  const locales = i18n.locales ?? i18n.locale;
  const comma = boxSeparator() === ",";
  const key = `${String(locales)} ${comma}`;
  let writer = writers.get(key);
  if (writer === undefined) {
    writer = new Intl.NumberFormat(locales, {
      maximumFractionDigits: 3,
      useGrouping: !comma,
    });
    writers.set(key, writer);
  }
  if (!comma) {
    return writer.format(value);
  }
  return writer
    .formatToParts(value)
    .map((part) => (part.type === "decimal" ? "," : part.value))
    .join("");
}

/** `amount`, in thousandths (shared/quantity.ts), as `sayNumber` writes
 * it: 1234500 as "1,234.5", or "1234,5" on a comma page. */
export function sayAmount(amount: number): string {
  return sayNumber(amount / 1000);
}

/** Marks a value's place in a message as it is said: private-use
 * characters no phrase or catalogue holds. */
const HOLE = /\uE000(\d+)\uE001/;
const HOLES = new RegExp(HOLE, "g");
/** Words a phrase marks `<n>…</n>`, such as a link's. */
const MARKED = /<(\d+)>([\s\S]*?)<\/\1>/;

/**
 * `phrase` said in the active locale with each value left as a hole, and
 * the values held for those holes. Lingui never reads a value, as it
 * would decode `\uXXXX` in one, so a name is shown exactly as typed.
 * Numbers are filled, so a plural can count them, its # written as
 * `sayNumber` would, and a missing value is left out, as Lingui leaves it.
 */
function holed(
  phrase: MessageDescriptor,
  values: Record<string, unknown>,
): [string, unknown[]] {
  // Read so a component rendering this renders again on a change.
  void changes.value;
  const held: unknown[] = [];
  const filled = Object.fromEntries(
    Object.entries({ ...phrase.values, ...values }).map(([name, value]) => {
      if (typeof value === "number" || value == null) {
        return [name, value];
      }
      held.push(value);
      return [name, `\uE000${held.length - 1}\uE001`];
    }),
  );
  const options: MessageOptions = {
    ...phrase,
    // A plural's # ungrouped on a comma page, as sayNumber writes it.
    ...(boxSeparator() === "," ? { formats: UNGROUPED } : {}),
  };
  return [i18n._(phrase.id, filled, options), held];
}

/** Lingui's number format for a plural's # on a comma page: its counts
 * are whole, so no grouping is all that sayNumber's rule asks. */
const UNGROUPED = { number: { useGrouping: false } };

/** A phrase in the active locale, with the values its placeholders name. */
export function say(
  phrase: MessageDescriptor,
  values: Record<string, unknown> = {},
): string {
  const [said, held] = holed(phrase, values);
  return said.replace(HOLES, (_, at) => String(held[at]));
}

/**
 * A phrase in parts, for words that are more than a string: each value
 * other than a number, such as a name in bold or a list of parts (outbox's
 * Said), put in whole where the phrase places it, never read as part of
 * the phrase; and each `<n>…</n>` in it, such as a link's words, made by
 * `marks[n]` from the parts inside it, or left as they are if there is no
 * `marks[n]`.
 */
export function sayParts<Part>(
  phrase: MessageDescriptor,
  values: Record<string, number | string | Part | readonly (string | Part)[]>,
  marks: readonly ((inside: (string | Part)[]) => Part)[] = [],
): (string | Part)[] {
  const [words, held] = holed(phrase, values);
  const unhole = (bits: string): (string | Part)[] =>
    bits.split(HOLE).flatMap((bit, at) => {
      if (at % 2 === 0) {
        return bit === "" ? [] : [bit];
      }
      const value = held[Number(bit)] as
        | string
        | Part
        | readonly (string | Part)[];
      return Array.isArray(value) ? value : [value as string | Part];
    });
  const said = words.split(MARKED);
  // split gives, after each part, a mark's number and the words inside it.
  const parts: (string | Part)[] = [];
  for (let at = 0; at < said.length; at += 3) {
    parts.push(...unhole(said[at] as string));
    if (at + 2 < said.length) {
      const inside = unhole(said[at + 2] as string);
      const mark = marks[Number(said[at + 1])];
      parts.push(...(mark === undefined ? inside : [mark(inside)]));
    }
  }
  return parts;
}
