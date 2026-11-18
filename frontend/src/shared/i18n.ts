/**
 * The scan page's words, through Lingui (docs/decisions/0022): each phrase
 * is a descriptor, named and exported where it is used, made with Lingui's
 * `msg` macro, its English the message's id; `say` reads one in the active
 * locale. Vite's plugin compiles each catalogue as it is imported, so the
 * build holds compiled messages and nothing is compiled in the browser.
 */
import { i18n, type MessageDescriptor } from "@lingui/core";
import { signal } from "@preact/signals";
import { messages } from "../locales/en.po";

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
 * Numbers are filled, so a plural can count them, and a missing value is
 * left out, as Lingui leaves it.
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
  return [i18n._({ ...phrase, values: filled }), held];
}

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
