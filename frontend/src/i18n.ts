/**
 * The scan page's words, through Lingui (docs/decisions/0022): each phrase
 * is a descriptor, named and exported where it is used, made with Lingui's
 * `msg` macro, its English the message's id; `say` reads one in the active
 * locale. Vite's plugin compiles each catalogue as it is imported, so the
 * build holds compiled messages and nothing is compiled in the browser.
 */
import { i18n, type MessageDescriptor } from "@lingui/core";
import { signal } from "@preact/signals";
import { messages } from "./locales/en.po";

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

/** A phrase in the active locale, with the values its placeholders name. */
export function say(
  phrase: MessageDescriptor,
  values?: Record<string, unknown>,
): string {
  // Read so a component rendering this renders again on a change.
  void changes.value;
  const filled = { ...phrase, values: { ...phrase.values, ...values } };
  return i18n._(filled);
}

/** Marks a value's place in a message as it is said: private-use
 * characters no phrase or catalogue holds. */
const HOLE = /(\d+)/;
/** Words a phrase marks `<n>…</n>`, such as a link's. */
const MARKED = /<(\d+)>([\s\S]*?)<\/\1>/;

/**
 * A phrase in parts, for words that are more than a string: each value
 * other than a number, such as a name in bold or a list of parts (outbox's
 * Said), put in whole where the phrase places it, never read as part of
 * the phrase; and each `<n>…</n>` in it, such as a link's words, made by
 * `marks[n]` from the parts inside it. Numbers are filled as `say` fills
 * them, so a plural can count them.
 */
export function sayParts<Part>(
  phrase: MessageDescriptor,
  values: Record<string, number | string | Part | readonly (string | Part)[]>,
  marks: readonly ((inside: (string | Part)[]) => Part)[] = [],
): (string | Part)[] {
  const held: (readonly (string | Part)[])[] = [];
  const filled = Object.fromEntries(
    Object.entries(values).map(([name, value]) => {
      if (typeof value === "number") {
        return [name, value];
      }
      held.push(Array.isArray(value) ? value : [value as string | Part]);
      return [name, `${held.length - 1}`];
    }),
  );
  const unhole = (words: string): (string | Part)[] =>
    words
      .split(HOLE)
      .flatMap((bit, at) =>
        at % 2 === 1 ? [...(held[Number(bit)] as [])] : bit === "" ? [] : [bit],
      );
  const said = say(phrase, filled).split(MARKED);
  // split gives, after each part, a mark's number and the words inside it.
  const parts: (string | Part)[] = [];
  for (let at = 0; at < said.length; at += 3) {
    parts.push(...unhole(said[at] as string));
    const mark = marks[Number(said[at + 1])];
    if (at + 2 < said.length && mark !== undefined) {
      parts.push(mark(unhole(said[at + 2] as string)));
    }
  }
  return parts;
}
