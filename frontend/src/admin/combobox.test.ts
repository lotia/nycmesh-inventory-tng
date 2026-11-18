import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type Suggestion, suggest } from "./combobox";

const FOUND: Suggestion[] = [
  { name: "LiteBeam 5AC", hint: "Radios", href: "/admin/items?pick=1" },
  { name: "LiteAP", hint: "", href: "/admin/items?pick=2" },
];

let input: HTMLInputElement;
let asked: URL[];
let went: string[];
let answer: () => Promise<Response>;

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `<form><label>Find items
    <input name="q" data-suggest="/admin/items/suggest?show=all"></label></form>`;
  input = document.querySelector("input") as HTMLInputElement;
  asked = [];
  went = [];
  answer = async () => new Response(JSON.stringify(FOUND));
  suggest(input, {
    fetch: (async (url: URL) => {
      asked.push(url);
      return answer();
    }) as typeof fetch,
    go: (href) => went.push(href),
  });
});

afterEach(() => {
  document.body.replaceChildren();
});

function list(): HTMLElement {
  return document.getElementById(
    input.getAttribute("aria-controls") ?? "",
  ) as HTMLElement;
}

async function type(text: string) {
  input.value = text;
  input.dispatchEvent(new Event("input"));
  await vi.runAllTimersAsync();
}

function press(key: string): KeyboardEvent {
  const event = new KeyboardEvent("keydown", { key, cancelable: true });
  input.dispatchEvent(event);
  return event;
}

describe("the search box", () => {
  it("is a combobox, closed at first", () => {
    expect(input.getAttribute("role")).toBe("combobox");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(list().getAttribute("role")).toBe("listbox");
    expect(list().hidden).toBe(true);
  });

  it("asks once typing pauses, keeping the list's filter", async () => {
    await type("lite");
    expect(asked).toHaveLength(1);
    expect(asked[0]?.pathname).toBe("/admin/items/suggest");
    expect(asked[0]?.searchParams.get("show")).toBe("all");
    expect(asked[0]?.searchParams.get("q")).toBe("lite");
    expect(input.getAttribute("aria-expanded")).toBe("true");
    const options = list().querySelectorAll('[role="option"]');
    expect([...options].map((option) => option.textContent)).toEqual([
      "LiteBeam 5ACRadios",
      "LiteAP",
    ]);
  });

  it("moves through them with the arrows, round through the box", async () => {
    await type("lite");
    press("ArrowDown");
    expect(input.getAttribute("aria-activedescendant")).toBe(
      list().children[0]?.id,
    );
    expect(list().children[0]?.getAttribute("aria-selected")).toBe("true");
    press("ArrowDown");
    press("ArrowDown");
    expect(input.hasAttribute("aria-activedescendant")).toBe(false);
    press("ArrowUp");
    expect(input.getAttribute("aria-activedescendant")).toBe(
      list().children[1]?.id,
    );
  });

  it("starts from the last with the up arrow", async () => {
    await type("lite");
    press("ArrowUp");
    expect(input.getAttribute("aria-activedescendant")).toBe(
      list().children[1]?.id,
    );
    press("ArrowUp");
    expect(input.getAttribute("aria-activedescendant")).toBe(
      list().children[0]?.id,
    );
  });

  it("sends the form on Enter once closed, though one was picked", async () => {
    await type("lite");
    press("ArrowDown");
    press("Escape");
    expect(press("Enter").defaultPrevented).toBe(false);
    expect(went).toEqual([]);
  });

  it("goes to the one picked with Enter", async () => {
    await type("lite");
    press("ArrowDown");
    expect(press("Enter").defaultPrevented).toBe(true);
    expect(went).toEqual(["/admin/items?pick=1"]);
  });

  it("sends the form on Enter with none picked", async () => {
    await type("lite");
    expect(press("Enter").defaultPrevented).toBe(false);
    expect(went).toEqual([]);
  });

  it("goes to one tapped", async () => {
    await type("lite");
    (list().children[1] as HTMLElement).click();
    expect(went).toEqual(["/admin/items?pick=2"]);
  });

  it("closes on Escape, and opens again with an arrow", async () => {
    await type("lite");
    expect(press("Escape").defaultPrevented).toBe(true);
    expect(list().hidden).toBe(true);
    expect(press("Escape").defaultPrevented).toBe(false);
    press("ArrowDown");
    expect(list().hidden).toBe(false);
  });

  it("closes when it loses focus", async () => {
    await type("lite");
    input.dispatchEvent(new Event("blur"));
    expect(input.getAttribute("aria-expanded")).toBe("false");
  });

  it("offers nothing for nothing typed, or nothing found", async () => {
    await type("  ");
    expect(asked).toHaveLength(0);
    answer = async () => new Response("[]");
    await type("zzz");
    expect(list().hidden).toBe(true);
    expect(press("ArrowDown").defaultPrevented).toBe(false);
  });

  it("keeps quiet when asking fails", async () => {
    answer = async () => new Response("", { status: 500 });
    await type("lite");
    expect(list().hidden).toBe(true);
    answer = async () => {
      throw new TypeError("offline");
    };
    await type("liteb");
    expect(list().hidden).toBe(true);
  });
});

describe("closing", () => {
  it("drops what typing had started", async () => {
    input.value = "lite";
    input.dispatchEvent(new Event("input"));
    input.dispatchEvent(new Event("blur"));
    await vi.runAllTimersAsync();
    expect(asked).toHaveLength(0);
    expect(list().hidden).toBe(true);
  });
});
