import { beforeEach, expect, test } from "vitest";
import { followWebSearch } from "./web-search";

beforeEach(() => {
  document.body.innerHTML = `
    <form>
      <input name="manufacturer" value="">
      <input name="part_number" value="">
      <p data-web-search hidden>
        <a href="https://duckduckgo.com/?q=" target="_blank">Search the web</a>
      </p>
    </form>`;
  followWebSearch(document);
});

const shown = () =>
  document.querySelector("[data-web-search]") as HTMLParagraphElement;
const link = () => document.querySelector("a") as HTMLAnchorElement;

function type(name: string, value: string) {
  const field = document.querySelector(
    `input[name="${name}"]`,
  ) as HTMLInputElement;
  field.value = value;
  field.dispatchEvent(new Event("input"));
}

test("the link searches for what is typed, encoded", () => {
  type("manufacturer", " Ubiquiti ");
  type("part_number", "LBE-5AC/Gen2 & co?");
  expect(shown().hidden).toBe(false);
  const address = new URL(link().href);
  expect(address.origin + address.pathname).toBe("https://duckduckgo.com/");
  expect(address.searchParams.get("q")).toBe("Ubiquiti LBE-5AC/Gen2 & co?");
  expect(address.search).toBe("?q=Ubiquiti+LBE-5AC%2FGen2+%26+co%3F");
});

test("either field alone is searched for", () => {
  type("part_number", "RB5009*1~2");
  // The server spells * and ~ differently (%2A, ~), but q reads the same.
  expect(new URL(link().href).searchParams.get("q")).toBe("RB5009*1~2");
  expect(shown().hidden).toBe(false);
});

test("it hides while both fields are blank", () => {
  type("manufacturer", "MikroTik");
  expect(shown().hidden).toBe(false);
  type("manufacturer", "  ");
  expect(shown().hidden).toBe(true);
});

test("what the page was sent with shows at once", () => {
  document.body.innerHTML = `
    <form>
      <input name="manufacturer" value="MikroTik">
      <input name="part_number" value="">
      <p data-web-search hidden><a href="https://duckduckgo.com/?q=">Search</a></p>
    </form>`;
  followWebSearch(document);
  expect(shown().hidden).toBe(false);
  expect(new URL(link().href).searchParams.get("q")).toBe("MikroTik");
});

test("a link outside a form is left as it is", () => {
  document.body.innerHTML = `<p data-web-search hidden><a href="https://duckduckgo.com/?q=">Search</a></p>`;
  followWebSearch(document);
  expect(shown().hidden).toBe(true);
});
