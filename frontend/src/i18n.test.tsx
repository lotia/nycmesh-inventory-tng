import { i18n } from "@lingui/core";
import { render, screen } from "@testing-library/preact";
import { afterEach, expect, test } from "vitest";
import { say, speak } from "./i18n";
import { messages as english } from "./locales/en.po";
import { messages as pseudo } from "./locales/pseudo.po";
import { BATCHES_WAITING, REGISTERED } from "./RegisterPage";

afterEach(() => speak("en", english));

test("the page speaks English, from its catalogue", () => {
  expect(i18n.locale).toBe("en");
  expect(say(REGISTERED)).toBe("This browser is registered.");
});

test("a plural reads the count its placeholder names", () => {
  expect(say(BATCHES_WAITING, { count: 1 })).toBe(
    "The batch waiting on this browser is being sent.",
  );
  expect(say(BATCHES_WAITING, { count: 3 })).toBe(
    "The 3 batches waiting on this browser are being sent.",
  );
});

test("a component that says a phrase renders again when the locale changes", async () => {
  let shown = 0;
  function Said() {
    shown++;
    return <p>{say(REGISTERED)}</p>;
  }
  render(<Said />);
  const before = shown;
  speak("pseudo", pseudo);
  expect(await screen.findByText("Ţĥĩś ƀŕōŵśēŕ ĩś ŕēĝĩśţēŕēď.")).not.toBeNull();
  expect(shown).toBeGreaterThan(before);
});

test("a component renders again when its locale's catalogue is loaded again", async () => {
  function Said() {
    return <p>{say(REGISTERED)}</p>;
  }
  render(<Said />);
  speak("en", { ...english, [REGISTERED.id]: "Registered again." });
  expect(await screen.findByText("Registered again.")).not.toBeNull();
});
