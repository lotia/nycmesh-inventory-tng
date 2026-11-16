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

/** The active locale, as a signal: a component that says anything reads
 * it, through `say`, so it renders again when the locale changes. */
export const locale = signal("");

i18n.on("change", () => {
  locale.value = i18n.locale;
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
  // Read so a component rendering this renders again on a change of locale.
  void locale.value;
  const filled = { ...phrase, values: { ...phrase.values, ...values } };
  return i18n._(filled);
}
