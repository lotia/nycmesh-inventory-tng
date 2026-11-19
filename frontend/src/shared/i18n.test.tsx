import { i18n } from "@lingui/core";
import { render, screen } from "@testing-library/preact";
import { expect, test } from "vitest";
import { messages as english } from "../locales/en.po";
import { messages as pseudo } from "../locales/pseudo.po";
import { BATCHES_WAITING, REGISTERED } from "../register/RegisterPage";
import { ADDED_ONE } from "../scan/CartForm";
import { ITEM_SAYS } from "../scan/cart";
import { REGISTER_IT } from "../scan/ScanPage";
import { say, sayAmount, sayNumber, sayParts, speak } from "./i18n";

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

// A name is shown exactly as typed: Lingui decodes \\uXXXX and \\xXX in
// what it fills in, and a name may hold such text, or a mark's.
const TYPED = String.raw`A\u0042 \x43 <0>D</0> {e} #`;

test("a value is said exactly as given", () => {
  expect(say(ADDED_ONE, { item: TYPED })).toBe(`Added one: ${TYPED}.`);
  expect(say(ADDED_ONE, { item: undefined })).toBe("Added one: .");
});

test("a value said in parts is put in whole, never read as the phrase's marks", () => {
  const bold = (inside: unknown[]) => ({ bold: inside });
  expect(sayParts(ITEM_SAYS, { name: TYPED, what: ["2 m"] }, [bold])).toEqual([
    TYPED,
    ": ",
    "2 m",
  ]);
});

test("words marked for no part given keep their place, unmarked", () => {
  // As a translation numbering its marks otherwise would leave them.
  expect(sayParts(REGISTER_IT, {}).join("")).toBe(
    "Register it, then come back.",
  );
});

test("a quantity is written as the locale writes a number, every thousandth kept", () => {
  expect(sayAmount(1_234_500)).toBe("1,234.5");
  expect(sayAmount(1_234_567)).toBe("1,234.567");
  expect(sayAmount(1)).toBe("0.001");
  expect(sayAmount(3000)).toBe("3");
  expect(sayAmount(-1_234_500)).toBe("-1,234.5");
  expect(sayNumber(1000)).toBe("1,000");
  // The most the ledger holds (models/base.py QUANTITY), every digit kept.
  expect(sayAmount(999_999_999_999)).toBe("999,999,999.999");
  speak("de", english);
  expect(sayAmount(1_234_500)).toBe("1.234,5");
  expect(sayAmount(1_234_567)).toBe("1.234,567");
  expect(sayNumber(1000)).toBe("1.000");
});
